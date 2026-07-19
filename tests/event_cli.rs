use session_sounds::app::{run_command, AudioSink, PluginEnv};
use session_sounds::audio::{AudioBackend, Playback};
use session_sounds::config::load_config;
use session_sounds::event::PluginEvent;
use session_sounds::herdr::{
    AgentSession, Herdr, LiveSnapshot, Metadata, MetadataClear, PaneInfo, WorkspaceInfo,
};
use session_sounds::state::{assign_sound, PaneIdentity, StateStore};
use session_sounds::theme::load_theme;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Arc, Barrier, Mutex};
use std::thread;
use std::time::Duration;
use tempfile::tempdir;

#[derive(Default)]
struct FakeHerdr {
    pane: PaneInfo,
    live_status: Mutex<Option<String>>,
    snapshot: LiveSnapshot,
    metadata: Mutex<Vec<Metadata>>,
    cleared: Mutex<Vec<MetadataClear>>,
    fail_metadata: bool,
    pane_error: Option<String>,
    snapshot_error: Option<String>,
}

impl Herdr for FakeHerdr {
    fn pane_info(&self, _pane_id: &str) -> Result<PaneInfo, String> {
        if let Some(error) = &self.pane_error {
            return Err(error.clone());
        }
        let mut pane = self.pane.clone();
        if let Some(status) = self.live_status.lock().unwrap().clone() {
            pane.agent_status = status;
        }
        Ok(pane)
    }

    fn live_snapshot(&self) -> Result<LiveSnapshot, String> {
        if let Some(error) = &self.snapshot_error {
            return Err(error.clone());
        }
        Ok(self.snapshot.clone())
    }

    fn report_metadata(&self, metadata: &Metadata) -> Result<(), String> {
        if self.fail_metadata {
            return Err("injected metadata failure".into());
        }
        self.metadata.lock().unwrap().push(metadata.clone());
        Ok(())
    }

    fn clear_metadata(&self, metadata: &MetadataClear) -> Result<(), String> {
        self.cleared.lock().unwrap().push(metadata.clone());
        Ok(())
    }
}

