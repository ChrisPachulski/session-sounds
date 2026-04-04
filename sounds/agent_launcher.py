"""
Agent-agnostic session launcher with sound and title management.

Launches Claude Code or Codex as a child process, managing the full
session lifecycle: pick, assign, play-on-response, title, release.

Claude: hooks handle per-response sound + title natively.
Codex (Windows): watcher thread tails rollout JSONL for task_complete events.

Usage:
    python agent_launcher.py claude [args...]
    python agent_launcher.py codex [args...]
"""

import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# Import co-located sound_manager
sys.path.insert(0, str(Path(__file__).parent))
import sound_manager
import terminal_title

log = sound_manager.log

CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"

# Claude title animation -- exact match of Claude Code's built-in title animation
# Source: cli.js drK=["⠂","⠐"], UrK="✳", bgz=960
CLAUDE_SPINNER_FRAMES = "\u2802\u2810"   # 2 alternating minimal dots (working state)
CLAUDE_IDLE_ICON = "\u2733"              # ✳ eight-spoked asterisk (idle/done state)
CLAUDE_SPINNER_INTERVAL = 0.96           # 960ms per frame

# Codex title animation -- native title disabled in config.toml ([tui] terminal_title = []),
# so our spinner is the sole writer. 10-frame braille at 80ms matches Codex's own style.
CODEX_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
CODEX_IDLE_ICON = "\u25cb"               # ○ open circle -- visually distinct from Claude's ✳
CODEX_SPINNER_INTERVAL = 0.08            # 80ms per frame


# ---------------------------------------------------------------------------
# Terminal title
# ---------------------------------------------------------------------------

def _emit_title(title: str) -> None:
    """Set terminal tab title using the best mechanism for this terminal."""
    terminal_title.emit_title(title)


# ---------------------------------------------------------------------------
# Spinner thread (replaces Claude's built-in title animation)
# ---------------------------------------------------------------------------

def _spinner_state_path(session_id: str) -> Path:
    """Path to the spinner state flag file for this session."""
    return sound_manager.ASSIGNMENTS_DIR / f".spinner_{session_id}"


def _read_spinner_state(session_id: str) -> str:
    """Read spinner state. Returns 'spin' or 'idle'. Default: 'idle'."""
    try:
        return _spinner_state_path(session_id).read_text().strip()
    except (OSError, ValueError):
        return "idle"


def _write_spinner_state(session_id: str, state: str) -> None:
    """Write spinner state from within the launcher process."""
    try:
        import tempfile
        dir_ = str(sound_manager.ASSIGNMENTS_DIR)
        fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".spintmp_")
        os.write(fd, state.encode())
        os.close(fd)
        os.replace(tmp, str(_spinner_state_path(session_id)))
    except OSError:
        pass


def _spinner_thread(
    session_id: str,
    title: str,
    stop_event: threading.Event,
    agent: str = "claude",
) -> None:
    """Daemon thread: animate title when working, show static icon when idle.

    Claude: alternates "⠂ Title" / "⠐ Title" at 960ms, idle "✳ Title"
    Codex:  10-frame braille spinner at 80ms, idle "✳ Title"
            (Codex's native title disabled via config.toml [tui] terminal_title = [])
    """
    if agent == "codex":
        frames, idle_icon, interval = CODEX_SPINNER_FRAMES, CODEX_IDLE_ICON, CODEX_SPINNER_INTERVAL
    else:
        frames, idle_icon, interval = CLAUDE_SPINNER_FRAMES, CLAUDE_IDLE_ICON, CLAUDE_SPINNER_INTERVAL

    idx = 0
    while not stop_event.is_set():
        state = _read_spinner_state(session_id)
        if state == "idle":
            _emit_title(f"{idle_icon} {title}")
        else:
            frame = frames[idx % len(frames)]
            _emit_title(f"{frame} {title}")
            idx += 1
        stop_event.wait(interval)
    # Final static title on shutdown
    _emit_title(f"{idle_icon} {title}")


# ---------------------------------------------------------------------------
# Sound picking (returns data instead of printing like pick())
# ---------------------------------------------------------------------------

