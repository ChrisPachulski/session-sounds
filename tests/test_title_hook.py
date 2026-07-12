"""Tests for title_hook._write_spinner_state.

Regression coverage for the unbound-variable bug: the original inline write
block referenced ``tmp`` in its except handler's ``os.unlink(tmp)`` cleanup even
when ``tempfile.mkstemp`` itself raised (so ``tmp`` was never bound). That
UnboundLocalError (a NameError subclass) was swallowed by a bare
``except Exception: pass``, so a disk-full / read-only / deleted ASSIGNMENTS_DIR
left the spinner state file unwritten with zero diagnostic trace -- the terminal
tab spinner got stuck on a stale state.

The fix (mirroring agent_launcher._write_spinner_state) narrows the inner
unlink's scope so it can only run when mkstemp already returned a real path, and
lets an outer ``except OSError: pass`` handle the mkstemp-itself-fails case with
no unbound-variable path.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "sounds"
sys.path.insert(0, str(SOUNDS_DIR))

import title_hook


def test_write_spinner_state_happy_path(tmp_path):
    """Baseline: state file is written atomically with the given contents."""
    state_file = tmp_path / ".spinner_res123"
    title_hook._write_spinner_state(state_file, "idle")
    assert state_file.read_text() == "idle"
    # No stray temp files left behind on success.
    assert not list(tmp_path.glob(".spintmp_*"))


def test_mkstemp_oserror_does_not_raise_unbound(tmp_path, monkeypatch):
    """RED GATE: when mkstemp raises OSError, the helper must not raise.

    Against the buggy inline code (before extraction) the except handler
    referenced an unbound ``tmp``, producing UnboundLocalError. The bare
    ``except Exception`` hid it at runtime, but the write silently never
    happened. Here we assert the helper swallows the mkstemp failure WITHOUT
    letting any UnboundLocalError/NameError escape.
    """
    def boom(*args, **kwargs):
        raise OSError("ENOSPC")

    monkeypatch.setattr(title_hook.tempfile, "mkstemp", boom)

    state_file = tmp_path / ".spinner_res123"
    # Must not raise -- in particular must not raise UnboundLocalError/NameError.
    title_hook._write_spinner_state(state_file, "idle")

    # mkstemp never succeeded, so nothing should have been written.
    assert not state_file.exists()


def test_mkstemp_oserror_never_calls_unlink(tmp_path, monkeypatch):
    """When mkstemp raises, os.unlink must NEVER be invoked.

    This pins the exact defect: the old code reached ``os.unlink(tmp)`` with
    ``tmp`` unbound. With the fix, the inner cleanup is unreachable when mkstemp
    fails, so the unlink spy records zero calls.
    """
    def boom(*args, **kwargs):
        raise OSError("EACCES")

    monkeypatch.setattr(title_hook.tempfile, "mkstemp", boom)

    unlink_calls = []
    real_unlink = os.unlink

    def spy_unlink(path, *a, **k):
        unlink_calls.append(path)
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(title_hook.os, "unlink", spy_unlink)

    state_file = tmp_path / ".spinner_res123"
    title_hook._write_spinner_state(state_file, "idle")

    assert unlink_calls == [], (
        "os.unlink was called even though mkstemp never produced a temp path "
        f"(unbound-variable path re-introduced): {unlink_calls!r}"
    )


def test_replace_failure_unlinks_only_real_temp_path(tmp_path, monkeypatch):
    """When mkstemp SUCCEEDS but os.replace fails, the inner cleanup must unlink
    the real temp path that mkstemp returned -- and nothing else.

    This exercises the surviving unlink branch and proves it only ever receives a
    genuine, existing path (never an unbound name).
    """
    created_paths = []
    real_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, tmp = real_mkstemp(*args, **kwargs)
        created_paths.append(tmp)
        return fd, tmp

    monkeypatch.setattr(title_hook.tempfile, "mkstemp", tracking_mkstemp)

    def boom_replace(*args, **kwargs):
        raise OSError("cross-device replace failed")

    monkeypatch.setattr(title_hook.os, "replace", boom_replace)

    unlink_calls = []
    real_unlink = os.unlink

    def spy_unlink(path, *a, **k):
        unlink_calls.append(path)
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(title_hook.os, "unlink", spy_unlink)

    state_file = tmp_path / ".spinner_res123"
    # Must not raise despite the replace failure.
    title_hook._write_spinner_state(state_file, "spin")

    # Exactly one temp file was created and it is exactly what got unlinked.
    assert len(created_paths) == 1
    assert unlink_calls == [created_paths[0]], (
        f"expected unlink of the real temp path {created_paths[0]!r}, "
        f"got {unlink_calls!r}"
    )
    # Cleanup succeeded: no temp files linger, and the target was never written.
    assert not list(tmp_path.glob(".spintmp_*"))
    assert not state_file.exists()


def test_write_helper_never_raises_unbound_error_directly():
    """Direct unit assertion: monkeypatch-free structural guard.

    Call the helper against a non-existent parent directory so mkstemp raises
    FileNotFoundError (an OSError subclass). The helper must swallow it rather
    than surface an UnboundLocalError from the cleanup path.
    """
    missing = Path(tempfile.gettempdir()) / "definitely_missing_dir_xyz" / ".spinner_x"
    # Should not raise anything (in particular not UnboundLocalError).
    title_hook._write_spinner_state(missing, "idle")


# --- subprocess integration: mirrors the reported reproduction ---------------

_SUBPROC_SNIPPET = r"""
import os, sys, json, tempfile
from pathlib import Path
sys.path.insert(0, r"{sounds}")

# Redirect ASSIGNMENTS_DIR to the test dir and drop a valid assignment.
import title_hook
assignments = Path(r"{assignments}")
title_hook.ASSIGNMENTS_DIR = assignments

session_id = "sess-abc"
(assignments / (session_id + ".json")).write_text(json.dumps(
    {{"name": "Test Sound", "reservation_id": "res-xyz"}}
))

# Force mkstemp to fail like a full/read-only disk.
def boom(*a, **k):
    raise OSError("ENOSPC")
title_hook.tempfile.mkstemp = boom

sys.argv = ["title_hook.py", "Stop"]

class FakeStdin:
    def read(self):
        return json.dumps({{"session_id": session_id}})
sys.stdin = FakeStdin()

# Must complete without raising (exit 0), writing nothing.
title_hook.main()
spinner = assignments / ".spinner_res-xyz"
print("SPINNER_EXISTS", spinner.exists())
print("MAIN_RETURNED_CLEAN")
"""


def test_main_survives_mkstemp_failure_via_subprocess(tmp_path):
    """End-to-end mirror of the bug report: a valid assignment on stdin with a
    reservation_id, a mkstemp that always raises OSError. The process must exit
    0 and simply not create the spinner file -- crucially, without an
    UnboundLocalError crashing the handler.
    """
    snippet = _SUBPROC_SNIPPET.format(
        sounds=str(SOUNDS_DIR), assignments=str(tmp_path)
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"title_hook.main crashed on mkstemp failure:\n{proc.stderr}"
    )
    assert "UnboundLocalError" not in proc.stderr, proc.stderr
    assert "NameError" not in proc.stderr, proc.stderr
    assert "MAIN_RETURNED_CLEAN" in proc.stdout, proc.stdout
    assert "SPINNER_EXISTS False" in proc.stdout, proc.stdout
    assert not (tmp_path / ".spinner_res-xyz").exists()
