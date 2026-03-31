"""Keep the terminal title pinned to the assigned sound for Codex sessions."""
from __future__ import annotations

import os
import sys
import time


def _emit_title(title: str) -> None:
    sequence = f"\033]0;{title}\007\033]2;{title}\007"
    try:
        sys.stderr.write(sequence)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        with open("CONOUT$", "w") as con:
            con.write(sequence)
            con.flush()
    except Exception:
        pass


def _parent_alive(ppid: int) -> bool:
    """Check if the parent process is still running."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, ppid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(ppid, 0)
            return True
        except OSError:
            return False


title = os.environ.get("CLAUDE_SOUND_TITLE", "") or (
    sys.argv[1] if len(sys.argv) > 1 else ""
)

if not title:
    sys.exit(0)

ppid = os.getppid()

try:
    while True:
        if not _parent_alive(ppid):
            break
        _emit_title(title)
        time.sleep(2)
except (KeyboardInterrupt, BrokenPipeError):
    pass
