"""Tests for install_claude_sounds hook registration.

Regression guard for the "tab spinner does not animate after turn 1" bug.

The spinner thread animates the terminal tab title only while its state file
says "spin". title_hook.py flips that state:
  - SessionStart / UserPromptSubmit -> "spin"  (animate)
  - Stop / SessionEnd / etc.        -> "idle"  (static)

SessionStart fires exactly once per session, so after the first Stop flips the
state to "idle" the spinner never animates again -- unless a UserPromptSubmit
hook re-flips it to "spin" at the start of every subsequent turn. title_hook.py
already MAPS UserPromptSubmit -> "spin", but the installer never REGISTERED a
UserPromptSubmit hook, so title_hook.py UserPromptSubmit was never invoked and
the spinner stayed frozen from turn 2 onward.

These tests assert the installer now registers that hook and that install()'s
settings.json merge wires it in without clobbering unrelated hook events.
"""
import json
import sys
from pathlib import Path

import pytest

# Make the installer importable (it lives at the repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import install_claude_sounds  # noqa: E402


# --------------------------------------------------------------------------- #
# RED gate: _hook_commands() must register UserPromptSubmit -> title_hook.py
# --------------------------------------------------------------------------- #


def _commands_for(event: str) -> list:
    """Flatten every command string registered under ``event``."""
    groups = install_claude_sounds._hook_commands().get(event, [])
    return [
        h.get("command", "")
        for group in groups
        for h in group.get("hooks", [])
    ]


def test_userpromptsubmit_hook_is_registered():
    """The installer must register a UserPromptSubmit event so the spinner is
    re-armed at the start of every turn (not just the first)."""
    hooks = install_claude_sounds._hook_commands()
    assert "UserPromptSubmit" in hooks, (
        "UserPromptSubmit hook missing -- tab spinner will not animate after "
        "turn 1 because nothing re-flips the spinner state back to 'spin'."
    )


def test_userpromptsubmit_invokes_title_hook_with_literal_event_arg():
    """UserPromptSubmit must call title_hook.py with the literal 'UserPromptSubmit'
    CLI arg -- title_hook.py reads the event name from argv[1], not stdin."""
    commands = _commands_for("UserPromptSubmit")
    assert commands, "UserPromptSubmit registered but has no hook commands"
    title_hook_cmds = [c for c in commands if "title_hook.py" in c]
    assert title_hook_cmds, (
        f"UserPromptSubmit must invoke title_hook.py; got commands: {commands}"
    )
    assert all(c.rstrip().endswith("UserPromptSubmit") for c in title_hook_cmds), (
        "title_hook.py must be passed the literal 'UserPromptSubmit' event arg so "
        f"it maps the event to the 'spin' state; got: {title_hook_cmds}"
    )


def test_userpromptsubmit_plays_no_sound():
    """Submitting a prompt must NOT play a sound -- only flip the spinner state.
    So no sound_manager.py invocation may hang off UserPromptSubmit."""
    commands = _commands_for("UserPromptSubmit")
    assert commands, "UserPromptSubmit registered but has no hook commands"
    assert all("sound_manager.py" not in c for c in commands), (
        f"UserPromptSubmit must not trigger sound_manager (silent event); got: {commands}"
    )


