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

log = sound_manager.log

CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"


# ---------------------------------------------------------------------------
# Terminal title
# ---------------------------------------------------------------------------

def _emit_title(title: str) -> None:
    """Write ANSI title sequences to stderr and CONOUT$ (Windows)."""
    sequence = f"\033]0;{title}\007\033]2;{title}\007"
    try:
        sys.stderr.write(sequence)
        sys.stderr.flush()
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            with open("CONOUT$", "w") as con:
                con.write(sequence)
                con.flush()
        except Exception:
            pass


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
    sound_manager._cleanup_stale()
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

def _find_codex_rollout(launch_epoch: float, timeout: float = 60.0) -> Path | None:
    """Find the rollout JSONL for the current Codex session.

    Strategy: try the DB first (fast), then fall back to scanning the
    sessions directory for recently-created JSONL files (robust against
    DB transaction delays).
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
                    "ORDER BY created_at DESC LIMIT 1"
                )
                row = c.fetchone()
            if row and row[0]:
                rollout = Path(row[0])
                if row[1] >= launch_epoch and rollout.exists():
                    log.debug("launcher: found rollout via DB %s", rollout)
                    return rollout
        except Exception as exc:
            log.debug("launcher: rollout query error: %s", exc)

        # Strategy 2: filesystem scan (robust against uncommitted transactions)
        try:
            now = time.time()
            candidates = []
            for jsonl in sessions_dir.rglob("rollout-*.jsonl"):
                try:
                    mtime = jsonl.stat().st_mtime
                    if mtime >= launch_epoch:
                        candidates.append((mtime, jsonl))
                except OSError:
                    pass
            if candidates:
                candidates.sort(reverse=True)
                rollout = candidates[0][1]
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
                            if (
                                event.get("type") == "event_msg"
                                and event.get("payload", {}).get("type")
                                == "task_complete"
                            ):
                                log.debug("watcher: task_complete -> play + title")
                                sound_manager.play(session_id)
                                _emit_title(title)
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
    result = _pick_sound()
    if not result:
        return subprocess.call(_agent_cmd(agent, "", args))

    choice, reservation_id = result
    title = choice["name"]

    # Environment for Claude hooks (assign reads these)
    os.environ["CLAUDE_SOUND_TITLE"] = title
    os.environ["CLAUDE_SOUND_RESERVATION"] = reservation_id
    os.environ["SESSION_SOUND_HOST"] = agent

    # Set terminal title immediately
    _emit_title(title)

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

    launch_epoch = time.time() - 2  # buffer for process startup latency
    cmd = _agent_cmd(agent, title, args)
    log.debug("launcher: %s -> %s", agent, cmd)

    stop_event = threading.Event()
    watcher_thread = None

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

        if agent == "codex":
            sound_manager.release(reservation_id)
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