impl FakeHerdr {
    fn set_status(&self, status: &str) {
        *self.live_status.lock().unwrap() = Some(status.into());
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum GatePoint {
    Pane,
    Snapshot,
    Report,
}

struct GatedHerdr {
    point: GatePoint,
    pane: Mutex<PaneInfo>,
    snapshot: Mutex<LiveSnapshot>,
    entered: mpsc::Sender<()>,
    release: Mutex<mpsc::Receiver<()>>,
    metadata: Mutex<Vec<Metadata>>,
    effects: Mutex<Vec<&'static str>>,
}

impl Herdr for GatedHerdr {
    fn pane_info(&self, _pane_id: &str) -> Result<PaneInfo, String> {
        let pane = self.pane.lock().unwrap().clone();
        if self.point == GatePoint::Pane {
            self.entered.send(()).unwrap();
            self.release.lock().unwrap().recv().unwrap();
        }
        Ok(pane)
    }

    fn live_snapshot(&self) -> Result<LiveSnapshot, String> {
        let snapshot = self.snapshot.lock().unwrap().clone();
        if self.point == GatePoint::Snapshot {
            self.entered.send(()).unwrap();
            self.release.lock().unwrap().recv().unwrap();
        }
        Ok(snapshot)
    }

    fn report_metadata(&self, metadata: &Metadata) -> Result<(), String> {
        if self.point == GatePoint::Report {
            self.entered.send(()).unwrap();
            self.release.lock().unwrap().recv().unwrap();
        }
        self.metadata.lock().unwrap().push(metadata.clone());
        self.effects.lock().unwrap().push("report");
        Ok(())
    }

    fn clear_metadata(&self, _metadata: &MetadataClear) -> Result<(), String> {
        self.effects.lock().unwrap().push("clear");
        Ok(())
    }
}

fn gated_herdr(
    point: GatePoint,
    pane: PaneInfo,
    snapshot: LiveSnapshot,
) -> (Arc<GatedHerdr>, mpsc::Receiver<()>, mpsc::Sender<()>) {
    let (entered_tx, entered_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    (
        Arc::new(GatedHerdr {
            point,
            pane: Mutex::new(pane),
            snapshot: Mutex::new(snapshot),
            entered: entered_tx,
            release: Mutex::new(release_rx),
            metadata: Mutex::new(Vec::new()),
            effects: Mutex::new(Vec::new()),
        }),
        entered_rx,
        release_tx,
    )
}

#[derive(Default)]
struct FakeAudio {
    played: Mutex<Vec<PathBuf>>,
}

impl AudioSink for FakeAudio {
    fn play(&self, path: &Path) -> Playback {
        self.played.lock().unwrap().push(path.into());
        Playback::Started
    }

    fn readiness(&self) -> Option<AudioBackend> {
        Some(AudioBackend::Command("fake-player"))
    }
}

fn pane() -> PaneInfo {
    PaneInfo {
        pane_id: "w1:p1".into(),
        terminal_id: "term-1".into(),
        workspace_id: "w1".into(),
        tab_id: "w1:t1".into(),
        focused: true,
        agent: Some("codex".into()),
        agent_status: "idle".into(),
        agent_session: Some(AgentSession {
            agent: "codex".into(),
            kind: "id".into(),
            source: "native".into(),
            value: "session-1".into(),
        }),
    }
}

fn fake_herdr(visible: bool) -> FakeHerdr {
    let pane = pane();
    FakeHerdr {
        pane: pane.clone(),
        snapshot: LiveSnapshot {
            panes: vec![pane],
            workspaces: vec![WorkspaceInfo {
                workspace_id: "w1".into(),
                focused: visible,
                active_tab_id: Some("w1:t1".into()),
            }],
        },
        ..FakeHerdr::default()
    }
}

fn env(root: &Path, config: &Path, state: &Path) -> PluginEnv {
    PluginEnv {
        herdr_bin_path: PathBuf::from("fake-herdr"),
        plugin_root: root.into(),
        config_dir: config.into(),
        state_dir: state.into(),
        pane_id: Some("w1:p1".into()),
        workspace_id: Some("w1".into()),
        ..PluginEnv::default()
    }
}

fn run(
    command: &str,
    env: &PluginEnv,
    herdr: &dyn Herdr,
    audio: &dyn AudioSink,
) -> (i32, String, String) {
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let code = run_command(command, env, herdr, audio, &mut stdout, &mut stderr);
    (
        code,
        String::from_utf8(stdout).unwrap(),
        String::from_utf8(stderr).unwrap(),
    )
}

#[test]
fn event_parser_accepts_official_wrapper_and_direct_fields_but_rejects_malformed_json() {
    let wrapped = PluginEvent::parse(
        Some("pane.agent_status_changed"),
        r#"{"event":"pane_agent_status_changed","data":{"pane_id":"w1:p1","agent_status":"done"}}"#,
    )
    .unwrap();
    assert_eq!(wrapped.kind, "pane_agent_status_changed");
    assert_eq!(wrapped.string("pane_id").as_deref(), Some("w1:p1"));

    let direct = PluginEvent::parse(None, r#"{"type":"pane_exited","pane_id":"w1:p1"}"#).unwrap();
    assert_eq!(direct.kind, "pane_exited");
    assert!(PluginEvent::parse(None, "{bad-json").is_err());
}

#[test]
fn detected_and_status_events_assign_reassert_metadata_and_play_completion_once() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());

    plugin_env.event = Some("pane.agent_detected".into());
    plugin_env.event_json =
        Some(r#"{"data":{"pane_id":"w1:p1","workspace_id":"w1","agent":"codex"}}"#.into());
    assert_eq!(run("event", &plugin_env, &herdr, &audio).0, 0);
    assert_eq!(
        StateStore::new(state.path())
            .read()
            .unwrap()
            .state
            .assignments
            .len(),
        1
    );

    plugin_env.event = Some("pane.agent_status_changed".into());
    herdr.set_status("working");
    plugin_env.event_json =
        Some(r#"{"data":{"pane_id":"w1:p1","workspace_id":"w1","agent_status":"working"}}"#.into());
    run("event", &plugin_env, &herdr, &audio);
    herdr.set_status("done");
    plugin_env.event_json =
        Some(r#"{"data":{"pane_id":"w1:p1","workspace_id":"w1","agent_status":"done"}}"#.into());
    run("event", &plugin_env, &herdr, &audio);
    run("event", &plugin_env, &herdr, &audio);

    assert_eq!(audio.played.lock().unwrap().len(), 1);
    let metadata = herdr.metadata.lock().unwrap();
    assert!(metadata.len() >= 3);
    assert_eq!(metadata[0].source, "session-sounds");
    assert_eq!(metadata[0].raw_agent.as_deref(), Some("codex"));
    assert_eq!(metadata[0].applies_to_source.as_deref(), Some("native"));
    assert!(metadata[0].seq > 0);
    assert_eq!(metadata[0].ttl_ms, 86_400_000);
    assert!(metadata[0].token.starts_with("sound="));
    assert!(metadata[0].display_agent.starts_with("codex · "));
    assert!(metadata.windows(2).all(|pair| pair[0].seq < pair[1].seq));
}

#[test]
fn blocked_plays_in_background_not_foreground_and_metadata_failure_is_nonfatal() {
    fn blocked_run(visible: bool, fail_metadata: bool) -> (usize, i32, String) {
        let config = tempdir().unwrap();
        let state = tempdir().unwrap();
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        let mut herdr = fake_herdr(visible);
        herdr.fail_metadata = fail_metadata;
        let audio = FakeAudio::default();
        let mut plugin_env = env(root, config.path(), state.path());
        plugin_env.event = Some("pane.agent_status_changed".into());
        herdr.set_status("working");
        plugin_env.event_json =
            Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"working"}}"#.into());
        run("event", &plugin_env, &herdr, &audio);
        herdr.set_status("blocked");
        plugin_env.event_json =
            Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"blocked"}}"#.into());
        let (code, _, stderr) = run("event", &plugin_env, &herdr, &audio);
        let count = audio.played.lock().unwrap().len();
        (count, code, stderr)
    }

    assert_eq!(blocked_run(false, false).0, 1);
    assert_eq!(blocked_run(true, false).0, 0);
    let failed = blocked_run(false, true);
    assert_eq!(failed.1, 0);
    assert!(failed.2.contains("metadata"));
}

#[test]
fn detection_is_silent_but_an_initial_background_status_change_alerts() {
    fn run_once(event: &str) -> usize {
        let config = tempdir().unwrap();
        let state = tempdir().unwrap();
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        let herdr = fake_herdr(false);
        herdr.set_status("blocked");
        let audio = FakeAudio::default();
        let mut plugin_env = env(root, config.path(), state.path());
        plugin_env.event = Some(event.into());
        plugin_env.event_json =
            Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"blocked"}}"#.into());

