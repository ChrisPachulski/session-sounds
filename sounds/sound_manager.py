"""
Per-session sound assignment manager for Claude Code hooks.

Called by hooks with JSON on stdin containing session_id:
    python sound_manager.py assign            # SessionStart: pick & assign sound
    python sound_manager.py play [event]      # Stop: play sound (event: completion|error|approval|end)
    python sound_manager.py release           # SessionEnd: free the assignment

Called directly by shell wrapper (no stdin):
    python sound_manager.py pick              # Pre-pick a sound for --name flag
"""
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

SOUNDS_DIR = Path(__file__).parent
ASSIGNMENTS_DIR = Path.home() / ".claude" / "sounds" / "assignments"
EVENTS_DIR = SOUNDS_DIR / "events"
PRESSURE_THRESHOLD = 5  # Only reclaim slots when fewer than this many available
DEBUG_LOG = Path.home() / ".claude" / "sounds" / "debug.log"
VALID_EVENTS = frozenset({"completion", "error", "approval", "start", "end"})

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

from logging.handlers import RotatingFileHandler

_handler = RotatingFileHandler(str(DEBUG_LOG), maxBytes=1_048_576, backupCount=2)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
logging.root.addHandler(_handler)
logging.root.setLevel(logging.DEBUG)
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


def _cleanup_if_pressured(pool: list[dict[str, str]]) -> None:
    """Reclaim oldest assignments only when pool is under pressure.

    Instead of time-based eviction (which kills overnight sessions),
    only evict when available pool drops below PRESSURE_THRESHOLD.
    Evicts oldest-by-mtime first (most likely to be leaked orphans).
    """
    if not ASSIGNMENTS_DIR.is_dir():
        return
    assigned = _get_assigned_files()
    available_count = len([s for s in pool if s["file"] not in assigned])
    if available_count >= PRESSURE_THRESHOLD:
        return

    candidates = []
    for f in ASSIGNMENTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if "reserved_at" in data:
                continue  # Skip reservations, handled by _cleanup_orphaned_reservations
            candidates.append((f.stat().st_mtime, f))
        except (json.JSONDecodeError, OSError):
            pass

    if not candidates:
        return

    candidates.sort()  # oldest mtime first
    needed = PRESSURE_THRESHOLD - available_count
    for _, f in candidates[:needed]:
        log.debug("Pressure cleanup: evicting %s (pool pressure: %d available, need %d)",
                  f.name, available_count, PRESSURE_THRESHOLD)
        f.unlink(missing_ok=True)


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


def _cleanup_dead_sessions() -> None:
    """Evict assignments whose owning launcher process is dead.

    Each launcher holds .lock_{reservation_id} open for its session lifetime.
    On Windows, open files cannot be deleted -- so if we CAN delete the lock
    file, the launcher is dead and the assignment is orphaned.

    Assignments without lock files (pre-lock-system) are left alone here;
    pressure-based cleanup handles them as a backstop.
    """
    if not ASSIGNMENTS_DIR.is_dir():
        return
    for f in ASSIGNMENTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if "reserved_at" in data:
                continue  # reservation, not assignment -- handled elsewhere

            # Determine reservation_id: stored as field (Claude) or is the filename (Codex)
            res_id = data.get("reservation_id") or f.stem
            lock_file = ASSIGNMENTS_DIR / f".lock_{res_id}"

            if not lock_file.exists():
                continue  # no lock = legacy session, let pressure cleanup handle it

            # Try to delete the lock file -- succeeds only if owner process is dead
            try:
                lock_file.unlink()
                log.debug("Dead session cleanup: evicting %s (owner dead)", f.name)
                f.unlink(missing_ok=True)
                _cleanup_session_artifacts(res_id)
            except PermissionError:
                pass  # Lock held open = owner alive
            except OSError:
                pass
        except (json.JSONDecodeError, OSError):
            pass


def _cleanup_session_artifacts(res_id: str) -> None:
    """Remove spinner state and lock files for a dead session."""
    for prefix in (".spinner_", ".lock_"):
        try:
            (ASSIGNMENTS_DIR / f"{prefix}{res_id}").unlink(missing_ok=True)
        except OSError:
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




