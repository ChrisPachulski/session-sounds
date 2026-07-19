use crate::state::{IdentityKey, LivePane, PaneIdentity};
use serde::Deserialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq)]
pub struct AgentSession {
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub value: String,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq)]
pub struct PaneInfo {
    #[serde(default)]
    pub pane_id: String,
    #[serde(default)]
    pub terminal_id: String,
    #[serde(default)]
    pub workspace_id: String,
    #[serde(default)]
    pub tab_id: String,
    #[serde(default)]
    pub focused: bool,
    #[serde(default)]
    pub agent: Option<String>,
    #[serde(default)]
    pub agent_status: String,
    #[serde(default)]
    pub agent_session: Option<AgentSession>,
}

impl PaneInfo {
    pub fn identity(&self) -> Option<PaneIdentity> {
        let session = self.agent_session.as_ref();
        let raw_agent = self
            .agent
            .as_deref()
            .or_else(|| session.map(|session| session.agent.as_str()))?;
        let key = IdentityKey::from_parts(
            Some(raw_agent),
            session.map(|session| session.kind.as_str()),
            session.map(|session| session.value.as_str()),
            Some(self.terminal_id.as_str()),
        )?;
        Some(PaneIdentity {
            key,
            agent: raw_agent.into(),
            terminal_id: (!self.terminal_id.is_empty()).then(|| self.terminal_id.clone()),
            pane_id: self.pane_id.clone(),
            workspace_id: (!self.workspace_id.is_empty()).then(|| self.workspace_id.clone()),
        })
    }
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq)]
pub struct WorkspaceInfo {
    #[serde(default)]
    pub workspace_id: String,
    #[serde(default)]
    pub focused: bool,
    #[serde(default)]
    pub active_tab_id: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct LiveSnapshot {
    pub panes: Vec<PaneInfo>,
    pub workspaces: Vec<WorkspaceInfo>,
}

impl LiveSnapshot {
    pub fn pane_visible(&self, pane_id: &str) -> bool {
        let Some(pane) = self.panes.iter().find(|pane| pane.pane_id == pane_id) else {
            return false;
        };
        self.workspaces.iter().any(|workspace| {
            workspace.workspace_id == pane.workspace_id
                && workspace.focused
                && workspace
                    .active_tab_id
                    .as_deref()
                    .is_none_or(|active| active == pane.tab_id)
        })
    }

    pub fn live_panes(&self) -> Vec<LivePane> {
        self.panes
            .iter()
            .filter_map(|pane| {
                pane.identity().map(|identity| LivePane {
                    identity: identity.key,
                    pane_id: pane.pane_id.clone(),
                })
            })
            .collect()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Metadata {
    pub pane_id: String,
    pub source: String,
    pub token: String,
    pub display_agent: String,
    pub raw_agent: String,
    pub ttl_ms: u64,
}

pub trait Herdr {
    fn pane_info(&self, pane_id: &str) -> Result<PaneInfo, String>;
    fn live_snapshot(&self) -> Result<LiveSnapshot, String>;
    fn report_metadata(&self, metadata: &Metadata) -> Result<(), String>;
    fn clear_metadata(&self, pane_id: &str, raw_agent: &str) -> Result<(), String>;
}

#[derive(Clone, Debug)]
pub struct ProcessHerdr {
    binary: PathBuf,
}

impl ProcessHerdr {
    pub fn new(binary: &Path) -> Self {
        Self {
            binary: binary.into(),
        }
    }

    fn output(&self, arguments: &[&str]) -> Result<String, String> {
        let output = Command::new(&self.binary)
            .args(arguments)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .map_err(|error| error.to_string())?;
        if !output.status.success() {
            return Err(format!("Herdr exited with {}", output.status));
        }
        String::from_utf8(output.stdout).map_err(|error| error.to_string())
    }

    fn status(&self, arguments: &[&str]) -> Result<(), String> {
        let status = Command::new(&self.binary)
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|error| error.to_string())?;
        status
            .success()
            .then_some(())
            .ok_or_else(|| format!("Herdr exited with {status}"))
    }
}

impl Herdr for ProcessHerdr {
    fn pane_info(&self, pane_id: &str) -> Result<PaneInfo, String> {
        parse_pane_response(&self.output(&["pane", "get", pane_id])?)
    }

    fn live_snapshot(&self) -> Result<LiveSnapshot, String> {
        let workspaces_json = self.output(&["workspace", "list"])?;
        let workspaces = parse_workspaces(&workspaces_json)?;
        let pane_json: Result<Vec<_>, _> = workspaces
            .iter()
            .map(|workspace| self.output(&["pane", "list", "--workspace", &workspace.workspace_id]))
            .collect();
        let pane_json = pane_json?;
        let references: Vec<_> = pane_json.iter().map(String::as_str).collect();
        parse_snapshot_responses(&workspaces_json, &references)
    }

    fn report_metadata(&self, metadata: &Metadata) -> Result<(), String> {
        self.status(&[
            "pane",
            "report-metadata",
            &metadata.pane_id,
            "--source",
            &metadata.source,
            "--token",
            &metadata.token,
            "--display-agent",
            &metadata.display_agent,
            "--agent",
            &metadata.raw_agent,
            "--ttl-ms",
            &metadata.ttl_ms.to_string(),
        ])
    }

    fn clear_metadata(&self, pane_id: &str, raw_agent: &str) -> Result<(), String> {
        self.status(&[
            "pane",
            "report-metadata",
            pane_id,
            "--source",
            "session-sounds",
            "--clear-token",
            "sound",
            "--clear-display-agent",
            "--agent",
            raw_agent,
        ])
    }
}

pub fn parse_pane_response(json: &str) -> Result<PaneInfo, String> {
    let value: Value = serde_json::from_str(json).map_err(|error| error.to_string())?;
    let pane = value
        .pointer("/result/pane")
        .or_else(|| value.get("pane"))
        .unwrap_or(&value);
    let pane: PaneInfo = serde_json::from_value(pane.clone()).map_err(|error| error.to_string())?;
    if pane.pane_id.is_empty() {
        return Err("pane response has no pane_id".into());
    }
    Ok(pane)
}

fn parse_workspaces(json: &str) -> Result<Vec<WorkspaceInfo>, String> {
    let value: Value = serde_json::from_str(json).map_err(|error| error.to_string())?;
    let workspaces = value
        .pointer("/result/workspaces")
        .or_else(|| value.get("workspaces"))
        .ok_or_else(|| "workspace response has no workspaces".to_owned())?;
    serde_json::from_value(workspaces.clone()).map_err(|error| error.to_string())
}

fn parse_panes(json: &str) -> Result<Vec<PaneInfo>, String> {
    let value: Value = serde_json::from_str(json).map_err(|error| error.to_string())?;
    let panes = value
        .pointer("/result/panes")
        .or_else(|| value.get("panes"))
        .ok_or_else(|| "pane list response has no panes".to_owned())?;
    serde_json::from_value(panes.clone()).map_err(|error| error.to_string())
}

pub fn parse_snapshot_responses(
    workspaces_json: &str,
    pane_list_json: &[&str],
) -> Result<LiveSnapshot, String> {
    let workspaces = parse_workspaces(workspaces_json)?;
    let mut panes = Vec::new();
    for json in pane_list_json {
        panes.extend(parse_panes(json)?);
    }
    Ok(LiveSnapshot { panes, workspaces })
}
