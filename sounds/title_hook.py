"""Hook script that pushes terminal-title updates for both Claude and Codex."""
import json
import sys
from pathlib import Path

ASSIGNMENTS_DIR = Path.home() / ".claude" / "sounds" / "assignments"


def _get_title() -> str | None:
    try:
        stdin_data = json.loads(sys.stdin.read())
    except Exception:
        return None

    session_id = stdin_data.get("session_id", "")
    if not session_id:
        return None

    assignment_file = ASSIGNMENTS_DIR / f"{session_id}.json"
    if not assignment_file.exists():
        return None

    try:
        assignment = json.loads(assignment_file.read_text())
    except Exception:
        return None

    return assignment.get("name", "") or None


def _emit_terminal_sequences(title: str) -> None:
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


def set_title() -> None:
    title = _get_title()
    if not title:
        return
    _emit_terminal_sequences(title)


if __name__ == "__main__":
    set_title()
