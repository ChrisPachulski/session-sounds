"""Hook script that signals the spinner thread to change state.

On Stop/SessionEnd: writes 'idle' -> spinner shows static title
On UserPromptSubmit/SessionStart: writes 'spin' -> spinner animates

Also emits the title directly as a fallback (for agents without a spinner thread).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import terminal_title

ASSIGNMENTS_DIR = Path.home() / ".claude" / "sounds" / "assignments"

# Map hook events to spinner states
_EVENT_STATE = {
    "Stop": "idle",
    "SessionEnd": "idle",
    "SessionStart": "spin",
    "UserPromptSubmit": "spin",
    "Notification": "idle",
    "StopFailure": "idle",
}

try:
    stdin_data = json.loads(sys.stdin.read())
except Exception:
    stdin_data = {}

session_id = stdin_data.get("session_id", "")
# hook_event_name is NOT in Claude's stdin JSON -- take it from CLI arg instead
hook_event = sys.argv[1] if len(sys.argv) > 1 else ""

if session_id:
    assignment_file = ASSIGNMENTS_DIR / f"{session_id}.json"
    if assignment_file.exists():
        try:
            assignment = json.loads(assignment_file.read_text())
            name = assignment.get("name", "")
        except Exception:
            name = ""

        if name:
            # Signal the spinner thread via state file
            state = _EVENT_STATE.get(hook_event, "spin")
            # The spinner thread reads .spinner_{reservation_id}
            # Get reservation_id from the assignment file (stored at assign time)
            reservation_id = assignment.get("reservation_id", "")

            # Write to reservation_id file (spinner thread reads this)
            target_id = reservation_id or session_id
            state_file = ASSIGNMENTS_DIR / f".spinner_{target_id}"
            try:
                dir_ = str(ASSIGNMENTS_DIR)
                fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".spintmp_")
                os.write(fd, state.encode())
                os.close(fd)
                os.replace(tmp, str(state_file))
            except OSError:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
