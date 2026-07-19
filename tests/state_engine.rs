use session_sounds::state::{
    apply_detection_observation, apply_status, apply_status_observation, assign_sound,
    cleanup_pane, move_pane, next_metadata_seq, reconcile_under_pressure, reshuffle_pane,
    IdentityKey, LivePane, PaneIdentity, State, StateStore,
};
use session_sounds::theme::Sound;
use std::collections::HashSet;
use std::io;
use std::path::PathBuf;
use std::sync::{Arc, Barrier};
use std::thread;
use tempfile::tempdir;

fn sounds(count: usize) -> Vec<Sound> {
    (0..count)
        .map(|index| Sound {
            id: format!("sound-{index}"),
            display_name: format!("Sound {index}"),
            path: PathBuf::from(format!("sound-{index}.wav")),
        })
        .collect()
}

fn pane(number: usize) -> PaneIdentity {
    PaneIdentity {
        key: IdentityKey::AgentSession {
            agent: "codex".into(),
            session_kind: "id".into(),
            session_value: format!("session-{number}"),
        },
        agent: "codex".into(),
        agent_source: Some("native".into()),
        terminal_id: Some(format!("term-{number}")),
        pane_id: format!("w1:p{number}"),
        workspace_id: Some("w1".into()),
    }
}

#[test]
fn durable_key_prefers_complete_agent_session_then_falls_back_to_terminal() {
    assert_eq!(
        IdentityKey::from_parts(Some("codex"), Some("id"), Some("abc"), Some("term-1")),
        Some(IdentityKey::AgentSession {
            agent: "codex".into(),
            session_kind: "id".into(),
            session_value: "abc".into(),
        })
    );
    assert_eq!(
        IdentityKey::from_parts(Some("codex"), None, None, Some("term-1")),
        Some(IdentityKey::Terminal {
            terminal_id: "term-1".into()
        })
    );
    assert_eq!(IdentityKey::from_parts(None, None, None, None), None);
}

#[test]
fn replacement_in_same_pane_or_terminal_evicts_old_identity() {
    let pool = sounds(2);
    let mut state = State::default();
    let first = pane(1);
    assign_sound(&mut state, &first, &pool, 10);
    let mut replacement = pane(2);
    replacement.pane_id = first.pane_id.clone();
    replacement.terminal_id = first.terminal_id.clone();

    assign_sound(&mut state, &replacement, &pool, 20);

    assert_eq!(state.assignments.len(), 1);
    assert_eq!(state.assignments[0].identity, replacement.key);
}

#[test]
fn different_agent_on_same_terminal_is_a_fresh_assignment() {
    let pool = sounds(2);
    let mut state = State::default();
    let mut first = pane(1);
    first.key = IdentityKey::Terminal {
        terminal_id: "term-shared".into(),
    };
    first.terminal_id = Some("term-shared".into());
    let assignment = assign_sound(&mut state, &first, &pool, 10);
    assignment.status = Some("done".into());
    assignment.last_played_at_ms = Some(10);
    assignment.completion_handled = true;

    let mut replacement = first.clone();
    replacement.agent = "claude".into();
    let replacement = assign_sound(&mut state, &replacement, &pool, 20);

    assert_eq!(replacement.agent, "claude");
    assert_eq!(replacement.status, None);
    assert_eq!(replacement.last_played_at_ms, None);
    assert!(!replacement.completion_handled);
    assert_eq!(replacement.assigned_at_ms, 20);
}

#[test]
fn newly_authoritative_agent_on_terminal_fallback_is_a_fresh_assignment() {
    let pool = sounds(2);
    let mut state = State::default();
    let mut unknown = pane(1);
    unknown.key = IdentityKey::Terminal {
        terminal_id: "term-shared".into(),
    };
    unknown.terminal_id = Some("term-shared".into());
    unknown.agent.clear();
    let assignment = assign_sound(&mut state, &unknown, &pool, 10);
    assignment.status = Some("done".into());
    assignment.last_played_at_ms = Some(10);
    assignment.completion_handled = true;

    let mut identified = unknown;
    identified.agent = "codex".into();
    let assignment = assign_sound(&mut state, &identified, &pool, 20);

    assert_eq!(assignment.agent, "codex");
    assert_eq!(assignment.status, None);
    assert_eq!(assignment.last_played_at_ms, None);
    assert!(!assignment.completion_handled);
    assert_eq!(assignment.assigned_at_ms, 20);
}

