"""Tests for agent_launcher's Codex rollout claim mechanism.

The claim mechanism relies on every launcher PROCESS computing the SAME lock
filename for a given rollout path. Python's built-in hash(str) is salted
per-process via PYTHONHASHSEED (Python 3.3+), so deriving the filename from it
silently breaks the cross-process claim: process B never sees process A's lock
and both tail the same rollout. These tests fork fresh interpreters so the
per-process salt is actually exercised (pytest itself runs in one process).
"""

import os
import subprocess
import sys
from pathlib import Path

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "sounds"

# Snippet that prints the claim filename for a fixed rollout path. Imports only
# agent_launcher (which co-locates sound_manager via its own sys.path.insert)
# and calls _claim_file_for_rollout, which merely reads ASSIGNMENTS_DIR to build
# a path -- it does not create the directory or otherwise mutate state.
_SNIPPET = (
    "import sys; sys.path.insert(0, r'%s');"
    "import agent_launcher as a;"
    "from pathlib import Path;"
    "print(a._claim_file_for_rollout(Path('/x/y/rollout-abc.jsonl')).name)"
) % SOUNDS_DIR


def _run_snippet(env=None):
    """Run the filename-printing snippet in a fresh interpreter, return its stdout."""
    return subprocess.run(
        [sys.executable, "-c", _SNIPPET],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def test_claim_filename_stable_across_processes():
    """Two independent processes must derive the SAME claim filename.

    Each subprocess gets its own randomized PYTHONHASHSEED, so a salted hash()
    would produce different filenames here (red on the old code); a deterministic
    digest produces identical filenames (green on the hashlib fix).
    """
    first = _run_snippet()
    second = _run_snippet()
    assert first == second, (
        f"claim filename differs across processes: {first!r} != {second!r} "
        "(rollout-claim lock filename is not deterministic across processes)"
    )


def test_claim_filename_stable_under_distinct_hash_seeds():
    """Deterministic isolation of the salt: pin PYTHONHASHSEED to two distinct
    values and require the derived filename to be identical.

    This is fully reproducible (does not depend on the OS choosing different
    random seeds) and pins down that the salt -- not some other environmental
    difference -- is what the fix neutralizes.
    """
    env0 = {**os.environ, "PYTHONHASHSEED": "0"}
    env1 = {**os.environ, "PYTHONHASHSEED": "1"}
    assert _run_snippet(env=env0) == _run_snippet(env=env1)


def test_claim_filename_format():
    """The claim filename keeps the '.rollout_claim_' + 8-hex-char contract that
    the O_EXCL locking in _claim_rollout/_is_rollout_claimed/_release_rollout
    depends on."""
    import re

    sys.path.insert(0, str(SOUNDS_DIR))
    import agent_launcher

    name = agent_launcher._claim_file_for_rollout(
        Path("/x/y/rollout-abc.jsonl")
    ).name
    assert re.fullmatch(r"\.rollout_claim_[0-9a-f]{8}", name), name
