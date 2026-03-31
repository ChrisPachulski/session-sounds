"""
Background process that periodically re-asserts the terminal tab title.

Launched by the PowerShell claude() wrapper via:
    cmd /c "python title_keeper.py --ppid <PID> < NUL"

Writes its own PID to %TEMP%/claude_title_keeper.pid for cleanup.
Monitors --ppid and self-exits when the parent process dies.
Stdin is redirected to NUL by the cmd /c wrapper so we never compete
with Claude for the console input buffer.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _write_pid_file() -> Path:
    pid_path = Path(os.environ.get("TEMP", "/tmp")) / "claude_title_keeper.pid"
    pid_path.write_text(str(os.getpid()))
    return pid_path


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


def _parse_ppid() -> int | None:
    for i, arg in enumerate(sys.argv):
        if arg == "--ppid" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                return None
    return None


def main() -> None:
    # Close stdin at the Python level (belt + suspenders with cmd /c < NUL)
    try:
        sys.stdin.close()
    except Exception:
        pass

    title = os.environ.get("CLAUDE_SOUND_TITLE", "") or (
        sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else ""
    )
    if not title:
        sys.exit(0)

    ppid = _parse_ppid()
    pid_file = _write_pid_file()

    try:
        while True:
            # Check parent is alive every cycle (if ppid provided)
            if ppid is not None and not _parent_alive(ppid):
                break
            sys.stderr.write(f"\033]2;{title}\007")
            sys.stderr.flush()
            time.sleep(3)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
