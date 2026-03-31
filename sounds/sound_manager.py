"""
Per-session sound assignment manager for Claude Code hooks.

Called by hooks with JSON on stdin containing session_id:
    python sound_manager.py assign   # SessionStart: pick & assign sound
    python sound_manager.py play     # Stop: play the assigned sound
    python sound_manager.py release  # SessionEnd: free the assignment

Called directly by shell wrapper (no stdin):
    python sound_manager.py pick     # Pre-pick a sound for --name flag
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

SOUNDS_DIR = Path(__file__).parent
ASSIGNMENTS_DIR = Path.home() / ".claude" / "sounds" / "assignments"
STALE_MINUTES = 120
DEBUG_LOG = Path.home() / ".claude" / "sounds" / "debug.log"

# Production files only: no _a/_b/_c candidates, no src_ sources
_CANDIDATE_RE = re.compile(r"^(src_|.*_[a-c])$", re.IGNORECASE)

# Display names for sounds (filename stem -> title)
_DISPLAY_NAMES: dict[str, str] = {
    "abouttime": "About Time",
    "africa": "Africa",
    "bond": "007",
    "civ": "New Era",
    "coolcat": "Cool Cat",
    "creek": "Bluey Creek",
    "dangerzone": "Danger Zone",
    "gladiator": "Elysium",
    "indy": "Indy",
    "jurassic": "Life Finds a Way",
    "lightsaber": "Lightsaber",
    "mangione": "Feels So Good",
    "mario_powerup": "Mario Mushroom",
    "minecraft": "Minecraft",
    "mission": "IMF",
    "mohican": "Mohican",
    "pacman": "Waka Waka",
    "pentakill": "Pentakill",
    "pokeball": "Gotcha",
    "potg": "POTG",
    "r2d2": "R2-D2",
    "scorpion": "Scorpion",
    "shire": "The Shire",
    "takeonme": "Take On Me",
    "tetris": "Tetris",
}

logging.basicConfig(
    filename=str(DEBUG_LOG),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sound_manager")



def _load_pool() -> list[dict[str, str]]:
    """Build sound pool from production .wav files in the sounds directory."""
    pool = []
    for wav in sorted(SOUNDS_DIR.glob("*.wav")):
        if _CANDIDATE_RE.match(wav.stem):
            continue
        name = _DISPLAY_NAMES.get(wav.stem, wav.stem.replace("_", " ").title())
        pool.append({"file": wav.name, "name": name})
    return pool


def _cleanup_stale() -> None:
    """Remove assignment files older than STALE_MINUTES."""
    if not ASSIGNMENTS_DIR.is_dir():
        return
    cutoff = time.time() - (STALE_MINUTES * 60)
    for f in ASSIGNMENTS_DIR.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                log.debug("Cleaning stale assignment: %s", f.name)
                f.unlink(missing_ok=True)
        except OSError:
            pass


def _cleanup_orphaned_reservations() -> None:
    """Remove reservation files that were never claimed (pick ran, Claude never started)."""
    if not ASSIGNMENTS_DIR.is_dir():
        return
    cutoff = time.time() - 120  # 2 minutes
    for f in ASSIGNMENTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            reserved_at = data.get("reserved_at")
            if reserved_at is not None and reserved_at < cutoff:
                log.debug("Cleaning orphaned reservation: %s", f.name)
                f.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            pass


def _get_assigned_files() -> set[str]:
    """Return set of sound filenames currently assigned to active sessions."""
    assigned = set()
    if ASSIGNMENTS_DIR.is_dir():
        for f in ASSIGNMENTS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                assigned.add(data["file"])
            except Exception:
                pass
    return assigned


def _build_title(sound_name: str) -> str:
    """Build the session title string."""
    return sound_name


def _play_sound(wav_path: Path) -> None:
    """Play a WAV file using the platform's native audio player."""
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)
    elif sys.platform == "darwin":
        subprocess.run(
            ["afplay", str(wav_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
    else:
        for player_cmd in [
            ["paplay", str(wav_path)],
            ["pw-play", str(wav_path)],
            ["aplay", "-q", str(wav_path)],
        ]:
            if shutil.which(player_cmd[0]):
                subprocess.run(
                    player_cmd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
                return
        log.warning("_play_sound: no Linux audio player found (tried paplay, pw-play, aplay)")


def _find_sound_by_name(name: str) -> dict[str, str] | None:
    """Find a sound pool entry whose name matches (case-insensitive)."""
    for entry in _load_pool():
        if entry["name"].lower() == name.lower():
            return entry
    return None


def _find_most_recent_assignment() -> Path | None:
    """Return the most recently modified assignment file, or None."""
    if not ASSIGNMENTS_DIR.is_dir():
        return None
    files = sorted(
        ASSIGNMENTS_DIR.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def pick() -> None:
    """Pick an available sound, reserve it atomically, and output title + reservation ID."""
    pool = _load_pool()
    if not pool:
        log.warning("pick: empty pool")
        return
    ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_stale()
    _cleanup_orphaned_reservations()

    assigned = _get_assigned_files()
    available = [s for s in pool if s["file"] not in assigned]
    if not available:
        available = pool

    choice = random.choice(available)
    reservation_id = str(uuid.uuid4())
    reservation_file = ASSIGNMENTS_DIR / f"{reservation_id}.json"
    reservation_file.write_text(json.dumps({**choice, "reserved_at": time.time()}))
    log.debug("pick: reserved %s (%s) -> %s", choice["name"], choice["file"], reservation_id)
    print(f"{_build_title(choice['name'])}\t{reservation_id}")


def assign(session_id: str) -> None:
    """Assign a sound to this session by claiming its reservation from pick()."""
    log.debug("assign: session_id=%s", session_id)
    ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)

    target = ASSIGNMENTS_DIR / f"{session_id}.json"
    if target.is_file():
        choice = json.loads(target.read_text())
        log.debug("assign: reusing existing %s", choice["name"])
    else:
        choice = None

        # Path 1: Claim reservation written by pick()
        reservation_id = os.environ.get("CLAUDE_SOUND_RESERVATION", "")
        if reservation_id:
            reserve_file = ASSIGNMENTS_DIR / f"{reservation_id}.json"
            if reserve_file.is_file():
                choice = json.loads(reserve_file.read_text())
                choice.pop("reserved_at", None)
                target.write_text(json.dumps(choice))  # claim first
                reserve_file.unlink(missing_ok=True)    # then release reservation
                log.debug("assign: claimed reservation %s -> %s", reservation_id, choice["name"])

        # Path 2: Legacy fallback -- match by CLAUDE_SOUND_TITLE env var
        if choice is None:
            title = os.environ.get("CLAUDE_SOUND_TITLE", "")
            if title:
                log.debug("assign: trying CLAUDE_SOUND_TITLE=%r", title)
                choice = _find_sound_by_name(title)

        # Path 3: Random from available pool
        if choice is None:
            pool = _load_pool()
            if not pool:
                log.warning("assign: empty pool")
                return
            assigned = _get_assigned_files()
            available = [s for s in pool if s["file"] not in assigned]
            if not available:
                log.warning("assign: all sounds assigned, no available slots")
                return
            choice = random.choice(available)
            log.debug("assign: random fallback -> %s", choice["name"])

        if not target.is_file():
            target.write_text(json.dumps(choice))
            log.debug("assign: wrote %s -> %s", target.name, choice["name"])

    if os.environ.get("SESSION_SOUND_HOST") != "codex":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    f"This session's sound is '{choice['name']}'. "
                    f"Do not mention the session name or sound assignment to the user."
                )
            }
        }))