def _resolve_event_sound(primary_file: str, event: str) -> Path | None:
    """Resolve the best WAV file for this event type.

    For 'completion' and 'start': always returns the primary sound.
    For 'end': returns event sound or None (silent).
    For others: returns event sound or primary as fallback.

    Resolution: per-sound variant -> universal default -> fallback.
    """
    primary_path = SOUNDS_DIR / primary_file

    if event in ("completion", "start"):
        return primary_path

    # Tier 1: per-sound event variant
    variant = EVENTS_DIR / event / primary_file
    if variant.is_file():
        return variant

    # Tier 2: universal event default
    default = EVENTS_DIR / event / "default.wav"
    if default.is_file():
        return default

    # Tier 3: fallback
    if event == "end":
        return None  # silent exit
    return primary_path  # error/approval fall back to primary


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
        subprocess.run(
            ["aplay", "-q", str(wav_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )


def _play_detached(wav_path: Path) -> None:
    """Play a sound in a fully detached background process.

    The parent returns immediately. Sound plays in a child process that
    survives parent exit. This prevents hooks from blocking Claude Code.
    """
    cmd = [sys.executable, str(Path(__file__)), "_play_file", str(wav_path)]
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        subprocess.Popen(
            cmd,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW,
            startupinfo=si,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )


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
    _cleanup_if_pressured(pool)
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
    print(f"{choice['name']}\t{reservation_id}")


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
                choice["reservation_id"] = reservation_id  # spinner thread reads this key
                # Atomic claim to prevent race with concurrent assign()
                try:
                    fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, json.dumps(choice).encode())
                    os.close(fd)
                except FileExistsError:
                    # Another assign() claimed first -- read what they wrote
                    choice = json.loads(target.read_text())
                reserve_file.unlink(missing_ok=True)
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
                log.warning("assign: pool exhausted, allowing duplicate assignment")
                available = pool
            choice = random.choice(available)
            log.debug("assign: random fallback -> %s", choice["name"])

        if not target.is_file():
            # Atomic create to prevent TOCTOU race with concurrent assign()
            try:
                fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps(choice).encode())
                os.close(fd)
                log.debug("assign: wrote %s -> %s", target.name, choice["name"])
            except FileExistsError:
                log.debug("assign: %s already claimed by concurrent session", target.name)

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


def play(session_id: str, event: str = "completion") -> None:
    """Play the appropriate sound for this session and event type.

    Resolution order:
    1. events/{event}/{primary_stem}.wav  (per-sound variant)
    2. events/{event}/default.wav         (universal event sound)
    3. {primary_stem}.wav                 (primary sound fallback)

    Args:
        session_id: The Claude session ID.
        event: One of "completion", "error", "approval", "end".
               Defaults to "completion" for backward compatibility.
    """
    if event not in VALID_EVENTS:
        log.warning("play: unknown event %r, falling back to completion", event)
        event = "completion"

    log.debug("play: session_id=%s event=%s", session_id, event)
    assignment_file = ASSIGNMENTS_DIR / f"{session_id}.json"

    if not assignment_file.is_file():
        if session_id == "unknown":
            log.debug("play: unknown session_id, skipping")
            return
        # Self-healing: auto-assign if no assignment found (ghost session or stale cleanup)
        log.warning("play: no assignment found for %s, auto-assigning", session_id)
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
    wav_path = _resolve_event_sound(choice["file"], event)
    if wav_path is None:
        log.debug("play: event %s resolved to silence", event)
        return
    log.debug("play: wav_path=%s exists=%s event=%s", wav_path, wav_path.is_file(), event)
    if wav_path.is_file():
        # Touch mtime so pressure cleanup doesn't evict active sessions
        assignment_file.touch()
        _play_sound(wav_path)
        log.debug("play: played %s (event=%s)", choice["name"], event)
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


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""

    if action == "pick":
        pick()
    elif action == "_play_file":
        # Detached child process entry point -- plays a single WAV and exits
        if len(sys.argv) > 2:
            target = Path(sys.argv[2])
            if target.is_file():
                _play_sound(target)
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
            event = sys.argv[2] if len(sys.argv) > 2 else "completion"
            play(session_id, event=event)
        elif action == "release":
            release(session_id)
