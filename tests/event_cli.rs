use session_sounds::app::{run_command, AudioSink, PluginEnv};
use session_sounds::audio::{AudioBackend, Playback};
use session_sounds::config::load_config;
use session_sounds::event::PluginEvent;
use session_sounds::herdr::{AgentSession, Herdr, LiveSnapshot, Metadata, PaneInfo, WorkspaceInfo};
use session_sounds::state::{assign_sound, PaneIdentity, StateStore};
use session_sounds::theme::load_theme;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tempfile::tempdir;

#[derive(Default)]
struct FakeHerdr {
    pane: PaneInfo,
    live_status: Mutex<Option<String>>,
    snapshot: LiveSnapshot,
    metadata: Mutex<Vec<Metadata>>,
    cleared: Mutex<Vec<(String, String)>>,
    fail_metadata: bool,
}

impl Herdr for FakeHerdr {
    fn pane_info(&self, _pane_id: &str) -> Result<PaneInfo, String> {
        let mut pane = self.pane.clone();
        if let Some(status) = self.live_status.lock().unwrap().clone() {
            pane.agent_status = status;
        }
        Ok(pane)
    }

    fn live_snapshot(&self) -> Result<LiveSnapshot, String> {
        Ok(self.snapshot.clone())
    }

    fn report_metadata(&self, metadata: &Metadata) -> Result<(), String> {
        if self.fail_metadata {
            return Err("injected metadata failure".into());
        }
        self.metadata.lock().unwrap().push(metadata.clone());
        Ok(())
    }

    fn clear_metadata(&self, pane_id: &str, raw_agent: &str) -> Result<(), String> {
        self.cleared
            .lock()
            .unwrap()
            .push((pane_id.into(), raw_agent.into()));
        Ok(())
    }
}

impl FakeHerdr {
    fn set_status(&self, status: &str) {
        *self.live_status.lock().unwrap() = Some(status.into());
    }
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
    assert_eq!(metadata[0].raw_agent, "codex");
    assert_eq!(metadata[0].ttl_ms, 86_400_000);
    assert!(metadata[0].token.starts_with("sound="));
    assert!(metadata[0].display_agent.starts_with("codex · "));
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
    assert_eq!(
        herdr.cleared.lock().unwrap().as_slice(),
        &[("w1:p1".into(), "codex".into())]
    );
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

    assert_eq!(
        herdr.cleared.lock().unwrap().as_slice(),
        &[("w2:p9".into(), "codex".into())]
    );
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