        assert_eq!(run("event", &plugin_env, &herdr, &audio).0, 0);
        let count = audio.played.lock().unwrap().len();
        count
    }

    assert_eq!(run_once("pane.agent_detected"), 0);
    assert_eq!(run_once("pane.agent_status_changed"), 1);
}

#[test]
fn terminal_only_identity_reports_safe_display_without_agent_guards() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut herdr = fake_herdr(false);
    herdr.pane.agent = None;
    herdr.pane.agent_session = None;
    herdr.snapshot.panes[0] = herdr.pane.clone();
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.agent_detected".into());
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1"}}"#.into());

    assert_eq!(run("event", &plugin_env, &herdr, &audio).0, 0);

    let metadata = herdr.metadata.lock().unwrap();
    assert_eq!(metadata.len(), 1);
    assert!(metadata[0].display_agent.starts_with("Agent · "));
    assert!(metadata[0].raw_agent.is_none());
    assert!(metadata[0].applies_to_source.is_none());
}

#[test]
fn visible_done_event_is_suppressed_defensively() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(true);
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.agent_status_changed".into());
    herdr.set_status("working");
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"working"}}"#.into());
    run("event", &plugin_env, &herdr, &audio);
    herdr.set_status("done");
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"done"}}"#.into());

    let result = run("event", &plugin_env, &herdr, &audio);

    assert_eq!(result.0, 0);
    assert!(audio.played.lock().unwrap().is_empty());
}

#[test]
fn reversed_event_processes_converge_on_live_done_and_play_once() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.agent_status_changed".into());
    herdr.set_status("done");
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"done"}}"#.into());
    run("event", &plugin_env, &herdr, &audio);
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"working"}}"#.into());

    run("event", &plugin_env, &herdr, &audio);
    run("event", &plugin_env, &herdr, &audio);

    let stored = StateStore::new(state.path()).read().unwrap().state;
    assert_eq!(stored.assignments[0].status.as_deref(), Some("done"));
    assert_eq!(audio.played.lock().unwrap().len(), 1);
}

