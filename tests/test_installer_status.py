"""Tests for install_claude_sounds.status() reporting.

Regression guard: the events/{type}/default.wav files shipped in the repo are
INERT. sound_manager._resolve_event_sound() never reads the events directory --
completion/start/approval/error always play the session's identity sound, and
end is always silent. The status() output must therefore NOT claim these files
are functional per-event overrides (the old "Event sounds: end(1), error(1)..."
line was a lie about a dead code path).
"""
import sys
from pathlib import Path

import pytest

# Make the installer importable (it lives at the repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import install_claude_sounds  # noqa: E402


@pytest.fixture
def installer_env(tmp_path, monkeypatch):
    """Point the installer's module-level paths at an isolated tmp_path and
    create inert events/{type}/default.wav stubs to mimic a real install."""
    sounds_dst = tmp_path / "sounds"
    assignments_dir = sounds_dst / "assignments"
    sounds_dst.mkdir()
    assignments_dir.mkdir()

    events_dir = sounds_dst / "events"
    for event_type in ("error", "approval", "end"):
        d = events_dir / event_type
        d.mkdir(parents=True)
        (d / "default.wav").write_bytes(b"\x00")

    monkeypatch.setattr(install_claude_sounds, "SOUNDS_DST", sounds_dst)
    monkeypatch.setattr(install_claude_sounds, "ASSIGNMENTS_DIR", assignments_dir)
    # Non-existent settings so the hooks branch stays quiet.
    monkeypatch.setattr(
        install_claude_sounds, "SETTINGS_PATH", tmp_path / "nonexistent-settings.json"
    )
    return {"sounds_dst": sounds_dst, "events_dir": events_dir}


def test_status_does_not_claim_events_are_active_overrides(installer_env, capsys):
    """The inert events/{type}/default.wav files must not be reported as active
    per-event sounds."""
    install_claude_sounds.status()
    out = capsys.readouterr().out

    # The old misleading per-event count lines must be gone. These files are
    # never played as per-event overrides by sound_manager.
    assert "Event sounds: end(1)" not in out
    assert "Event sounds:" not in out
    # Any per-event count formatting like "end(1)" is misleading -- forbid it.
    for token in ("end(1)", "error(1)", "approval(1)"):
        assert token not in out


def test_status_accurately_describes_event_override_support(installer_env, capsys):
    """If status() mentions event overrides at all, it must tell the truth:
    they are not supported; all events use the identity sound and end is silent.
    """
    install_claude_sounds.status()
    out = capsys.readouterr().out.lower()

    if "event" in out and "override" in out:
        assert "not supported" in out
        assert "identity sound" in out
        assert "silent" in out


def test_status_still_reports_core_sections(installer_env, capsys):
    """Sanity: the rest of the status report is untouched."""
    install_claude_sounds.status()
    out = capsys.readouterr().out

    assert "Claude Code Session Sounds -- Status" in out
    assert "Sounds directory:" in out
    assert "Active sessions:" in out
