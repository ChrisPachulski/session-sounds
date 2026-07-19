use crate::audio::{self, AudioBackend, Platform, Playback};
use crate::config::{load_config, ConfigGuard};
use crate::event::PluginEvent;
use crate::herdr::{Herdr, Metadata, MetadataClear};
use crate::state::{
    apply_detection_observation, apply_status_observation, assign_sound, cleanup_pane,
    next_metadata_seq, reconcile_under_pressure, reshuffle_pane, Assignment, StateStore,
};
use crate::theme::{load_theme, Sound, Theme};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub trait AudioSink {
    fn play(&self, path: &Path) -> Playback;
    fn readiness(&self) -> Option<AudioBackend>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemAudio;

impl AudioSink for SystemAudio {
    fn play(&self, path: &Path) -> Playback {
        audio::play(path)
    }

    fn readiness(&self) -> Option<AudioBackend> {
        audio::select_backend(Platform::current(), audio::command_available)
    }
}

#[derive(Clone, Debug, Default)]
pub struct PluginEnv {
    pub herdr_bin_path: PathBuf,
    pub plugin_root: PathBuf,
    pub config_dir: PathBuf,
    pub state_dir: PathBuf,
    pub event: Option<String>,
    pub event_json: Option<String>,
    pub context_json: Option<String>,
    pub pane_id: Option<String>,
    pub workspace_id: Option<String>,
    pub terminal_id: Option<String>,
    pub herdr_config_path: Option<PathBuf>,
    pub xdg_config_home: Option<PathBuf>,
    pub home: Option<PathBuf>,
    pub appdata: Option<PathBuf>,
}

impl PluginEnv {
    pub fn from_current() -> Self {
        Self {
            herdr_bin_path: path_env("HERDR_BIN_PATH").unwrap_or_default(),
            plugin_root: path_env("HERDR_PLUGIN_ROOT").unwrap_or_default(),
            config_dir: path_env("HERDR_PLUGIN_CONFIG_DIR").unwrap_or_default(),
            state_dir: path_env("HERDR_PLUGIN_STATE_DIR").unwrap_or_default(),
            event: std::env::var("HERDR_PLUGIN_EVENT").ok(),
            event_json: std::env::var("HERDR_PLUGIN_EVENT_JSON").ok(),
            context_json: std::env::var("HERDR_PLUGIN_CONTEXT_JSON").ok(),
            pane_id: std::env::var("HERDR_PANE_ID").ok(),
            workspace_id: std::env::var("HERDR_WORKSPACE_ID").ok(),
            terminal_id: std::env::var("HERDR_TERMINAL_ID").ok(),
            herdr_config_path: path_env("HERDR_CONFIG_PATH"),
            xdg_config_home: path_env("XDG_CONFIG_HOME"),
            home: path_env("HOME"),
            appdata: path_env("APPDATA"),
        }
    }
}

fn path_env(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

pub fn run_command(
    command: &str,
    env: &PluginEnv,
    herdr: &dyn Herdr,
    audio: &dyn AudioSink,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> i32 {
    if matches!(
        command,
        "event" | "toggle-mute" | "reshuffle" | "test-sound"
    ) && !required_paths_present(env)
    {
        let _ = writeln!(
            stderr,
            "error: required Herdr plugin environment is missing"
        );
        return 1;
    }
    let result = match command {
        "event" => event(env, herdr, audio, stderr),
        "toggle-mute" => toggle_mute(env, herdr, stdout, stderr),
        "reshuffle" => reshuffle(env, herdr, stdout, stderr),
        "test-sound" => test_sound(env, audio, stdout, stderr),
        "doctor" => doctor(env, audio, stdout),
        _ => {
            let _ = writeln!(
                stderr,
                "usage: session-sounds <event|toggle-mute|reshuffle|test-sound|doctor>"
            );
            return 2;
        }
    };
    match result {
        Ok(()) => 0,
        Err(error) => {
            let _ = writeln!(stderr, "error: {error}");
            1
        }
    }
}

fn required_paths_present(env: &PluginEnv) -> bool {
    !env.herdr_bin_path.as_os_str().is_empty()
        && !env.plugin_root.as_os_str().is_empty()
        && !env.config_dir.as_os_str().is_empty()
        && !env.state_dir.as_os_str().is_empty()
}

fn event(
    env: &PluginEnv,
    herdr: &dyn Herdr,
    audio: &dyn AudioSink,
    stderr: &mut dyn Write,
) -> Result<(), String> {
    let payload = env.event_json.as_deref().unwrap_or("{}");
    let event = match PluginEvent::parse(env.event.as_deref(), payload) {
        Ok(event) => event,
        Err(error) => {
            writeln!(stderr, "warning: malformed Herdr event: {error}")
                .map_err(|error| error.to_string())?;
            return Ok(());
        }
    };
    let pane_id = event
        .string("pane_id")
        .or_else(|| context_string(env, "pane_id"))
        .or_else(|| env.pane_id.clone());
    match event.kind.as_str() {
        "pane_agent_detected" | "pane_agent_status_changed" => {
            let Some(pane_id) = pane_id else {
                return warn(stderr, "event has no pane_id");
            };
            handle_agent_event(env, herdr, audio, stderr, &event, &pane_id)
        }
        "pane_moved" => {
            let Some(new_pane_id) = pane_id else {
                return warn(stderr, "move event has no pane_id");
            };
            let previous = event
                .string("previous_pane_id")
                .unwrap_or_else(|| new_pane_id.clone());
            let workspace = event
                .string("workspace_id")
                .or_else(|| env.workspace_id.clone());
            handle_move_event(
                env,
                herdr,
                stderr,
                &previous,
                &new_pane_id,
                workspace.as_deref(),
            )
        }
        "pane_exited" | "pane_closed" => {
            let Some(pane_id) = pane_id else {
                return warn(stderr, "cleanup event has no pane_id");
            };
            handle_cleanup_event(env, herdr, stderr, &pane_id)
        }
        _ => warn(stderr, &format!("ignored event `{}`", event.kind)),
    }
}

fn handle_agent_event(
    env: &PluginEnv,
    herdr: &dyn Herdr,
    audio: &dyn AudioSink,
    stderr: &mut dyn Write,
    event: &PluginEvent,
    pane_id: &str,
) -> Result<(), String> {
    let config_guard = ConfigGuard::acquire(&env.config_dir).map_err(|error| error.to_string())?;
    let loaded_config = config_guard.load();
    for warning in loaded_config.warnings {
        writeln!(stderr, "warning: {warning}").map_err(|error| error.to_string())?;
    }
    let loaded_theme = load_theme(
        &env.plugin_root,
        &env.config_dir,
        &loaded_config.config.theme,
    )?;
    for warning in loaded_theme.warnings {
        writeln!(stderr, "warning: {warning}").map_err(|error| error.to_string())?;
    }
    let status = event.string("agent_status");
    let store = StateStore::new(&env.state_dir);
    let mut state_guard = store.lock().map_err(|error| error.to_string())?;
    let snapshot = herdr.live_snapshot().ok();
    let pane = match herdr.pane_info(pane_id) {
        Ok(pane) => pane,
        Err(error) => return warn(stderr, &format!("could not query pane: {error}")),
    };
    let Some(identity) = pane.identity() else {
        return warn(stderr, "pane has no durable identity");
    };
    let visible = snapshot
        .as_ref()
        .is_none_or(|snapshot| snapshot.pane_visible(&pane.pane_id));
    let observed_at = now_ms();
    let state = state_guard.state_mut();
    if state.assignments.len() >= loaded_theme.theme.sounds.len() {
        if let Some(snapshot) = &snapshot {
            reconcile_under_pressure(
                state,
                &snapshot.live_panes(),
                loaded_theme.theme.sounds.len(),
            );
        }
    }
    let assignment = assign_sound(state, &identity, &loaded_theme.theme.sounds, observed_at);
    let live_status = (!pane.agent_status.is_empty()).then_some(pane.agent_status.as_str());
    let play = if event.kind == "pane_agent_detected" {
        apply_detection_observation(
            assignment,
            status.as_deref(),
            live_status,
            visible,
            observed_at,
        )
    } else {
        apply_status_observation(
            assignment,
            status.as_deref(),
            live_status,
            visible,
            observed_at,
        )
    };
    let assignment = assignment.clone();
    let seq = loaded_config.config.enabled.then(|| {
        next_metadata_seq(
            state,
            assignment
                .terminal_id
                .as_deref()
                .unwrap_or(&assignment.pane_id),
            "session-sounds",
            observed_at,
        )
    });
    state_guard.commit().map_err(|error| error.to_string())?;
    if loaded_config.config.enabled {
        report_assignment(
            herdr,
            stderr,
            &assignment,
            &loaded_theme.theme,
            seq.expect("enabled metadata has a sequence"),
        )?;
        if play {
            if let Some(sound) = sound_for(&loaded_theme.theme, &assignment.sound_id) {
                warn_playback(stderr, audio.play(&sound.path))?;
            }
        }
    }
    Ok(())
}

fn handle_move_event(
    env: &PluginEnv,
    herdr: &dyn Herdr,
    stderr: &mut dyn Write,
    previous_pane_id: &str,
    pane_id: &str,
    workspace_id: Option<&str>,
) -> Result<(), String> {
    let config_guard = ConfigGuard::acquire(&env.config_dir).map_err(|error| error.to_string())?;
    let loaded_config = config_guard.load();
    emit_warnings(stderr, loaded_config.warnings)?;
    let loaded_theme = load_theme(
        &env.plugin_root,
        &env.config_dir,
        &loaded_config.config.theme,
    )?;
    emit_warnings(stderr, loaded_theme.warnings)?;
    let mut state_guard = StateStore::new(&env.state_dir)
        .lock()
        .map_err(|error| error.to_string())?;
    let pane = match herdr.pane_info(pane_id) {
        Ok(pane) => pane,
        Err(error) => return warn(stderr, &format!("could not query moved pane: {error}")),
    };
    let Some(mut identity) = pane.identity() else {
        return warn(stderr, "moved pane has no durable identity");
    };
    if identity.workspace_id.is_none() {
        identity.workspace_id = workspace_id.map(str::to_owned);
    }
    let observed_at = now_ms();
    let state = state_guard.state_mut();
    let previous_owned_by_current = state
        .assignments
        .iter()
        .find(|assignment| assignment.pane_id == previous_pane_id)
        .is_some_and(|assignment| assignment.identity == identity.key);
    if !previous_owned_by_current {
        cleanup_pane(state, previous_pane_id);
    }
    let assignment =
        assign_sound(state, &identity, &loaded_theme.theme.sounds, observed_at).clone();
    let seq = loaded_config.config.enabled.then(|| {
        next_metadata_seq(
            state,
            assignment
                .terminal_id
                .as_deref()
                .unwrap_or(&assignment.pane_id),
            "session-sounds",
            observed_at,
        )
    });
    state_guard.commit().map_err(|error| error.to_string())?;
    if let Some(seq) = seq {
        report_assignment(herdr, stderr, &assignment, &loaded_theme.theme, seq)?;
    }
    Ok(())
}

fn handle_cleanup_event(
    env: &PluginEnv,
    herdr: &dyn Herdr,
    stderr: &mut dyn Write,
    pane_id: &str,
) -> Result<(), String> {
    let config_guard = ConfigGuard::acquire(&env.config_dir).map_err(|error| error.to_string())?;
    let loaded_config = config_guard.load();
    emit_warnings(stderr, loaded_config.warnings)?;
    let loaded_theme = load_theme(
        &env.plugin_root,
        &env.config_dir,
        &loaded_config.config.theme,
    )?;
    emit_warnings(stderr, loaded_theme.warnings)?;
    let mut state_guard = StateStore::new(&env.state_dir)
        .lock()
        .map_err(|error| error.to_string())?;
    let live_pane = match herdr.pane_info(pane_id) {
        Ok(pane) => Some(pane),
        Err(pane_error) => match herdr.live_snapshot() {
            Ok(snapshot) => snapshot
                .panes
                .into_iter()
                .find(|pane| pane.pane_id == pane_id),
            Err(snapshot_error) => {
                return warn(
                    stderr,
                    &format!(
                        "could not confirm pane cleanup ({pane_error}; {snapshot_error}); retaining assignment"
                    ),
                )
            }
        },
    };
    let Some(pane) = live_pane else {
        cleanup_pane(state_guard.state_mut(), pane_id);
        state_guard.commit().map_err(|error| error.to_string())?;
        return Ok(());
    };
    let Some(identity) = pane.identity() else {
        return warn(
            stderr,
            "cleanup address is still live without a durable identity; retaining assignment",
        );
    };
    let observed_at = now_ms();
    let state = state_guard.state_mut();
    let assignment =
        assign_sound(state, &identity, &loaded_theme.theme.sounds, observed_at).clone();
    let seq = loaded_config.config.enabled.then(|| {
        next_metadata_seq(
            state,
            assignment
                .terminal_id
                .as_deref()
                .unwrap_or(&assignment.pane_id),
            "session-sounds",
            observed_at,
        )
    });
    state_guard.commit().map_err(|error| error.to_string())?;
    if let Some(seq) = seq {
        report_assignment(herdr, stderr, &assignment, &loaded_theme.theme, seq)?;
    }
    Ok(())
}

fn report_assignment(
    herdr: &dyn Herdr,
    stderr: &mut dyn Write,
    assignment: &Assignment,
    theme: &Theme,
    seq: u64,
) -> Result<(), String> {
    let Some(sound) = sound_for(theme, &assignment.sound_id) else {
        return Ok(());
    };
    let metadata = Metadata {
        pane_id: assignment.pane_id.clone(),
        source: "session-sounds".into(),
        token: format!("sound={}", sound.display_name),
        display_agent: format!(
            "{} · {}",
            if assignment.agent.is_empty() {
                "Agent"
            } else {
                &assignment.agent
            },
            sound.display_name
        ),
        raw_agent: (!assignment.agent.is_empty()).then(|| assignment.agent.clone()),
        applies_to_source: assignment.agent_source.clone(),
        seq,
        ttl_ms: 86_400_000,
    };
    if let Err(error) = herdr.report_metadata(&metadata) {
        writeln!(stderr, "warning: metadata update failed: {error}")
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn toggle_mute(
    env: &PluginEnv,
    herdr: &dyn Herdr,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> Result<(), String> {
    let config_guard = ConfigGuard::acquire(&env.config_dir).map_err(|error| error.to_string())?;
    let loaded_config = config_guard.load();
    emit_warnings(stderr, loaded_config.warnings)?;
    let mut config = loaded_config.config;
    config.enabled = !config.enabled;
    config_guard
        .save(&config)
        .map_err(|error| error.to_string())?;
    if !config.enabled {
        let mut state_guard = StateStore::new(&env.state_dir)
            .lock()
            .map_err(|error| error.to_string())?;
        let snapshot = herdr.live_snapshot().ok();
        let assignments = state_guard.state().assignments.clone();
        let mut clears = Vec::new();
        let observed_at = now_ms();
        for assignment in assignments {
            let (pane_id, agent, agent_source, terminal_id) = match &snapshot {
                Some(snapshot) => {
                    let Some(pane) = snapshot.panes.iter().find(|pane| {
                        pane.identity()
                            .is_some_and(|identity| identity.key == assignment.identity)
                    }) else {
                        continue;
                    };
                    let Some(identity) = pane.identity() else {
                        continue;
                    };
                    (
                        pane.pane_id.clone(),
                        identity.agent,
                        identity.agent_source,
                        identity.terminal_id.unwrap_or_else(|| pane.pane_id.clone()),
                    )
                }
                None => (
                    assignment.pane_id.clone(),
                    assignment.agent.clone(),
                    assignment.agent_source.clone(),
                    assignment
                        .terminal_id
                        .clone()
                        .unwrap_or_else(|| assignment.pane_id.clone()),
                ),
            };
            let seq = next_metadata_seq(
                state_guard.state_mut(),
                &terminal_id,
                "session-sounds",
                observed_at,
            );
            clears.push(MetadataClear {
                pane_id,
                source: "session-sounds".into(),
                raw_agent: (!agent.is_empty()).then_some(agent),
                applies_to_source: agent_source,
                seq,
            });
        }
        state_guard.commit().map_err(|error| error.to_string())?;
        for clear in &clears {
            if let Err(error) = herdr.clear_metadata(clear) {
                writeln!(stderr, "warning: metadata clear failed: {error}")
                    .map_err(|error| error.to_string())?;
            }
        }
    }
    writeln!(
        stdout,
        "{}",
        if config.enabled { "unmuted" } else { "muted" }
    )
    .map_err(|error| error.to_string())
}

fn reshuffle(
    env: &PluginEnv,
    herdr: &dyn Herdr,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> Result<(), String> {
    let pane_id = context_string(env, "pane_id")
        .or_else(|| env.pane_id.clone())
        .ok_or_else(|| "HERDR_PANE_ID is missing".to_owned())?;
    let config_guard = ConfigGuard::acquire(&env.config_dir).map_err(|error| error.to_string())?;
    let loaded_config = config_guard.load();
    emit_warnings(stderr, loaded_config.warnings)?;
    let config = loaded_config.config;
    let loaded_theme = load_theme(&env.plugin_root, &env.config_dir, &config.theme)?;
    emit_warnings(stderr, loaded_theme.warnings)?;
    let theme = loaded_theme.theme;
    let mut state_guard = StateStore::new(&env.state_dir)
        .lock()
        .map_err(|error| error.to_string())?;
    let pane = herdr
        .pane_info(&pane_id)
        .map_err(|error| format!("could not query context pane: {error}"))?;
    let identity = pane
        .identity()
        .ok_or_else(|| "context pane has no durable identity".to_owned())?;
    let observed_at = now_ms();
    let state = state_guard.state_mut();
    assign_sound(state, &identity, &theme.sounds, observed_at);
    reshuffle_pane(state, &pane.pane_id, &theme.sounds, observed_at);
    let assignment = state
        .assignments
        .iter()
        .find(|assignment| assignment.identity == identity.key)
        .cloned()
        .ok_or_else(|| "context pane has no sound assignment".to_owned())?;
    let seq = config.enabled.then(|| {
        next_metadata_seq(
            state,
            assignment
                .terminal_id
                .as_deref()
                .unwrap_or(&assignment.pane_id),
            "session-sounds",
            observed_at,
        )
    });
    state_guard.commit().map_err(|error| error.to_string())?;
    if config.enabled {
        report_assignment(
            herdr,
            stderr,
            &assignment,
            &theme,
            seq.expect("enabled metadata has a sequence"),
        )?;
    }
    let display = sound_for(&theme, &assignment.sound_id)
        .map(|sound| sound.display_name.as_str())
        .unwrap_or(&assignment.sound_id);
    writeln!(stdout, "{display}").map_err(|error| error.to_string())
}

fn test_sound(
    env: &PluginEnv,
    audio: &dyn AudioSink,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> Result<(), String> {
    let config_guard = ConfigGuard::acquire(&env.config_dir).map_err(|error| error.to_string())?;
    let loaded_config = config_guard.load();
    emit_warnings(stderr, loaded_config.warnings)?;
    let config = loaded_config.config;
    let loaded_theme = load_theme(&env.plugin_root, &env.config_dir, &config.theme)?;
    emit_warnings(stderr, loaded_theme.warnings)?;
    let theme = loaded_theme.theme;
    let state_guard = StateStore::new(&env.state_dir)
        .lock()
        .map_err(|error| error.to_string())?;
    let state = state_guard.state();
    let assigned = env.pane_id.as_deref().and_then(|pane_id| {
        state
            .assignments
            .iter()
            .find(|assignment| assignment.pane_id == pane_id)
            .and_then(|assignment| sound_for(&theme, &assignment.sound_id))
    });
    let sound = assigned
        .or_else(|| theme.sounds.first())
        .ok_or_else(|| "theme has no sounds".to_owned())?;
    warn_playback(stderr, audio.play(&sound.path))?;
    writeln!(stdout, "{}", sound.display_name).map_err(|error| error.to_string())
}

fn doctor(env: &PluginEnv, audio: &dyn AudioSink, stdout: &mut dyn Write) -> Result<(), String> {
    writeln!(stdout, "session-sounds {}", env!("CARGO_PKG_VERSION"))
        .map_err(|error| error.to_string())?;
    let env_ok = !env.herdr_bin_path.as_os_str().is_empty()
        && !env.plugin_root.as_os_str().is_empty()
        && !env.config_dir.as_os_str().is_empty()
        && !env.state_dir.as_os_str().is_empty();
    writeln!(stdout, "env: {}", if env_ok { "ok" } else { "incomplete" })
        .map_err(|error| error.to_string())?;
    let config = load_config(&env.config_dir);
    for warning in config.warnings {
        writeln!(stdout, "warning: {warning}").map_err(|error| error.to_string())?;
    }
    let theme = load_theme(&env.plugin_root, &env.config_dir, &config.config.theme)?;
    for warning in &theme.warnings {
        writeln!(stdout, "warning: {warning}").map_err(|error| error.to_string())?;
    }
    writeln!(
        stdout,
        "theme: {} ({} sounds)",
        theme.theme.name,
        theme.theme.sounds.len()
    )
    .map_err(|error| error.to_string())?;
    let state = StateStore::new(&env.state_dir)
        .read()
        .map_err(|error| error.to_string())?;
    writeln!(
        stdout,
        "state: {} ({} assignments)",
        if state.warning.is_some() {
            "warning"
        } else {
            "ok"
        },
        state.state.assignments.len()
    )
    .map_err(|error| error.to_string())?;
    writeln!(
        stdout,
        "audio: {}",
        if audio.readiness().is_some() {
            "ready"
        } else {
            "unavailable"
        }
    )
    .map_err(|error| error.to_string())?;
    let path =
        herdr_config_path(env).ok_or_else(|| "cannot resolve Herdr config path".to_owned())?;
    if native_sound_disabled(&path) {
        writeln!(stdout, "Herdr built-in background sound: disabled")
            .map_err(|error| error.to_string())?;
    } else {
        writeln!(
            stdout,
            "Herdr's built-in background sound is enabled and will double-play. Add this to {}:\n[ui.sound]\nenabled = false\nThen run: herdr server reload-config",
            path.display()
        )
        .map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn herdr_config_path(env: &PluginEnv) -> Option<PathBuf> {
    env.herdr_config_path.clone().or_else(|| {
        if cfg!(windows) {
            env.appdata
                .as_ref()
                .map(|base| base.join("herdr").join("config.toml"))
        } else {
            env.xdg_config_home
                .as_ref()
                .map(|base| base.join("herdr").join("config.toml"))
                .or_else(|| {
                    env.home
                        .as_ref()
                        .map(|home| home.join(".config").join("herdr").join("config.toml"))
                })
        }
    })
}

fn native_sound_disabled(path: &Path) -> bool {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| text.parse::<toml::Value>().ok())
        .and_then(|config| {
            config
                .get("ui")
                .and_then(|ui| ui.get("sound"))
                .and_then(|sound| sound.get("enabled"))
                .and_then(toml::Value::as_bool)
        })
        == Some(false)
}

fn context_string(env: &PluginEnv, field: &str) -> Option<String> {
    env.context_json.as_deref().and_then(|json| {
        serde_json::from_str::<serde_json::Value>(json)
            .ok()
            .and_then(|value| {
                value
                    .get(field)
                    .and_then(|value| value.as_str())
                    .map(str::to_owned)
            })
    })
}

fn sound_for<'a>(theme: &'a Theme, sound_id: &str) -> Option<&'a Sound> {
    theme.sounds.iter().find(|sound| sound.id == sound_id)
}

fn warn(stderr: &mut dyn Write, message: &str) -> Result<(), String> {
    writeln!(stderr, "warning: {message}").map_err(|error| error.to_string())
}

fn emit_warnings(
    writer: &mut dyn Write,
    warnings: impl IntoIterator<Item = String>,
) -> Result<(), String> {
    for warning in warnings {
        writeln!(writer, "warning: {warning}").map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn warn_playback(stderr: &mut dyn Write, playback: Playback) -> Result<(), String> {
    match playback {
        Playback::Started => Ok(()),
        Playback::Unavailable => warn(stderr, "no audio player is available"),
        Playback::Failed(error) => warn(stderr, &format!("audio playback failed: {error}")),
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