def test_title_hook_maps_userpromptsubmit_to_spin():
    """Cross-check the consumer side: title_hook.py must map the event the
    installer now sends ('UserPromptSubmit') to the 'spin' state. If this map
    ever loses the key, the registered hook would be inert."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sounds"))
    import title_hook  # noqa: E402

    assert title_hook._EVENT_STATE.get("UserPromptSubmit") == "spin"


# --------------------------------------------------------------------------- #
# GREEN gate: install()'s settings.json merge wires the hook in cleanly and
# does not duplicate or clobber unrelated hook events (end-to-end merge check).
# --------------------------------------------------------------------------- #


@pytest.fixture
def install_env(tmp_path, monkeypatch):
    """Point every install()-touched module path at an isolated scratch HOME and
    stub the external side-effect helpers (VS Code, Apple Terminal, Codex config,
    GSD statusline, shell rc) so the test exercises ONLY the hook-merge codepath.

    SOUNDS_SRC is left pointing at the real repo sounds/ dir so the copy step
    succeeds; everything it writes lands under the scratch HOME.
    """
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    sounds_dst = claude_dir / "sounds"
    assignments_dir = sounds_dst / "assignments"
    claude_dir.mkdir(parents=True)
    settings_path = claude_dir / "settings.json"

    monkeypatch.setattr(install_claude_sounds, "SOUNDS_DST", sounds_dst)
    monkeypatch.setattr(install_claude_sounds, "ASSIGNMENTS_DIR", assignments_dir)
    monkeypatch.setattr(install_claude_sounds, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(
        install_claude_sounds, "GSD_STATUSLINE_PATH",
        claude_dir / "hooks" / "gsd-statusline.js",
    )

    # Stub external side effects so install() cannot touch the real machine.
    monkeypatch.setattr(install_claude_sounds, "_update_vscode_settings", lambda: False)
    monkeypatch.setattr(install_claude_sounds, "_patch_gsd_statusline", lambda: False)
    monkeypatch.setattr(install_claude_sounds, "_configure_apple_terminal", lambda: False)
    monkeypatch.setattr(install_claude_sounds, "_configure_codex_title", lambda: None)
    # Redirect the shell-rc wrapper write into the scratch HOME (Path.home()).
    monkeypatch.setattr(install_claude_sounds.Path, "home", staticmethod(lambda: home))

    return {"settings_path": settings_path, "home": home}


def _all_registered_commands(settings: dict, event: str) -> list:
    return [
        h.get("command", "")
        for group in settings.get("hooks", {}).get(event, [])
        for h in group.get("hooks", [])
    ]


def test_install_merges_userpromptsubmit_without_clobbering_unrelated_hooks(install_env):
    """End-to-end: run install() against a scratch settings.json that already has
    an unrelated hook event plus a stale (empty) UserPromptSubmit key. The new
    UserPromptSubmit -> title_hook.py hook must be merged in cleanly, exactly
    once, while the unrelated event is preserved untouched."""
    settings_path = install_env["settings_path"]

    # Pre-existing settings: a stale/empty UserPromptSubmit key and an unrelated
    # user hook the installer must NOT touch.
    sentinel_cmd = "echo my-own-preexisting-hook"
    settings_path.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [],
            "PreToolUse": [{"hooks": [
                {"type": "command", "command": sentinel_cmd, "timeout": 5},
            ]}],
        },
        "someUnrelatedTopLevelKey": {"keep": "me"},
    }))

    install_claude_sounds.install()

    merged = json.loads(settings_path.read_text())

    # 1. UserPromptSubmit now invokes title_hook.py with the literal event arg.
    ups_cmds = _all_registered_commands(merged, "UserPromptSubmit")
    title_hook_cmds = [c for c in ups_cmds if "title_hook.py" in c]
    assert len(title_hook_cmds) == 1, (
        f"expected exactly one title_hook.py UserPromptSubmit hook, got: {ups_cmds}"
    )
    assert title_hook_cmds[0].rstrip().endswith("UserPromptSubmit")
    # And no sound plays on prompt submit.
    assert all("sound_manager.py" not in c for c in ups_cmds)

    # 2. The unrelated PreToolUse hook survived untouched.
    pretool_cmds = _all_registered_commands(merged, "PreToolUse")
    assert sentinel_cmd in pretool_cmds, "installer clobbered an unrelated hook event"

    # 3. Unrelated top-level settings survived.
    assert merged.get("someUnrelatedTopLevelKey") == {"keep": "me"}

    # 4. All the other sound events still got wired in (merge covers every event).
    for event in ("SessionStart", "Stop", "Notification", "StopFailure", "SessionEnd"):
        assert merged.get("hooks", {}).get(event), f"{event} hook not registered"


def test_install_is_idempotent_for_userpromptsubmit(install_env):
    """Running install() twice must not duplicate the UserPromptSubmit hook --
    the 'cleaned' upgrade-path filter strips prior title_hook.py entries before
    re-adding, so a re-run stays at exactly one title_hook.py invocation."""
    settings_path = install_env["settings_path"]

    install_claude_sounds.install()
    install_claude_sounds.install()

    merged = json.loads(settings_path.read_text())
    ups_cmds = _all_registered_commands(merged, "UserPromptSubmit")
    title_hook_cmds = [c for c in ups_cmds if "title_hook.py" in c]
    assert len(title_hook_cmds) == 1, (
        f"re-running install() duplicated the UserPromptSubmit hook: {ups_cmds}"
    )
