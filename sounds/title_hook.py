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


def _write_spinner_state(state_file, state):
    """Atomically write ``state`` to ``state_file`` via a temp-file + os.replace.

    Mirrors agent_launcher._write_spinner_state: any OSError (disk full,
    ASSIGNMENTS_DIR missing/read-only, permission denied) is swallowed. The
    inner unlink runs ONLY when mkstemp already returned a real ``tmp`` path
    (i.e. the write/close/replace failed after the temp file was created), so
    the cleanup can never reference an unbound name. If mkstemp itself raises,
    the outer handler cleanly no-ops with no UnboundLocalError.
    """
    state_file = Path(state_file)
    try:
        dir_ = str(state_file.parent)
        fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".spintmp_")
        try:
            os.write(fd, state.encode())
            os.close(fd)
            os.replace(tmp, str(state_file))
        except OSError:
            os.unlink(tmp)
    except OSError:
        pass


def main():
    try:
        stdin_data = json.loads(sys.stdin.read())
    except Exception:
        stdin_data = {}

    session_id = stdin_data.get("session_id", "")
    # hook_event_name is NOT in Claude's stdin JSON -- take it from CLI arg instead
    hook_event = sys.argv[1] if len(sys.argv) > 1 else ""

    if not session_id:
        return

    assignment_file = ASSIGNMENTS_DIR / f"{session_id}.json"
    if not assignment_file.exists():
        return

    try:
        assignment = json.loads(assignment_file.read_text())
        name = assignment.get("name", "")
    except Exception:
        name = ""

    if not name:
        return

    # Signal the spinner thread via state file
    state = _EVENT_STATE.get(hook_event, "spin")
    # The spinner thread reads .spinner_{reservation_id}
    # Get reservation_id from the assignment file (stored at assign time)
    reservation_id = assignment.get("reservation_id", "")

    # Write to reservation_id file (spinner thread reads this)
    target_id = reservation_id or session_id
    state_file = ASSIGNMENTS_DIR / f".spinner_{target_id}"
    _write_spinner_state(state_file, state)


if __name__ == "__main__":
    main()