#[test]
fn stale_hook_requeries_replacement_identity_after_state_ownership() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let old = pane();
    let mut replacement = old.clone();
    replacement.agent_session.as_mut().unwrap().value = "replacement".into();
    let old_snapshot = LiveSnapshot {
        panes: vec![old.clone()],
        workspaces: vec![WorkspaceInfo {
            workspace_id: "w1".into(),
            focused: false,
            active_tab_id: Some("w1:t1".into()),
        }],
    };
    let (herdr, entered, release) = gated_herdr(GatePoint::Pane, old, old_snapshot);
    let audio = Arc::new(FakeAudio::default());
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.agent_detected".into());
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1"}}"#.into());
    let state_guard = StateStore::new(state.path()).lock().unwrap();
    let event_env = plugin_env.clone();
    let event_herdr = Arc::clone(&herdr);
    let event_audio = Arc::clone(&audio);
    let worker = thread::spawn(move || {
        run(
            "event",
            &event_env,
            event_herdr.as_ref(),
            event_audio.as_ref(),
        )
    });

    let _ = entered.recv_timeout(Duration::from_millis(150));
    *herdr.pane.lock().unwrap() = replacement.clone();
    herdr.snapshot.lock().unwrap().panes[0] = replacement.clone();
    release.send(()).unwrap();
    drop(state_guard);

    assert_eq!(worker.join().unwrap().0, 0);
    let stored = StateStore::new(state.path()).read().unwrap().state;
    assert_eq!(stored.assignments.len(), 1);
    assert_eq!(
        stored.assignments[0].identity,
        replacement.identity().unwrap().key
    );
}

#[test]
fn stale_pressure_snapshot_cannot_remove_a_live_replacement() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    let old = pane();
    let mut replacement = old.clone();
    replacement.agent_session.as_mut().unwrap().value = "replacement".into();
    let mut other_panes = Vec::new();
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(stored, &replacement.identity().unwrap(), &theme.sounds, 1);
            for index in 2..=7 {
                let mut other = pane();
                other.pane_id = format!("w1:p{index}");
                other.terminal_id = format!("term-{index}");
                other.agent_session.as_mut().unwrap().value = format!("session-{index}");
                assign_sound(
                    stored,
                    &other.identity().unwrap(),
                    &theme.sounds,
                    index as u64,
                );
                other_panes.push(other);
            }
            Ok(())
        })
        .unwrap();
    let mut stale_panes = vec![old.clone()];
    stale_panes.extend(other_panes.clone());
    let stale_snapshot = LiveSnapshot {
        panes: stale_panes,
        workspaces: vec![WorkspaceInfo {
            workspace_id: "w1".into(),
            focused: false,
            active_tab_id: Some("w1:t1".into()),
        }],
    };
    let (herdr, entered, release) = gated_herdr(GatePoint::Snapshot, old, stale_snapshot);
    let audio = Arc::new(FakeAudio::default());
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.agent_detected".into());
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1"}}"#.into());
    let state_guard = StateStore::new(state.path()).lock().unwrap();
    let event_env = plugin_env.clone();
    let event_herdr = Arc::clone(&herdr);
    let event_audio = Arc::clone(&audio);
    let worker = thread::spawn(move || {
        run(
            "event",
            &event_env,
            event_herdr.as_ref(),
            event_audio.as_ref(),
        )
    });

    let _ = entered.recv_timeout(Duration::from_millis(150));
    *herdr.pane.lock().unwrap() = replacement.clone();
    let mut current_panes = vec![replacement.clone()];
    current_panes.extend(other_panes);
    herdr.snapshot.lock().unwrap().panes = current_panes;
    release.send(()).unwrap();
    drop(state_guard);

    assert_eq!(worker.join().unwrap().0, 0);
    let stored = StateStore::new(state.path()).read().unwrap().state;
    assert_eq!(stored.assignments.len(), theme.sounds.len());
    assert!(stored
        .assignments
        .iter()
        .any(|assignment| assignment.identity == replacement.identity().unwrap().key));
    assert!(!stored
        .assignments
        .iter()
        .any(|assignment| assignment.identity == pane().identity().unwrap().key));
}

