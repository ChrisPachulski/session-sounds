use crate::atomic;
use crate::theme::Sound;
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub const STATE_VERSION: u32 = 1;
pub const DEBOUNCE_MS: u64 = 1_500;

#[derive(Clone, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum IdentityKey {
    AgentSession {
        agent: String,
        session_kind: String,
        session_value: String,
    },
    Terminal {
        terminal_id: String,
    },
}

impl IdentityKey {
    pub fn from_parts(
        agent: Option<&str>,
        session_kind: Option<&str>,
        session_value: Option<&str>,
        terminal_id: Option<&str>,
    ) -> Option<Self> {
        match (agent, session_kind, session_value) {
            (Some(agent), Some(session_kind), Some(session_value))
                if !agent.is_empty() && !session_kind.is_empty() && !session_value.is_empty() =>
            {
                Some(Self::AgentSession {
                    agent: agent.into(),
                    session_kind: session_kind.into(),
                    session_value: session_value.into(),
                })
            }
            _ => terminal_id
                .filter(|terminal_id| !terminal_id.is_empty())
                .map(|terminal_id| Self::Terminal {
                    terminal_id: terminal_id.into(),
                }),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PaneIdentity {
    pub key: IdentityKey,
    pub agent: String,
    pub agent_source: Option<String>,
    pub terminal_id: Option<String>,
    pub pane_id: String,
    pub workspace_id: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Assignment {
    pub identity: IdentityKey,
    pub agent: String,
    #[serde(default)]
    pub agent_source: Option<String>,
    pub terminal_id: Option<String>,
    pub pane_id: String,
    pub workspace_id: Option<String>,
    pub sound_id: String,
    pub assigned_at_ms: u64,
    pub last_played_at_ms: Option<u64>,
    pub status: Option<String>,
    #[serde(default)]
    pub completion_handled: bool,
    #[serde(default)]
    pub blocked_handled: bool,
    #[serde(default)]
    pub last_event_status: Option<String>,
    #[serde(default)]
    pub pending_cycle_predecessor: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct State {
    pub version: u32,
    pub assignments: Vec<Assignment>,
    #[serde(default)]
    pub metadata_sequences: BTreeMap<String, u64>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            version: STATE_VERSION,
            assignments: Vec::new(),
            metadata_sequences: BTreeMap::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LivePane {
    pub identity: IdentityKey,
    pub pane_id: String,
}

pub fn assign_sound<'a>(
    state: &'a mut State,
    pane: &PaneIdentity,
    sounds: &[Sound],
    now_ms: u64,
) -> &'a mut Assignment {
    assert!(!sounds.is_empty(), "validated theme has no sounds");
    let terminal_agent_replacement = matches!(pane.key, IdentityKey::Terminal { .. })
        && state
            .assignments
            .iter()
            .any(|assignment| assignment.identity == pane.key && assignment.agent != pane.agent);
    state.assignments.retain(|assignment| {
        if assignment.identity == pane.key && !terminal_agent_replacement {
            return true;
        }
        let same_pane = assignment.pane_id == pane.pane_id;
        let same_terminal = pane.terminal_id.is_some()
            && assignment.terminal_id.as_ref() == pane.terminal_id.as_ref();
        !same_pane && !same_terminal
    });

    if let Some(index) = state
        .assignments
        .iter()
        .position(|assignment| assignment.identity == pane.key)
    {
        let sound_is_valid = sounds
            .iter()
            .any(|sound| sound.id == state.assignments[index].sound_id);
        let replacement =
            (!sound_is_valid).then(|| choose_sound(&state.assignments, sounds).to_owned());
        let assignment = &mut state.assignments[index];
        assignment.agent.clone_from(&pane.agent);
        assignment.agent_source.clone_from(&pane.agent_source);
        assignment.terminal_id.clone_from(&pane.terminal_id);
        assignment.pane_id.clone_from(&pane.pane_id);
        assignment.workspace_id.clone_from(&pane.workspace_id);
        if let Some(replacement) = replacement {
            assignment.sound_id = replacement;
            assignment.assigned_at_ms = now_ms;
        }
        return assignment;
    }

    let sound_id = choose_sound(&state.assignments, sounds).to_owned();
    state.assignments.push(Assignment {
        identity: pane.key.clone(),
        agent: pane.agent.clone(),
        agent_source: pane.agent_source.clone(),
        terminal_id: pane.terminal_id.clone(),
        pane_id: pane.pane_id.clone(),
        workspace_id: pane.workspace_id.clone(),
        sound_id,
        assigned_at_ms: now_ms,
        last_played_at_ms: None,
        status: None,
        completion_handled: false,
        blocked_handled: false,
        last_event_status: None,
        pending_cycle_predecessor: None,
    });
    state.assignments.last_mut().expect("assignment was pushed")
}

fn choose_sound<'a>(assignments: &[Assignment], sounds: &'a [Sound]) -> &'a str {
    for sound in sounds {
        if assignments
            .iter()
            .all(|assignment| assignment.sound_id != sound.id)
        {
            return &sound.id;
        }
    }
    sounds
        .iter()
        .min_by_key(|sound| {
            assignments
                .iter()
                .filter(|assignment| assignment.sound_id == sound.id)
                .map(|assignment| assignment.assigned_at_ms)
                .max()
                .unwrap_or(0)
        })
        .map(|sound| sound.id.as_str())
        .expect("validated theme has sounds")
}

pub fn reshuffle_pane(
    state: &mut State,
    pane_id: &str,
    sounds: &[Sound],
    now_ms: u64,
) -> Option<String> {
    let index = state
        .assignments
        .iter()
        .position(|assignment| assignment.pane_id == pane_id)?;
    let current = state.assignments[index].sound_id.as_str();
    let replacement = if state.assignments.len() <= sounds.len() {
        sounds.iter().find(|sound| {
            sound.id != current
                && state
                    .assignments
                    .iter()
                    .enumerate()
                    .all(|(other_index, assignment)| {
                        other_index == index || assignment.sound_id != sound.id
                    })
        })
    } else {
        sounds
            .iter()
            .filter(|sound| sound.id != current)
            .min_by_key(|sound| {
                state
                    .assignments
                    .iter()
                    .filter(|assignment| assignment.sound_id == sound.id)
                    .map(|assignment| assignment.assigned_at_ms)
                    .max()
                    .unwrap_or(0)
            })
    }
    .or_else(|| sounds.iter().find(|sound| sound.id != current))
    .or_else(|| sounds.first())?;
    let assignment = &mut state.assignments[index];
    assignment.sound_id.clone_from(&replacement.id);
    assignment.assigned_at_ms = now_ms;
    Some(assignment.sound_id.clone())
}

pub fn cleanup_pane(state: &mut State, pane_id: &str) -> bool {
    let previous = state.assignments.len();
    state
        .assignments
        .retain(|assignment| assignment.pane_id != pane_id);
    previous != state.assignments.len()
}

pub fn move_pane(
    state: &mut State,
    previous_pane_id: &str,
    pane_id: &str,
    workspace_id: Option<&str>,
    terminal_id: Option<&str>,
) -> bool {
    let Some(identity) = state
        .assignments
        .iter()
        .find(|assignment| assignment.pane_id == previous_pane_id)
        .map(|assignment| assignment.identity.clone())
    else {
        return false;
    };
    state.assignments.retain(|assignment| {
        assignment.identity == identity
            || (assignment.pane_id != pane_id
                && terminal_id.is_none_or(|terminal_id| {
                    assignment.terminal_id.as_deref() != Some(terminal_id)
                }))
    });
    let assignment = state
        .assignments
        .iter_mut()
        .find(|assignment| assignment.identity == identity)
        .expect("moving assignment was retained");
    assignment.pane_id = pane_id.into();
    assignment.workspace_id = workspace_id.map(str::to_owned);
    if let Some(terminal_id) = terminal_id {
        assignment.terminal_id = Some(terminal_id.into());
    }
    true
}

pub fn reconcile_under_pressure(
    state: &mut State,
    live_panes: &[LivePane],
    pool_size: usize,
) -> usize {
    if state.assignments.len() < pool_size {
        return 0;
    }
    let live: HashSet<_> = live_panes.iter().map(|pane| &pane.identity).collect();
    for assignment in &mut state.assignments {
        if let Some(pane) = live_panes
            .iter()
            .find(|pane| pane.identity == assignment.identity)
        {
            assignment.pane_id.clone_from(&pane.pane_id);
        }
    }
    let previous = state.assignments.len();
    state
        .assignments
        .retain(|assignment| live.contains(&assignment.identity));
    previous - state.assignments.len()
}

pub fn apply_status(
    assignment: &mut Assignment,
    new_status: &str,
    target_visible: bool,
    now_ms: u64,
) -> bool {
    let previous = assignment.status.as_deref();
    let completed = new_status == "done" && matches!(previous, Some("working" | "blocked"));
    let newly_blocked =
        new_status == "blocked" && previous.is_some() && previous != Some("blocked");
    let candidate = !target_visible && (completed || newly_blocked);
    assignment.status = Some(new_status.into());
    if new_status != "done" {
        assignment.completion_handled = false;
    } else if completed {
        assignment.completion_handled = true;
    }
    if new_status != "blocked" {
        assignment.blocked_handled = false;
    } else if newly_blocked {
        assignment.blocked_handled = true;
    }
    if !candidate
        || assignment
            .last_played_at_ms
            .is_some_and(|last| now_ms.saturating_sub(last) < DEBOUNCE_MS)
    {
        return false;
    }
    assignment.last_played_at_ms = Some(now_ms);
    true
}

pub fn apply_status_observation(
    assignment: &mut Assignment,
    event_status: Option<&str>,
    live_status: Option<&str>,
    target_visible: bool,
    now_ms: u64,
) -> bool {
    let event_status = event_status.filter(|status| known_status(status));
    let current = live_status
        .filter(|status| known_status(status))
        .or(event_status);
    let Some(current) = current else {
        return false;
    };
    let event_is_duplicate =
        event_status.is_some() && event_status == assignment.last_event_status.as_deref();
    match current {
        "done" => {
            let confirmed_predecessor = (event_status == Some(current))
                .then(|| assignment.pending_cycle_predecessor.take())
                .flatten()
                .filter(|status| matches!(status.as_str(), "working" | "blocked"));
            if let Some(historical) = confirmed_predecessor {
                set_historical_status(assignment, &historical);
            } else if let Some(historical @ ("working" | "blocked")) =
                event_status.filter(|event_status| *event_status != current)
            {
                if event_is_duplicate
                    && assignment.status.as_deref() == Some(current)
                    && assignment.completion_handled
                {
                    assignment.pending_cycle_predecessor = Some(historical.into());
                } else if !event_is_duplicate {
                    assignment.pending_cycle_predecessor = None;
                    set_historical_status(assignment, historical);
                }
            }
            if !matches!(assignment.status.as_deref(), Some("working" | "blocked"))
                && !assignment.completion_handled
            {
                set_historical_status(assignment, "working");
            }
        }
        "blocked" => {
            let confirmed_predecessor = (event_status == Some(current))
                .then(|| assignment.pending_cycle_predecessor.take())
                .flatten()
                .filter(|status| status != "blocked" && known_status(status));
            if let Some(historical) = confirmed_predecessor {
                set_historical_status(assignment, &historical);
            } else if let Some(historical) = event_status
                .filter(|event_status| *event_status != "blocked" && known_status(event_status))
            {
                if event_is_duplicate
                    && assignment.status.as_deref() == Some(current)
                    && assignment.blocked_handled
                {
                    assignment.pending_cycle_predecessor = Some(historical.into());
                } else if !event_is_duplicate {
                    assignment.pending_cycle_predecessor = None;
                    set_historical_status(assignment, historical);
                }
            }
            if assignment.status.as_deref() != Some("working") && !assignment.blocked_handled {
                set_historical_status(assignment, "working");
            }
        }
        _ => assignment.pending_cycle_predecessor = None,
    }
    let play = apply_status(assignment, current, target_visible, now_ms);
    if let Some(event_status) = event_status {
        assignment.last_event_status = Some(event_status.into());
    }
    play
}

pub fn apply_detection_observation(
    assignment: &mut Assignment,
    event_status: Option<&str>,
    live_status: Option<&str>,
    _target_visible: bool,
    _now_ms: u64,
) -> bool {
    let event_status = event_status.filter(|status| known_status(status));
    let current = live_status
        .filter(|status| known_status(status))
        .or(event_status);
    let Some(current) = current else {
        return false;
    };
    let previous = assignment.status.as_deref();
    if current != "done" || previous != Some("done") {
        assignment.completion_handled = false;
    }
    if current != "blocked" || previous != Some("blocked") {
        assignment.blocked_handled = false;
    }
    assignment.status = Some(current.into());
    if let Some(event_status) = event_status {
        assignment.last_event_status = Some(event_status.into());
    }
    false
}

pub fn next_metadata_seq(state: &mut State, terminal_id: &str, source: &str, now_ms: u64) -> u64 {
    let key = format!("{terminal_id}\u{0}{source}");
    let previous = state.metadata_sequences.get(&key).copied().unwrap_or(0);
    let next = previous.saturating_add(1).max(now_ms);
    state.metadata_sequences.insert(key, next);
    next
}

fn known_status(status: &str) -> bool {
    matches!(status, "idle" | "working" | "blocked" | "done" | "unknown")
}

fn set_historical_status(assignment: &mut Assignment, status: &str) {
    assignment.status = Some(status.into());
    assignment.completion_handled = false;
    assignment.blocked_handled = false;
}

#[derive(Clone, Debug)]
pub struct LoadedState {
    pub state: State,
    pub warning: Option<String>,
}

#[derive(Clone, Debug)]
pub struct StateStore {
    directory: PathBuf,
}

impl StateStore {
    pub fn new(directory: &Path) -> Self {
        Self {
            directory: directory.into(),
        }
    }

    pub fn read(&self) -> io::Result<LoadedState> {
        read_state(&self.directory.join("state.json"))
    }

    pub fn transaction<T>(
        &self,
        operation: impl FnOnce(&mut State) -> io::Result<T>,
    ) -> io::Result<T> {
        let mut guard = self.lock()?;
        let result = operation(guard.state_mut())?;
        guard.commit()?;
        Ok(result)
    }

    pub fn lock(&self) -> io::Result<StateGuard> {
        fs::create_dir_all(&self.directory)?;
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(self.directory.join("state.lock"))?;
        FileExt::lock_exclusive(&file)?;
        let mut state = self.read()?.state;
        state.version = STATE_VERSION;
        Ok(StateGuard {
            directory: self.directory.clone(),
            state,
            lock: file,
        })
    }
}

pub struct StateGuard {
    directory: PathBuf,
    state: State,
    lock: File,
}

impl StateGuard {
    pub fn state(&self) -> &State {
        &self.state
    }

    pub fn state_mut(&mut self) -> &mut State {
        &mut self.state
    }

    pub fn commit(&mut self) -> io::Result<()> {
        atomic_write(&self.directory, &self.state)
    }
}

impl Drop for StateGuard {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.lock);
    }
}

fn read_state(path: &Path) -> io::Result<LoadedState> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Ok(LoadedState {
                state: State::default(),
                warning: None,
            });
        }
        Err(error) => return Err(error),
    };
    match serde_json::from_slice::<State>(&bytes) {
        Ok(state) if state.version == STATE_VERSION => Ok(LoadedState {
            state,
            warning: None,
        }),
        Ok(state) => Ok(LoadedState {
            warning: Some(format!(
                "unsupported state version {}; reconstructing",
                state.version
            )),
            state: State::default(),
        }),
        Err(error) => Ok(LoadedState {
            warning: Some(format!("corrupt state; reconstructing: {error}")),
            state: State::default(),
        }),
    }
}

fn atomic_write(directory: &Path, state: &State) -> io::Result<()> {
    let bytes = serde_json::to_vec_pretty(state).map_err(io::Error::other)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = directory.join(format!(".state.{}.{nonce}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    if let Err(error) = (|| {
        file.write_all(&bytes)?;
        file.sync_all()?;
        atomic::replace(&temporary, &directory.join("state.json"))
    })() {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    Ok(())
}
