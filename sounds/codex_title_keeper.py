"""Keep the terminal title pinned to the assigned sound for Codex sessions."""
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


title = os.environ.get("CLAUDE_SOUND_TITLE", "") or (
    sys.argv[1] if len(sys.argv) > 1 else ""
)

if not title:
    sys.exit(0)

try:
    while True:
        _emit_title(title)
        time.sleep(0.25)
except (KeyboardInterrupt, BrokenPipeError):
    pass