#[test]
fn active_tab_unfocused_split_is_visible_for_blocked_suppression() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut herdr = fake_herdr(true);
    herdr.pane.focused = false;
    herdr.snapshot.panes[0].focused = false;
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.agent_status_changed".into());
    herdr.set_status("working");
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"working"}}"#.into());
    run("event", &plugin_env, &herdr, &audio);
    herdr.set_status("blocked");
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1","agent_status":"blocked"}}"#.into());

    run("event", &plugin_env, &herdr, &audio);

    assert!(audio.played.lock().unwrap().is_empty());
}

#[test]
fn malformed_event_is_a_successful_noop_and_cleanup_is_idempotent() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.closed".into());
    plugin_env.event_json = Some("{bad-json".into());

    let result = run("event", &plugin_env, &herdr, &audio);

    assert_eq!(result.0, 0);
    assert!(result.2.contains("warning"));
    assert!(!state.path().join("state.json").exists());
}

#[test]
fn delayed_cleanup_reconciles_a_live_replacement_instead_of_deleting_it() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    let old_identity = herdr.pane.identity().unwrap();
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(stored, &old_identity, &theme.sounds, 1);
            Ok(())
        })
        .unwrap();
    herdr.pane.agent_session.as_mut().unwrap().value = "replacement".into();
    herdr.snapshot.panes[0] = herdr.pane.clone();
    let replacement = herdr.pane.identity().unwrap().key;
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.closed".into());
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1"}}"#.into());

    assert_eq!(run("event", &plugin_env, &herdr, &audio).0, 0);

    let stored = StateStore::new(state.path()).read().unwrap().state;
    assert_eq!(stored.assignments.len(), 1);
    assert_eq!(stored.assignments[0].identity, replacement);
}

#[test]
fn cleanup_retains_on_query_failure_and_deletes_only_after_confirmed_absence() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(stored, &pane().identity().unwrap(), &theme.sounds, 1);
            Ok(())
        })
        .unwrap();
    let mut unavailable = fake_herdr(false);
    unavailable.pane_error = Some("transport unavailable".into());
    unavailable.snapshot_error = Some("transport unavailable".into());
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.closed".into());
    plugin_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1"}}"#.into());

    assert_eq!(run("event", &plugin_env, &unavailable, &audio).0, 0);
    assert_eq!(
        StateStore::new(state.path())
            .read()
            .unwrap()
            .state
            .assignments
            .len(),
        1
    );

    let mut confirmed_gone = fake_herdr(false);
    confirmed_gone.pane_error = Some("pane not found".into());
    confirmed_gone.snapshot.panes.clear();
    assert_eq!(run("event", &plugin_env, &confirmed_gone, &audio).0, 0);
    assert!(StateStore::new(state.path())
        .read()
        .unwrap()
        .state
        .assignments
        .is_empty());
}

#[test]
fn move_event_emits_config_and_theme_fallback_warnings() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    fs::write(
        config.path().join("config.toml"),
        "enabled = true\ntheme = \"missing-personal-theme\"\n",
    )
    .unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.moved".into());
    plugin_env.event_json = Some(
        r#"{"data":{"pane_id":"w1:p1","previous_pane_id":"w1:p0","workspace_id":"w1"}}"#.into(),
    );

    let result = run("event", &plugin_env, &herdr, &audio);

    assert_eq!(result.0, 0);
    assert!(result.2.contains("invalid personal theme"));
    assert!(result.2.contains("using bundled default"));
}

#[test]
fn move_event_removes_a_stale_previous_owner_when_current_identity_exists_elsewhere() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    let mut current_elsewhere = herdr.pane.identity().unwrap();
    current_elsewhere.pane_id = "w1:p9".into();
    let mut stale_previous = current_elsewhere.clone();
    stale_previous.key = session_sounds::state::IdentityKey::AgentSession {
        agent: "codex".into(),
        session_kind: "id".into(),
        session_value: "stale".into(),
    };
    stale_previous.pane_id = "w1:p0".into();
    stale_previous.terminal_id = Some("term-stale".into());
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(stored, &current_elsewhere, &theme.sounds, 1);
            assign_sound(stored, &stale_previous, &theme.sounds, 2);
            Ok(())
        })
        .unwrap();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.event = Some("pane.moved".into());
    plugin_env.event_json = Some(
        r#"{"data":{"pane_id":"w1:p1","previous_pane_id":"w1:p0","workspace_id":"w1"}}"#.into(),
    );

    assert_eq!(run("event", &plugin_env, &herdr, &audio).0, 0);

    let stored = StateStore::new(state.path()).read().unwrap().state;
    assert_eq!(stored.assignments.len(), 1);
    assert_eq!(
        stored.assignments[0].identity,
        herdr.pane.identity().unwrap().key
    );
    assert_eq!(stored.assignments[0].pane_id, "w1:p1");
}