#[test]
fn existing_identity_moving_onto_an_occupied_address_evicts_the_occupant() {
    let pool = sounds(3);
    let mut state = State::default();
    let first = pane(1);
    let occupied = pane(2);
    assign_sound(&mut state, &first, &pool, 10);
    assign_sound(&mut state, &occupied, &pool, 20);
    let mut moved_first = first.clone();
    moved_first.pane_id = occupied.pane_id.clone();
    moved_first.terminal_id = occupied.terminal_id.clone();

    assign_sound(&mut state, &moved_first, &pool, 30);

    assert_eq!(state.assignments.len(), 1);
    assert_eq!(state.assignments[0].identity, first.key);
    assert_eq!(state.assignments[0].pane_id, occupied.pane_id);
}

#[test]
fn existing_identity_is_reassigned_when_active_theme_no_longer_has_its_sound() {
    let old_pool = sounds(2);
    let new_pool = vec![Sound {
        id: "new-sound".into(),
        display_name: "New Sound".into(),
        path: PathBuf::from("new-sound.wav"),
    }];
    let mut state = State::default();
    let identity = pane(1);
    assign_sound(&mut state, &identity, &old_pool, 10);

    let assignment = assign_sound(&mut state, &identity, &new_pool, 20);

    assert_eq!(assignment.sound_id, "new-sound");
    assert_eq!(assignment.assigned_at_ms, 20);
}

#[test]
fn first_pool_size_assignments_are_unique_then_least_recent_sound_is_reused() {
    let pool = sounds(3);
    let mut state = State::default();
    for index in 0..3 {
        assign_sound(&mut state, &pane(index), &pool, (index + 1) as u64 * 10);
    }

    let unique: HashSet<_> = state
        .assignments
        .iter()
        .map(|assignment| assignment.sound_id.as_str())
        .collect();
    assert_eq!(unique.len(), 3);

    let overflow = assign_sound(&mut state, &pane(4), &pool, 40);
    assert_eq!(overflow.sound_id, "sound-0");
    let next = assign_sound(&mut state, &pane(5), &pool, 50);
    assert_eq!(next.sound_id, "sound-1");
    assert_eq!(state.assignments.len(), 5);
}

#[test]
fn reshuffle_changes_only_context_pane_and_avoids_current_sound() {
    let pool = sounds(3);
    let mut state = State::default();
    assign_sound(&mut state, &pane(1), &pool, 10);
    assign_sound(&mut state, &pane(2), &pool, 20);
    let before_other = state.assignments[1].clone();
    let old = state.assignments[0].sound_id.clone();

    let new = reshuffle_pane(&mut state, "w1:p1", &pool, 30).unwrap();

    assert_ne!(new, old);
    assert_eq!(new, "sound-2");
    assert_eq!(state.assignments[1], before_other);
}

#[test]
fn cleanup_is_idempotent_and_move_updates_only_the_current_address() {
    let pool = sounds(2);
    let mut state = State::default();
    assign_sound(&mut state, &pane(1), &pool, 10);

    assert!(move_pane(
        &mut state,
        "w1:p1",
        "w2:p8",
        Some("w2"),
        Some("term-8"),
    ));
    assert_eq!(state.assignments[0].pane_id, "w2:p8");
    assert_eq!(state.assignments[0].workspace_id.as_deref(), Some("w2"));
    assert!(!cleanup_pane(&mut state, "w1:p1"));
    assert!(cleanup_pane(&mut state, "w2:p8"));
    assert!(!cleanup_pane(&mut state, "w2:p8"));
}

#[test]
fn move_evicts_a_different_assignment_at_the_destination_address() {
    let pool = sounds(3);
    let mut state = State::default();
    assign_sound(&mut state, &pane(1), &pool, 10);
    assign_sound(&mut state, &pane(2), &pool, 20);

    assert!(move_pane(
        &mut state,
        "w1:p1",
        "w1:p2",
        Some("w1"),
        Some("term-2"),
    ));

    assert_eq!(state.assignments.len(), 1);
    assert_eq!(state.assignments[0].identity, pane(1).key);
    assert_eq!(state.assignments[0].pane_id, "w1:p2");
}