def _pick_sound() -> tuple[dict[str, str], str] | None:
    """Pick an available sound and reserve it. Returns (choice, reservation_id) or None."""
    pool = sound_manager._load_pool()
    if not pool:
        log.warning("launcher: empty pool")
        return None

    sound_manager.ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)
    sound_manager._cleanup_dead_sessions()
    sound_manager._cleanup_if_pressured(pool)
    sound_manager._cleanup_orphaned_reservations()

    assigned = sound_manager._get_assigned_files()
    available = [s for s in pool if s["file"] not in assigned]
    if not available:
        available = pool

    choice = random.choice(available)
    reservation_id = str(uuid.uuid4())
    reservation_file = sound_manager.ASSIGNMENTS_DIR / f"{reservation_id}.json"
    reservation_file.write_text(json.dumps({**choice, "reserved_at": time.time()}))
    log.debug("launcher pick: %s (%s) -> %s", choice["name"], choice["file"], reservation_id)
    return choice, reservation_id


def _claim_reservation(reservation_id: str) -> None:
    """Claim a reservation for Codex by removing reserved_at (prevents orphan cleanup)."""
    path = sound_manager.ASSIGNMENTS_DIR / f"{reservation_id}.json"
    if path.is_file():
        data = json.loads(path.read_text())
        data.pop("reserved_at", None)
        path.write_text(json.dumps(data))
        log.debug("launcher: claimed reservation %s", reservation_id)


# ---------------------------------------------------------------------------
# Codex rollout discovery
# ---------------------------------------------------------------------------

def _claim_file_for_rollout(rollout: Path) -> Path:
    """Return the claim lock file path for a given rollout."""
    # Use a hash of the rollout path as the lock filename
    rollout_hash = hash(str(rollout)) & 0xFFFFFFFF
    return sound_manager.ASSIGNMENTS_DIR / f".rollout_claim_{rollout_hash:08x}"