#[test]
fn action_emits_invalid_config_warning_and_uses_safe_defaults() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    fs::write(
        config.path().join("config.toml"),
        "enabled = \"invalid\"\ntheme = 42\n",
    )
    .unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let plugin_env = env(root, config.path(), state.path());

    let result = run("test-sound", &plugin_env, &herdr, &audio);

    assert_eq!(result.0, 0);
    assert_eq!(audio.played.lock().unwrap().len(), 1);
    assert!(result.2.contains("config `enabled`"));
    assert!(result.2.contains("config `theme`"));
}

#[test]
fn mutating_action_rejects_missing_required_herdr_paths_before_writing() {
    let config = tempdir().unwrap();
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let plugin_env = PluginEnv {
        config_dir: config.path().into(),
        ..PluginEnv::default()
    };

    let result = run("toggle-mute", &plugin_env, &herdr, &audio);

    assert_eq!(result.0, 1);
    assert!(result.2.contains("required Herdr plugin environment"));
    assert!(!config.path().join("config.toml").exists());
}

#[test]
fn toggle_mute_persists_enabled_and_clears_known_metadata() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let plugin_env = env(root, config.path(), state.path());
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(
                stored,
                &PaneIdentity {
                    key: pane().identity().unwrap().key,
                    agent: "codex".into(),
                    agent_source: Some("native".into()),
                    terminal_id: Some("term-1".into()),
                    pane_id: "w1:p1".into(),
                    workspace_id: Some("w1".into()),
                },
                &theme.sounds,
                1,
            );
            Ok(())
        })
        .unwrap();

    let result = run("toggle-mute", &plugin_env, &herdr, &audio);

    assert_eq!(result.0, 0);
    assert!(!load_config(config.path()).config.enabled);
    let cleared = herdr.cleared.lock().unwrap();
    assert_eq!(cleared.len(), 1);
    assert_eq!(cleared[0].pane_id, "w1:p1");
    assert_eq!(cleared[0].raw_agent.as_deref(), Some("codex"));
}

#[test]
fn mute_waits_for_an_older_event_and_is_the_last_metadata_effect() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let current = pane();
    let snapshot = LiveSnapshot {
        panes: vec![current.clone()],
        workspaces: vec![WorkspaceInfo {
            workspace_id: "w1".into(),
            focused: false,
            active_tab_id: Some("w1:t1".into()),
        }],
    };
    let (herdr, report_entered, release_report) = gated_herdr(GatePoint::Report, current, snapshot);
    let audio = Arc::new(FakeAudio::default());
    let mut event_env = env(root, config.path(), state.path());
    event_env.event = Some("pane.agent_detected".into());
    event_env.event_json = Some(r#"{"data":{"pane_id":"w1:p1"}}"#.into());
    let event_herdr = Arc::clone(&herdr);
    let event_audio = Arc::clone(&audio);
    let event_worker = thread::spawn(move || {
        run(
            "event",
            &event_env,
            event_herdr.as_ref(),
            event_audio.as_ref(),
        )
    });
    report_entered
        .recv_timeout(Duration::from_secs(2))
        .expect("event reached metadata report");
    let mute_env = env(root, config.path(), state.path());
    let mute_herdr = Arc::clone(&herdr);
    let mute_audio = Arc::clone(&audio);
    let (mute_done_tx, mute_done_rx) = mpsc::channel();
    let mute_worker = thread::spawn(move || {
        let result = run(
            "toggle-mute",
            &mute_env,
            mute_herdr.as_ref(),
            mute_audio.as_ref(),
        );
        mute_done_tx.send(()).unwrap();
        result
    });

    assert!(mute_done_rx
        .recv_timeout(Duration::from_millis(150))
        .is_err());
    release_report.send(()).unwrap();

    assert_eq!(event_worker.join().unwrap().0, 0);
    assert_eq!(mute_worker.join().unwrap().0, 0);
    assert!(!load_config(config.path()).config.enabled);
    assert_eq!(
        herdr.effects.lock().unwrap().as_slice(),
        &["report", "clear"]
    );
}