#[test]
fn pressure_reconciliation_removes_only_identities_absent_from_live_snapshot() {
    let pool = sounds(2);
    let mut state = State::default();
    for index in 0..3 {
        assign_sound(&mut state, &pane(index), &pool, index as u64);
    }
    state.assignments[0].status = Some("idle".into());
    let live = vec![
        LivePane {
            identity: pane(0).key,
            pane_id: "w1:p0".into(),
        },
        LivePane {
            identity: pane(2).key,
            pane_id: "w1:p2".into(),
        },
    ];

    assert_eq!(reconcile_under_pressure(&mut state, &live, pool.len()), 1);
    assert_eq!(state.assignments.len(), 2);
    assert!(state
        .assignments
        .iter()
        .any(|assignment| assignment.status.as_deref() == Some("idle")));
}

#[test]
fn corrupt_state_reconstructs_and_atomic_round_trip_preserves_data() {
    let dir = tempdir().unwrap();
    let store = StateStore::new(dir.path());
    std::fs::create_dir_all(dir.path()).unwrap();
    std::fs::write(dir.path().join("state.json"), b"{not-json").unwrap();

    let corrupt = store.read().unwrap();
    assert!(corrupt.warning.is_some());
    assert_eq!(corrupt.state, State::default());

    store
        .transaction(|state| {
            assign_sound(state, &pane(1), &sounds(2), 10);
            Ok(())
        })
        .unwrap();
    let loaded = store.read().unwrap();
    assert!(loaded.warning.is_none());
    assert_eq!(loaded.state.assignments.len(), 1);
    assert_eq!(loaded.state.version, 1);
}

#[test]
fn metadata_sequences_are_monotonic_per_terminal_and_default_in_v1_state() {
    let legacy: State = serde_json::from_str(r#"{"version":1,"assignments":[]}"#).unwrap();
    assert!(legacy.metadata_sequences.is_empty());
    let mut state = legacy;

    assert_eq!(
        next_metadata_seq(&mut state, "term-1", "session-sounds", 100),
        100
    );
    assert_eq!(
        next_metadata_seq(&mut state, "term-1", "session-sounds", 100),
        101
    );
    assert_eq!(
        next_metadata_seq(&mut state, "term-2", "session-sounds", 50),
        50
    );
    assert_eq!(next_metadata_seq(&mut state, "term-1", "other", 7), 7);
}

#[test]
fn failed_transaction_releases_lock_and_does_not_persist_partial_state() {
    let dir = tempdir().unwrap();
    let store = StateStore::new(dir.path());
    let result: io::Result<()> = store.transaction(|state| {
        assign_sound(state, &pane(1), &sounds(2), 10);
        Err(io::Error::other("injected failure"))
    });
    assert!(result.is_err());

    store
        .transaction(|state| {
            assign_sound(state, &pane(2), &sounds(2), 20);
            Ok(())
        })
        .unwrap();

    let loaded = store.read().unwrap();
    assert_eq!(loaded.state.assignments.len(), 1);
    assert_eq!(loaded.state.assignments[0].identity, pane(2).key);
}

#[test]
fn concurrent_transactions_keep_valid_state_and_unique_sounds_before_exhaustion() {
    let dir = tempdir().unwrap();
    let store = Arc::new(StateStore::new(dir.path()));
    let barrier = Arc::new(Barrier::new(8));
    let mut threads = Vec::new();
    for index in 0..7 {
        let store = Arc::clone(&store);
        let barrier = Arc::clone(&barrier);
        threads.push(thread::spawn(move || {
            barrier.wait();
            store
                .transaction(|state| {
                    assign_sound(state, &pane(index), &sounds(7), index as u64);
                    Ok(())
                })
                .unwrap();
        }));
    }
    barrier.wait();
    for worker in threads {
        worker.join().unwrap();
    }

    let loaded = store.read().unwrap();
    let unique: HashSet<_> = loaded
        .state
        .assignments
        .iter()
        .map(|assignment| assignment.sound_id.as_str())
        .collect();
    assert_eq!(loaded.state.assignments.len(), 7);
    assert_eq!(unique.len(), 7);
}

#[test]
fn status_rules_cover_completion_visibility_repeats_and_debounce() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(!apply_status(assignment, "idle", false, 0));
    assert!(!apply_status(assignment, "working", false, 10));
    assert!(apply_status(assignment, "done", false, 20));
    assert!(!apply_status(assignment, "done", false, 30));
    assert!(!apply_status(assignment, "working", false, 40));
    assert!(!apply_status(assignment, "done", false, 1_000));
    assert!(!apply_status(assignment, "working", false, 1_500));
    assert!(apply_status(assignment, "done", false, 1_521));
}

