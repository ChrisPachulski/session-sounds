"""Tests for title_hook._write_spinner_state.

Regression coverage for an unbound-variable bug: the original inline write
block referenced ``tmp`` in its except handler's ``os.unlink(tmp)`` cleanup even
when ``tempfile.mkstemp`` itself raised (so ``tmp`` was never bound). That
UnboundLocalError (a NameError subclass) was swallowed by an outer
``except Exception``/``except OSError``, so a disk-full / read-only / deleted
ASSIGNMENTS_DIR left the spinner state file unwritten with zero diagnostic
trace -- the terminal tab spinner got stuck on a stale state.

The fix (mirroring agent_launcher._write_spinner_state) narrows the inner
unlink's scope so it can only run when mkstemp already returned a real path, and
lets an OUTER ``except OSError: pass`` handle the mkstemp-itself-fails case with
no unbound-variable path.

The primary gate here is STRUCTURAL (AST-based): the except handler directly
attached to the ``try`` that contains the ``tempfile.mkstemp(...)`` call must
NOT reference the name ``tmp`` at all -- because that handler can fire *before*
``tmp`` is ever bound. That property is false on the buggy source (the
``os.unlink(tmp)`` cleanup sits in the outer except alongside the fallible
mkstemp call) and true after the fix (the outer except is a bare ``pass``; the
``os.unlink(tmp)`` lives only in the inner except, where ``tmp`` is guaranteed
bound). The runtime tests below are a secondary belt-and-suspenders check.
"""

import ast
import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "sounds"
sys.path.insert(0, str(SOUNDS_DIR))

import title_hook


# --------------------------------------------------------------------------- #
# Structural (AST) gate -- the real red->green discriminator.
# --------------------------------------------------------------------------- #


def _call_is_mkstemp(node):
    """True if ``node`` is a call to ``*.mkstemp(...)`` (e.g. tempfile.mkstemp)."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mkstemp"
    )


def _find_write_spinner_state_def(module_ast):
    """Return the ``_write_spinner_state`` FunctionDef node, or None if inline."""
    for node in ast.walk(module_ast):
        if isinstance(node, ast.FunctionDef) and node.name == "_write_spinner_state":
            return node
    return None


def _find_mkstemp_try(scope_node):
    """Return the ``ast.Try`` whose *direct body* contains a mkstemp call.

    This is precisely "the try the outer except is attached to at the nesting
    level where the mkstemp assignment could have failed to execute": we require
    the mkstemp call to appear inside a statement that is a DIRECT child of the
    try body (not merely somewhere in a more deeply nested try), so the handlers
    we inspect are the ones that fire when mkstemp raises.
    """
    for node in ast.walk(scope_node):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if _call_is_mkstemp(sub):
                    return node
    return None


def test_outer_except_never_references_unbound_tmp():
    """RED-BEFORE / GREEN-AFTER structural gate.

    Parse title_hook's AST and locate the ``try`` that contains the
    ``tempfile.mkstemp(...)`` call. Assert that NO ``except`` handler directly
    attached to that try references the name ``tmp``. On the buggy source the
    outer handler contains ``os.unlink(tmp)`` (tmp may be unbound) -> this
    assertion fails. After the fix the outer handler is a bare ``pass`` and the
    ``os.unlink(tmp)`` lives only in the *inner* try (where tmp is bound) ->
    this assertion passes.
    """
    source = inspect.getsource(title_hook)
    module_ast = ast.parse(source)

    # Prefer the extracted helper's scope; fall back to the whole module so the
    # gate still fails on the pre-extraction inline version.
    scope = _find_write_spinner_state_def(module_ast) or module_ast

    mkstemp_try = _find_mkstemp_try(scope)
    assert mkstemp_try is not None, (
        "could not locate a try/except containing tempfile.mkstemp(...) in "
        "title_hook -- the write path was restructured unexpectedly"
    )

    offending = [
        sub.lineno
        for handler in mkstemp_try.handlers
        for sub in ast.walk(handler)
        if isinstance(sub, ast.Name) and sub.id == "tmp"
    ]
    assert offending == [], (
        "the except handler directly attached to the mkstemp try references "
        f"'tmp' (lines {offending}). That handler can fire before mkstemp binds "
        "'tmp', so this is the UnboundLocalError bug. The tmp cleanup must live "
        "only in an inner try/except where mkstemp already succeeded."
    )


def test_helper_is_importable_without_stdin_side_effects():
    """Importing title_hook must expose ``_write_spinner_state`` as a top-level
    function and must NOT read stdin at import time.

    The module-level script logic now lives in ``main()`` guarded by
    ``if __name__ == '__main__'``; importing the module (as this test file
    already did above without hanging on stdin) proves the side effects were
    moved out of import scope.
    """
    assert callable(getattr(title_hook, "_write_spinner_state", None))
    assert callable(getattr(title_hook, "main", None))


# --------------------------------------------------------------------------- #
# Behavioral tests -- belt-and-suspenders.
# --------------------------------------------------------------------------- #


def test_write_spinner_state_happy_path(tmp_path):
    """Baseline: state file is written atomically with the given contents."""
    state_file = tmp_path / ".spinner_res123"
    title_hook._write_spinner_state(state_file, "idle")
    assert state_file.read_text() == "idle"
    # No stray temp files left behind on success.
    assert not list(tmp_path.glob(".spintmp_*"))


def test_mkstemp_oserror_does_not_raise_unbound(tmp_path, monkeypatch):
    """When mkstemp raises OSError, the helper must not raise (esp. not
    UnboundLocalError/NameError) and must write nothing.

    This is the runtime mirror of the structural gate. On its own it does not
    *discriminate* the fix from a bare-except swallow, which is why the AST test
    above is the real gate -- but it pins the observable contract.
    """
    def boom(*args, **kwargs):
        raise OSError("ENOSPC")

    monkeypatch.setattr(title_hook.tempfile, "mkstemp", boom)

    state_file = tmp_path / ".spinner_res123"
    try:
        title_hook._write_spinner_state(state_file, "idle")
    except (UnboundLocalError, NameError) as exc:  # pragma: no cover - failure path
        pytest.fail(f"_write_spinner_state leaked an unbound-name error: {exc!r}")

    # mkstemp never succeeded, so nothing should have been written.
    assert not state_file.exists()


def test_replace_failure_unlinks_only_the_real_temp_path(tmp_path, monkeypatch):
    """When mkstemp SUCCEEDS but os.replace fails, the inner cleanup unlinks the
    real temp path mkstemp returned -- and leaves nothing behind."""
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
    title_hook._write_spinner_state(state_file, "spin")

    assert len(created_paths) == 1
    assert unlink_calls == [created_paths[0]], (
        f"expected unlink of the real temp path {created_paths[0]!r}, "
        f"got {unlink_calls!r}"
    )
    assert not list(tmp_path.glob(".spintmp_*"))
    assert not state_file.exists()


def test_missing_parent_dir_does_not_raise(tmp_path):
    """Directly reproduce the reported failure mode: a non-existent parent dir
    makes mkstemp raise FileNotFoundError (an OSError subclass). The helper must
    swallow it rather than surface an UnboundLocalError from cleanup."""
    missing = tmp_path / "definitely_missing_dir_xyz" / ".spinner_x"
    try:
        title_hook._write_spinner_state(missing, "idle")
    except (UnboundLocalError, NameError) as exc:  # pragma: no cover - failure path
        pytest.fail(f"_write_spinner_state leaked an unbound-name error: {exc!r}")
    assert not missing.exists()
