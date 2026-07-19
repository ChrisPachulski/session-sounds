#![cfg(unix)]

use session_sounds::herdr::{Herdr, Metadata, MetadataClear, ProcessHerdr};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use tempfile::tempdir;

#[test]
fn process_adapter_queries_live_state_and_emits_guarded_monotonic_metadata_argv() {
    let dir = tempdir().unwrap();
    let binary = dir.path().join("fake-herdr");
    let capture = dir.path().join("argv.log");
    let script = r###"#!/bin/sh
capture="$(dirname "$0")/argv.log"
for argument in "$@"; do
  printf '<%s>' "$argument" >> "$capture"
done
printf '\n' >> "$capture"
if [ "$1" = pane ] && [ "$2" = get ]; then
  printf '%s\n' '{"result":{"pane":{"pane_id":"w1:p1","terminal_id":"term-1","workspace_id":"w1","tab_id":"w1:t1","agent":"codex","agent_status":"working","agent_session":{"agent":"codex","kind":"id","source":"native","value":"session-1"}}}}'
elif [ "$1" = workspace ] && [ "$2" = list ]; then
  printf '%s\n' '{"result":{"workspaces":[{"workspace_id":"w1","focused":true,"active_tab_id":"w1:t1"}]}}'
elif [ "$1" = pane ] && [ "$2" = list ]; then
  printf '%s\n' '{"result":{"panes":[{"pane_id":"w1:p1","terminal_id":"term-1","workspace_id":"w1","tab_id":"w1:t1","agent":"codex","agent_status":"working","agent_session":{"agent":"codex","kind":"id","source":"native","value":"session-1"}}]}}'
fi
"###;
    fs::write(&binary, script).unwrap();
    let mut permissions = fs::metadata(&binary).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&binary, permissions).unwrap();
    let herdr = ProcessHerdr::new(&binary);

    assert_eq!(herdr.pane_info("w1:p1").unwrap().agent_status, "working");
    assert_eq!(herdr.live_snapshot().unwrap().panes.len(), 1);
    herdr
        .report_metadata(&Metadata {
            pane_id: "w1:p1".into(),
            source: "session-sounds".into(),
            token: "sound=Warm Bell".into(),
            display_agent: "codex · Warm Bell".into(),
            raw_agent: Some("codex".into()),
            applies_to_source: Some("native".into()),
            seq: 101,
            ttl_ms: 86_400_000,
        })
        .unwrap();
    herdr
        .clear_metadata(&MetadataClear {
            pane_id: "w1:p1".into(),
            source: "session-sounds".into(),
            raw_agent: Some("codex".into()),
            applies_to_source: Some("native".into()),
            seq: 102,
        })
        .unwrap();

    let argv = fs::read_to_string(capture).unwrap();
    assert!(argv.contains("<pane><get><w1:p1>"));
    assert!(argv.contains("<workspace><list>"));
    assert!(argv.contains("<pane><list><--workspace><w1>"));
    assert!(argv.contains("<--token><sound=Warm Bell>"));
    assert!(argv.contains("<--display-agent><codex · Warm Bell>"));
    assert!(argv.contains("<--agent><codex>"));
    assert!(argv.contains("<--applies-to-source><native>"));
    assert!(argv.contains("<--seq><101>"));
    assert!(argv.contains("<--ttl-ms><86400000>"));
    assert!(argv.contains("<--clear-token><sound><--clear-display-agent>"));
    assert!(argv.contains("<--seq><102>"));
}

#[test]
fn metadata_without_authoritative_agent_omits_agent_guards() {
    let dir = tempdir().unwrap();
    let binary = dir.path().join("fake-herdr");
    let script = r###"#!/bin/sh
capture="$(dirname "$0")/argv.log"
for argument in "$@"; do printf '<%s>' "$argument" >> "$capture"; done
printf '\n' >> "$capture"
"###;
    fs::write(&binary, script).unwrap();
    let mut permissions = fs::metadata(&binary).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&binary, permissions).unwrap();
    let herdr = ProcessHerdr::new(&binary);

    herdr
        .report_metadata(&Metadata {
            pane_id: "w1:p1".into(),
            source: "session-sounds".into(),
            token: "sound=Warm Bell".into(),
            display_agent: "Agent · Warm Bell".into(),
            raw_agent: None,
            applies_to_source: None,
            seq: 1,
            ttl_ms: 86_400_000,
        })
        .unwrap();

    let argv = fs::read_to_string(dir.path().join("argv.log")).unwrap();
    assert!(!argv.contains("<--agent>"));
    assert!(!argv.contains("<--applies-to-source>"));
}
