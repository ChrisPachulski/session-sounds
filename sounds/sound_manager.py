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
THEMES_DIR = SOUNDS_DIR / "themes"
CONFIG_FILE = SOUNDS_DIR / "config.json"


def _load_config() -> dict:
    """Load persistent config from ~/.claude/sounds/config.json."""
    if CONFIG_FILE.is_file():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


_config = _load_config()
# Env var overrides config file; config file overrides default
SESSION_SOUNDS_THEME = os.environ.get(
    "SESSION_SOUNDS_THEME", _config.get("theme", "default")
)


def _load_theme_config() -> dict:
    """Load theme.json for the active theme. Returns empty dict on failure."""
    theme_dir = THEMES_DIR / SESSION_SOUNDS_THEME
    config_file = theme_dir / "theme.json"
    if not config_file.is_file():
        log.debug("_load_theme_config: no theme.json at %s", config_file)
        return {}
    try:
        return json.loads(config_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("_load_theme_config: failed to read %s: %s", config_file, exc)
        return {}

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
    """Build sound pool. Active pack takes priority; theme dir as default."""
    # Priority 1: Pack system (if active)
    try:
        from pack_loader import load_pack_pool
        pack_pool = load_pack_pool()
        if pack_pool is not None:
            log.debug("_load_pool: using pack pool (%d sounds)", len(pack_pool))
            return pack_pool
    except ImportError:
        pass
    except Exception as exc:
        log.warning("_load_pool: pack_loader error: %s", exc)

    # Priority 2: Theme directory WAVs (default theme ships with synthesized sounds)
    theme_config = _load_theme_config()
    theme_names = theme_config.get("sounds", {})
    theme_dir = THEMES_DIR / SESSION_SOUNDS_THEME

    pool = []
    if theme_dir.is_dir():
        for wav in sorted(theme_dir.glob("*.wav")):
            if _CANDIDATE_RE.match(wav.stem):
                continue
            name = theme_names.get(wav.stem, wav.stem.replace("_", " ").title())
            pool.append({"file": str(wav), "name": name})

    if pool:
        log.debug("_load_pool: theme '%s' (%d sounds)", SESSION_SOUNDS_THEME, len(pool))
    else:
        log.warning("_load_pool: theme '%s' has no WAVs -- run generate_default_theme.py", SESSION_SOUNDS_THEME)
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


def _is_lock_held(lock_file: Path) -> bool:
    """Check if a lock file is held by a live process.

    Windows: open files cannot be deleted, so a successful unlink means dead.
    Unix: open files CAN be unlinked, so we try an exclusive flock instead.
    """
    if sys.platform == "win32":
        try:
            lock_file.unlink()
            return False  # deleted = owner dead
        except PermissionError:
            return True  # can't delete = owner alive
        except OSError:
            return False
    else:
        import fcntl
        try:
            fd = os.open(str(lock_file), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Got the lock = nobody else holds it = owner dead
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                lock_file.unlink(missing_ok=True)
                return False
            except (OSError, BlockingIOError):
                os.close(fd)
                return True  # can't lock = owner alive
        except OSError:
            return False


def _cleanup_dead_sessions() -> None:
    """Evict assignments whose owning launcher process is dead.

    Each launcher holds .lock_{reservation_id} open for its session lifetime.
    Liveness detection is OS-aware:
    - Windows: open files can't be deleted (PermissionError = alive)
    - Unix: uses fcntl.flock (LOCK_EX|LOCK_NB fails = alive)

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

            if not _is_lock_held(lock_file):
                log.debug("Dead session cleanup: evicting %s (owner dead)", f.name)
                f.unlink(missing_ok=True)
                _cleanup_session_artifacts(res_id)
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
    """Resolve the WAV file for this event type.

    - completion, start, approval, error: always the session's identity sound.
    - end: always silent.
    """
    if event == "end":
        return None

    primary_path = Path(primary_file)
    if not primary_path.is_absolute():
        primary_path = SOUNDS_DIR / primary_file
    return primary_path


_linux_play_cmd: list[str] | None = None
_linux_play_detected: bool = False


def _detect_linux_player() -> list[str] | None:
    """Detect the best audio player on Linux. Cached after first call."""
    global _linux_play_cmd, _linux_play_detected
    if _linux_play_detected:
        return _linux_play_cmd
    _linux_play_detected = True
    try:
        from native_pack_loader import detect_playback_command
        _linux_play_cmd = detect_playback_command()
    except ImportError:
        import shutil
        for candidate in ("paplay", "pw-play", "ogg123", "ffplay", "aplay"):
            if shutil.which(candidate):
                _linux_play_cmd = [candidate]
                break
    log.debug("Linux playback command: %s", _linux_play_cmd)
    return _linux_play_cmd


def _play_sound(wav_path: Path) -> None:
    """Play a sound file using the platform's native audio player."""
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)
    elif sys.platform == "darwin":
        subprocess.run(
            ["afplay", str(wav_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
    else:
        play_cmd = _detect_linux_player()
        if play_cmd is None:
            log.warning("play: no Linux audio player found")
            return
        cmd = list(play_cmd) + [str(wav_path)]
        if play_cmd[0] == "aplay" and "-q" not in play_cmd:
            cmd = ["aplay", "-q", str(wav_path)]
        subprocess.run(
            cmd,
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
    choice = None
    if target.is_file():
        try:
            choice = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("assign: corrupt assignment %s: %s", target.name, exc)
            target.unlink(missing_ok=True)
            choice = None
        if choice is not None:
            log.debug("assign: reusing existing %s", choice["name"])
    if choice is None:

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
        # No assignment = not a session we launched (subagent, ghost, etc). Stay silent.
        log.debug("play: no assignment for %s, skipping (likely subagent)", session_id)
        return

    try:
        choice = json.loads(assignment_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("play: corrupt assignment %s: %s, skipping", assignment_file.name, exc)
        assignment_file.unlink(missing_ok=True)
        return
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
    # Kill switch: env var OR config.json enabled=false disables all sounds.
    # Detached _play_file children inherit the env var, so they also exit early.
    if os.environ.get("SESSION_SOUNDS_DISABLED") or not _config.get("enabled", True):
        sys.exit(0)
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
                p = Path(entry["file"])
                wav_path = p if p.is_absolute() else SOUNDS_DIR / entry["file"]
                if wav_path.is_file():
                    _play_sound(wav_path)
                    log.debug("play-startup: played %s", title)
    else:
        try:
            raw = sys.stdin.read()
            log.debug("stdin raw: %r", raw[:500] if raw else "(empty)")
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