def play(session_id: str) -> None:
    """Play the assigned sound for this session."""
    log.debug("play: session_id=%s", session_id)
    assignment_file = ASSIGNMENTS_DIR / f"{session_id}.json"

    if not assignment_file.is_file():
        if session_id == "unknown":
            log.debug("play: unknown session_id, skipping")
            return
        # Self-healing: auto-assign if no assignment found (ghost session or stale cleanup)
        log.warning("play: no assignment found for %s, auto-assigning", session_id)
        # Call assign internals directly to avoid stdout pollution (assign() prints hookSpecificOutput)
        pool = _load_pool()
        if not pool:
            log.debug("play: auto-assign failed, empty pool")
            return
        ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)
        assigned = _get_assigned_files()
        available = [s for s in pool if s["file"] not in assigned]
        if not available:
            available = pool
        choice = random.choice(available)
        assignment_file.write_text(json.dumps(choice))
        log.debug("play: auto-assigned %s -> %s", session_id, choice["name"])

    choice = json.loads(assignment_file.read_text())
    wav_path = SOUNDS_DIR / choice["file"]
    log.debug("play: wav_path=%s exists=%s", wav_path, wav_path.is_file())
    if wav_path.is_file():
        # Touch mtime so stale cleanup doesn't delete active sessions
        assignment_file.touch()
        _play_sound(wav_path)
        log.debug("play: played %s", choice["name"])
    else:
        log.warning("play: WAV not found: %s", wav_path)


def release(session_id: str) -> None:
    """Free the sound assignment when the session ends."""
    log.debug("release: session_id=%s", session_id)
    assignment_file = ASSIGNMENTS_DIR / f"{session_id}.json"
    try:
        assignment_file.unlink(missing_ok=True)
        log.debug("release: removed %s", assignment_file.name)
    except OSError:
        pass
    _cleanup_stale()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""

    if action == "pick":
        pick()
    elif action == "play-startup":
        title = os.environ.get("CLAUDE_SOUND_TITLE", "")
        if title:
            entry = _find_sound_by_name(title)
            if entry:
                wav_path = SOUNDS_DIR / entry["file"]
                if wav_path.is_file():
                    _play_sound(wav_path)
                    log.debug("play-startup: played %s", title)
    else:
        try:
            raw = sys.stdin.read()
            log.debug("stdin raw: %r", raw[:200] if raw else "(empty)")
            stdin_data = json.loads(raw)
        except (json.JSONDecodeError, EOFError, ValueError) as exc:
            log.debug("stdin parse failed: %s", exc)
            stdin_data = {}
        session_id = stdin_data.get("session_id", "unknown")
        log.debug("action=%s session_id=%s", action, session_id)

        if action == "assign":
            assign(session_id)
        elif action == "play":
            play(session_id)
        elif action == "release":
            release(session_id)
