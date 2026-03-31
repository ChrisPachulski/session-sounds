"""Set the terminal tab title once Claude finishes starting.

Polls for a .ready signal file written by assign() when the SessionStart
hook completes. Sets the title once and exits. No loops, no fighting.
"""
import os
import sys
import time
from pathlib import Path

title = os.environ.get("CLAUDE_SOUND_TITLE", "")
if not title:
    sys.exit(0)

ready_file = Path.home() / ".claude" / "sounds" / ".ready"
ppid = os.getppid()

# Clear any stale signal
ready_file.unlink(missing_ok=True)

try:
    # Wait for assign() to signal readiness
    for _ in range(60):
        if ready_file.is_file():
            ready_file.unlink(missing_ok=True)
            time.sleep(0.5)
            sys.stderr.write(f"\033]2;{title}\007")
            sys.stderr.flush()
            break
        try:
            os.kill(ppid, 0)
        except OSError:
            break
        time.sleep(0.5)
except (KeyboardInterrupt, BrokenPipeError):
    pass
