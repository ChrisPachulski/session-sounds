use session_sounds::herdr::{parse_pane_response, parse_snapshot_responses};

#[test]
fn pane_parser_reads_live_074_shape_and_durable_session() {
    let pane = parse_pane_response(
        r#"{"id":"1","result":{"type":"pane_info","pane":{"pane_id":"w1:p1","terminal_id":"term-1","workspace_id":"w1","tab_id":"w1:t1","focused":true,"agent_status":"working","agent":"codex","agent_session":{"agent":"codex","kind":"id","source":"native","value":"abc"}}}}"#,
    )
    .unwrap();

    assert_eq!(pane.pane_id, "w1:p1");
    assert_eq!(pane.agent_session.unwrap().value, "abc");
}

#[test]
fn snapshot_parser_combines_workspace_and_pane_lists() {
    let snapshot = parse_snapshot_responses(
        r#"{"result":{"type":"workspace_list","workspaces":[{"workspace_id":"w1","focused":true,"active_tab_id":"w1:t1"}]}}"#,
        &[r#"{"result":{"type":"pane_list","panes":[{"pane_id":"w1:p1","terminal_id":"term-1","workspace_id":"w1","tab_id":"w1:t1","focused":true,"agent_status":"idle","agent":null,"agent_session":null}]}}"#],
    )
    .unwrap();

    assert!(snapshot.pane_visible("w1:p1"));
    assert_eq!(snapshot.panes.len(), 1);
}

#[test]
fn unfocused_split_in_focused_workspaces_active_tab_is_visible() {
    let snapshot = parse_snapshot_responses(
        r#"{"result":{"workspaces":[{"workspace_id":"w1","focused":true,"active_tab_id":"w1:t1"}]}}"#,
        &[r#"{"result":{"panes":[{"pane_id":"w1:p2","terminal_id":"term-2","workspace_id":"w1","tab_id":"w1:t1","focused":false,"agent_status":"blocked"}]}}"#],
    )
    .unwrap();

    assert!(snapshot.pane_visible("w1:p2"));
}