#[test]
fn blocked_plays_only_on_transition_while_target_is_background() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(!apply_status(assignment, "blocked", false, 10));
    assert!(!apply_status(assignment, "working", false, 20));
    assert!(!apply_status(assignment, "blocked", true, 30));
    assert!(!apply_status(assignment, "blocked", false, 40));
    assert!(!apply_status(assignment, "working", false, 50));
    assert!(apply_status(assignment, "blocked", false, 2_000));
    assert!(!apply_status(assignment, "unknown", false, 4_000));
}

#[test]
fn initial_authoritative_status_change_to_final_state_alerts_once() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(apply_status_observation(
        assignment,
        Some("blocked"),
        Some("blocked"),
        false,
        10,
    ));
    assert!(!apply_status_observation(
        assignment,
        Some("blocked"),
        Some("blocked"),
        false,
        2_000,
    ));
}

#[test]
fn detection_never_alerts_or_consumes_the_following_status_change() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);
    assert!(!apply_status(assignment, "working", false, 1));

    assert!(!apply_detection_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        2_000,
    ));
    assert_eq!(assignment.last_played_at_ms, None);
    assert!(apply_status_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        2_001,
    ));
    assert!(!apply_detection_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        3_000,
    ));
    assert!(!apply_status_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        4_000,
    ));
}

#[test]
fn rapid_second_cycle_is_not_permanently_hidden_by_handled_flags() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(apply_status_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        10,
    ));
    assert!(!apply_status_observation(
        assignment,
        Some("working"),
        Some("done"),
        false,
        20,
    ));
    assert!(!apply_status_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        30,
    ));
    assert!(apply_status_observation(
        assignment,
        Some("working"),
        Some("done"),
        false,
        2_000,
    ));
    assert!(!apply_status_observation(
        assignment,
        Some("working"),
        Some("done"),
        false,
        4_000,
    ));
}

#[test]
fn repeated_working_event_can_confirm_a_later_done_cycle_after_stale_reordering() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);
    assignment.status = Some("done".into());
    assignment.completion_handled = true;
    assignment.last_event_status = Some("working".into());
    assignment.last_played_at_ms = Some(10);

    assert!(!apply_status_observation(
        assignment,
        Some("working"),
        Some("done"),
        false,
        2_000,
    ));
    assert!(apply_status_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        2_001,
    ));
    assert!(!apply_status_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        4_000,
    ));
}

#[test]
fn repeated_working_event_can_confirm_a_later_blocked_cycle_after_stale_reordering() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);
    assignment.status = Some("blocked".into());
    assignment.blocked_handled = true;
    assignment.last_event_status = Some("working".into());
    assignment.last_played_at_ms = Some(10);

    assert!(!apply_status_observation(
        assignment,
        Some("working"),
        Some("blocked"),
        false,
        2_000,
    ));
    assert!(apply_status_observation(
        assignment,
        Some("blocked"),
        Some("blocked"),
        false,
        2_001,
    ));
    assert!(!apply_status_observation(
        assignment,
        Some("blocked"),
        Some("blocked"),
        false,
        4_000,
    ));
}

#[test]
fn foreground_completion_to_idle_does_not_play() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(!apply_status(assignment, "working", true, 1));
    assert!(!apply_status(assignment, "idle", true, 2));
}

#[test]
fn foreground_completion_to_done_is_suppressed_defensively() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(!apply_status(assignment, "working", true, 1));
    assert!(!apply_status(assignment, "done", true, 2));
}

#[test]
fn reversed_status_handlers_converge_on_live_done_and_notify_once() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(apply_status_observation(
        assignment,
        Some("done"),
        Some("done"),
        false,
        10,
    ));
    assert_eq!(assignment.status.as_deref(), Some("done"));
    assert!(!apply_status_observation(
        assignment,
        Some("working"),
        Some("done"),
        false,
        20,
    ));
    assert_eq!(assignment.status.as_deref(), Some("done"));
    assert!(!apply_status_observation(
        assignment,
        Some("working"),
        Some("done"),
        false,
        2_000,
    ));
    assert_eq!(assignment.status.as_deref(), Some("done"));
}

#[test]
fn stale_working_handler_observing_live_blocked_notifies_and_converges() {
    let pool = sounds(1);
    let mut state = State::default();
    let assignment = assign_sound(&mut state, &pane(1), &pool, 0);

    assert!(apply_status_observation(
        assignment,
        Some("working"),
        Some("blocked"),
        false,
        10,
    ));
    assert_eq!(assignment.status.as_deref(), Some("blocked"));
    assert!(!apply_status_observation(
        assignment,
        Some("blocked"),
        Some("blocked"),
        false,
        2_000,
    ));
}