def _claim_rollout(rollout: Path) -> bool:
    """Atomically claim a rollout path via O_CREAT|O_EXCL. Returns True if we won."""
    sound_manager.ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)
    claim_file = _claim_file_for_rollout(rollout)
    try:
        fd = os.open(str(claim_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(rollout).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _is_rollout_claimed(rollout: Path) -> bool:
    """Check if a rollout is already claimed by another launcher."""
    return _claim_file_for_rollout(rollout).is_file()


def _release_rollout(rollout: Path) -> None:
    """Release a claimed rollout path."""
    try:
        _claim_file_for_rollout(rollout).unlink(missing_ok=True)
    except OSError:
        pass


def _find_codex_rollout(launch_epoch: float, timeout: float = 60.0) -> Path | None:
    """Find the rollout JSONL for the current Codex session.

    Strategy: try the DB first (fast), then fall back to scanning the
    sessions directory for recently-created JSONL files (robust against
    DB transaction delays). Skips rollouts already claimed by other launchers.
    """
    deadline = time.time() + timeout
    sessions_dir = Path.home() / ".codex" / "sessions"

    while time.time() < deadline:
        # Strategy 1: DB query (fast when transaction is committed)
        try:
            with sqlite3.connect(str(CODEX_STATE_DB), timeout=2) as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT rollout_path, created_at FROM threads "
                    "WHERE created_at >= ? ORDER BY created_at DESC",
                    (launch_epoch,),
                )
                for row in c.fetchall():
                    if row[0]:
                        rollout = Path(row[0])
                        if not _is_rollout_claimed(rollout) and rollout.exists():
                            if _claim_rollout(rollout):
                                log.debug("launcher: found rollout via DB %s", rollout)
                                return rollout
        except Exception as exc:
            log.debug("launcher: rollout query error: %s", exc)

        # Strategy 2: filesystem scan (robust against uncommitted transactions)
        try:
            candidates = []
            for jsonl in sessions_dir.rglob("rollout-*.jsonl"):
                try:
                    mtime = jsonl.stat().st_mtime
                    if mtime >= launch_epoch and not _is_rollout_claimed(jsonl):
                        candidates.append((mtime, jsonl))
                except OSError:
                    pass
            if candidates:
                candidates.sort(reverse=True)
                for _, rollout in candidates:
                    if _claim_rollout(rollout):
                        log.debug("launcher: found rollout via filesystem scan %s", rollout)
                        return rollout
        except Exception as exc:
            log.debug("launcher: filesystem scan error: %s", exc)

        time.sleep(0.5)
    log.warning("launcher: rollout not found within %.0fs", timeout)
    return None


# ---------------------------------------------------------------------------
# Filesystem change waiting (event-driven on Windows, poll elsewhere)
# ---------------------------------------------------------------------------

def _wait_for_file_change(
    path: Path, stop_event: threading.Event, timeout: float = 2.0,
) -> None:
    """Block until the directory containing *path* reports a write, or timeout."""
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes

        FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
        INVALID = ctypes.wintypes.HANDLE(-1).value
        k32 = ctypes.windll.kernel32

        handle = k32.FindFirstChangeNotificationW(
            str(path.parent), False, FILE_NOTIFY_CHANGE_LAST_WRITE,
        )
        if handle == INVALID:
            stop_event.wait(timeout)
            return
        try:
            k32.WaitForSingleObject(handle, int(timeout * 1000))
        finally:
            k32.FindCloseChangeNotification(handle)
    else:
        stop_event.wait(timeout)


# ---------------------------------------------------------------------------
# Codex watcher thread
# ---------------------------------------------------------------------------

def _codex_watcher(
    rollout_path: Path,
    session_id: str,
    title: str,
    stop_event: threading.Event,
) -> None:
    """Tail the Codex rollout JSONL; play sound + refresh title on task_complete."""
    log.debug("watcher: tailing %s", rollout_path)
    file_pos = 0

    while not stop_event.is_set():
        try:
            with open(rollout_path, "r", encoding="utf-8") as f:
                f.seek(file_pos)
                new_data = f.read()
                if new_data:
                    file_pos = f.tell()
                    for line in new_data.splitlines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                            payload_type = (
                                event.get("payload", {}).get("type", "")
                                if event.get("type") == "event_msg"
                                else ""
                            )
                            if payload_type == "task_started":
                                _write_spinner_state(session_id, "spin")
                            elif payload_type == "task_complete":
                                log.debug("watcher: task_complete -> play + idle")
                                sound_manager.play(session_id, event="completion")
                                _write_spinner_state(session_id, "idle")
                        except json.JSONDecodeError:
                            pass
        except Exception as exc:
            log.debug("watcher: read error: %s", exc)

        if not stop_event.is_set():
            _wait_for_file_change(rollout_path, stop_event)

    log.debug("watcher: stopped")


# ---------------------------------------------------------------------------
# Agent command builder
# ---------------------------------------------------------------------------

def _agent_cmd(agent: str, title: str, args: list[str]) -> list[str]:
    """Build the command list to launch the agent binary."""
    if agent == "claude":
        if sys.platform == "win32":
            exe = str(Path.home() / ".local" / "bin" / "claude.exe")
        else:
            exe = "claude"
        cmd = [exe]
        if title:
            cmd += ["--name", title]
        return cmd + args

    if agent == "codex":
        if sys.platform == "win32":
            return ["cmd", "/c", "codex", "--enable", "codex_hooks"] + args
        return ["codex", "--enable", "codex_hooks"] + args

    raise ValueError(f"Unknown agent: {agent}")


# ---------------------------------------------------------------------------
# Main launch orchestrator
# ---------------------------------------------------------------------------

def launch(agent: str, args: list[str]) -> int:
    """Launch an agent with full sound + title lifecycle management."""
    # Kill switch: set SESSION_SOUNDS_DISABLED=1 to bypass all sound/title
    # management and launch the agent directly.  Unset the var to re-enable.
    if os.environ.get("SESSION_SOUNDS_DISABLED"):
        return subprocess.call(_agent_cmd(agent, "", args))
    result = _pick_sound()
    if not result:
        return subprocess.call(_agent_cmd(agent, "", args))

    choice, reservation_id = result
    title = choice["name"]

    # Environment for Claude hooks (assign reads these)
    os.environ["CLAUDE_SOUND_TITLE"] = title
    os.environ["CLAUDE_SOUND_RESERVATION"] = reservation_id
    os.environ["SESSION_SOUND_HOST"] = agent

    # Disable Claude's built-in title animation -- we handle it ourselves
    # This prevents the startup title fight where Claude overwrites our name
    # Fixed in Claude Code v2.1.79+. Known bug: clears title on exit (#31581)
    os.environ["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"

    # Set terminal title immediately (before agent starts) -- idle state with asterisk
    _emit_title(f"{CLAUDE_IDLE_ICON} {title}")

    # Startup sound in background thread
    wav_path = sound_manager.SOUNDS_DIR / choice["file"]
    if wav_path.is_file():
        threading.Thread(
            target=sound_manager._play_sound, args=(wav_path,), daemon=True
        ).start()

    # Codex: claim reservation now (parent manages lifecycle, no hooks)
    # Claude: leave reservation for hooks to claim via assign()
    if agent == "codex":
        _claim_reservation(reservation_id)
        _write_spinner_state(reservation_id, "spin")

    # Lock file for liveness detection -- held open for the entire session.
    # Windows: open files can't be deleted (cleanup probes via unlink)
    # Unix: uses fcntl.flock (cleanup probes via LOCK_EX|LOCK_NB)
    lock_file = sound_manager.ASSIGNMENTS_DIR / f".lock_{reservation_id}"
    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
    if sys.platform != "win32":
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

    launch_epoch = time.time() - 2  # buffer for process startup latency
    cmd = _agent_cmd(agent, title, args)
    log.debug("launcher: %s -> %s", agent, cmd)

    stop_event = threading.Event()
    watcher_thread = None
    spinner_thread_ref = None
    rollout = None

    # Start spinner thread (runs for entire session, daemon=True)
    # Claude: animated spinner at 960ms. Codex: static title reasserted at 3s.
    spinner_thread_ref = threading.Thread(
        target=_spinner_thread,
        args=(reservation_id, title, stop_event, agent),
        daemon=True,
    )
    spinner_thread_ref.start()

    try:
        proc = subprocess.Popen(cmd)

        # Codex: discover rollout and start watcher
        if agent == "codex":
            rollout = _find_codex_rollout(launch_epoch)
            if rollout:
                watcher_thread = threading.Thread(
                    target=_codex_watcher,
                    args=(rollout, reservation_id, title, stop_event),
                    daemon=True,
                )
                watcher_thread.start()
            else:
                log.warning("launcher: no rollout, per-response sounds disabled")

        # Block until agent exits
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            try:
                rc = proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                proc.terminate()
                rc = 1
        return rc

    finally:
        stop_event.set()
        if watcher_thread and watcher_thread.is_alive():
            watcher_thread.join(timeout=2.0)
        # Clean up spinner state file
        try:
            _spinner_state_path(reservation_id).unlink(missing_ok=True)
        except OSError:
            pass

        # Release lock file (enables dead session detection by future launchers)
        try:
            os.close(lock_fd)
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass

        if agent == "codex":
            sound_manager.release(reservation_id)
            # Release rollout claim so other launchers can reuse the slot
            if rollout:
                _release_rollout(rollout)
        else:
            # Claude hooks handle release; clean up unclaimed reservation
            res_file = sound_manager.ASSIGNMENTS_DIR / f"{reservation_id}.json"
            if res_file.is_file():
                try:
                    data = json.loads(res_file.read_text())
                    if "reserved_at" in data:
                        res_file.unlink(missing_ok=True)
                        log.debug("launcher: cleaned unclaimed reservation")
                except Exception:
                    pass

        log.debug("launcher: cleanup complete")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: agent_launcher.py <claude|codex> [args...]", file=sys.stderr)
        sys.exit(1)
    sys.exit(launch(sys.argv[1], sys.argv[2:]))