#[test]
fn simultaneous_toggle_actions_apply_both_flips() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = Arc::new(fake_herdr(false));
    let audio = Arc::new(FakeAudio::default());
    let plugin_env = env(root, config.path(), state.path());
    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for _ in 0..2 {
        let herdr = Arc::clone(&herdr);
        let audio = Arc::clone(&audio);
        let plugin_env = plugin_env.clone();
        let barrier = Arc::clone(&barrier);
        workers.push(thread::spawn(move || {
            barrier.wait();
            run("toggle-mute", &plugin_env, herdr.as_ref(), audio.as_ref())
        }));
    }
    barrier.wait();
    let mut outputs: Vec<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap().1)
        .collect();
    outputs.sort();

    assert_eq!(outputs, vec!["muted\n", "unmuted\n"]);
    assert!(load_config(config.path()).config.enabled);
}

#[test]
fn toggle_mute_clears_a_moved_identity_at_its_live_address() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut herdr = fake_herdr(false);
    herdr.snapshot.panes[0].pane_id = "w2:p9".into();
    herdr.snapshot.panes[0].workspace_id = "w2".into();
    let audio = FakeAudio::default();
    let plugin_env = env(root, config.path(), state.path());
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(stored, &pane().identity().unwrap(), &theme.sounds, 1);
            Ok(())
        })
        .unwrap();

    run("toggle-mute", &plugin_env, &herdr, &audio);

    let cleared = herdr.cleared.lock().unwrap();
    assert_eq!(cleared.len(), 1);
    assert_eq!(cleared[0].pane_id, "w2:p9");
    assert_eq!(cleared[0].raw_agent.as_deref(), Some("codex"));
}

#[test]
fn toggle_mute_does_not_clear_reused_address_owned_by_another_identity() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut herdr = fake_herdr(false);
    herdr.snapshot.panes[0]
        .agent_session
        .as_mut()
        .unwrap()
        .value = "replacement-session".into();
    let audio = FakeAudio::default();
    let plugin_env = env(root, config.path(), state.path());
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(stored, &pane().identity().unwrap(), &theme.sounds, 1);
            Ok(())
        })
        .unwrap();

    run("toggle-mute", &plugin_env, &herdr, &audio);

    assert!(herdr.cleared.lock().unwrap().is_empty());
}

#[test]
fn reshuffle_changes_context_assignment_while_test_sound_leaves_state_unchanged() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let plugin_env = env(root, config.path(), state.path());
    let theme = load_theme(root, config.path(), "default").unwrap().theme;
    StateStore::new(state.path())
        .transaction(|stored| {
            assign_sound(stored, &pane().identity().unwrap(), &theme.sounds, 1);
            Ok(())
        })
        .unwrap();
    let before = StateStore::new(state.path()).read().unwrap().state;

    assert_eq!(run("reshuffle", &plugin_env, &herdr, &audio).0, 0);
    let shuffled = StateStore::new(state.path()).read().unwrap().state;
    assert_ne!(
        before.assignments[0].sound_id,
        shuffled.assignments[0].sound_id
    );
    let state_bytes = fs::read(state.path().join("state.json")).unwrap();
    assert_eq!(run("test-sound", &plugin_env, &herdr, &audio).0, 0);
    assert_eq!(
        fs::read(state.path().join("state.json")).unwrap(),
        state_bytes
    );
    assert_eq!(audio.played.lock().unwrap().len(), 1);
}

#[test]
fn doctor_is_read_only_and_prints_exact_native_sound_guidance() {
    let config = tempdir().unwrap();
    let state = tempdir().unwrap();
    let herdr_config_dir = tempdir().unwrap();
    let herdr_config = herdr_config_dir.path().join("config.toml");
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let herdr = fake_herdr(false);
    let audio = FakeAudio::default();
    let mut plugin_env = env(root, config.path(), state.path());
    plugin_env.herdr_config_path = Some(herdr_config.clone());

    let result = run("doctor", &plugin_env, &herdr, &audio);

    assert_eq!(result.0, 0);
    assert!(result.1.contains("session-sounds 1.0.0"));
    let expected = format!(
        "Herdr's built-in background sound is enabled and will double-play. Add this to {}:\n[ui.sound]\nenabled = false\nThen run: herdr server reload-config",
        herdr_config.display()
    );
    assert!(result.1.contains(&expected));
    assert!(!config.path().join("config.toml").exists());
    assert!(!state.path().join("state.json").exists());
    assert!(!herdr_config.exists());
}
