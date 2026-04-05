---
name: session-sounds
description: Session sound and terminal tab naming system for Codex -- covers watcher-based architecture, sound pool, packs, themes, personal WAV customization, and critical constraints. Use when modifying session-sounds code, troubleshooting sounds or tab naming, or helping users customize their sound setup.
---

# Session Sounds -- Codex

## What This Is

session-sounds assigns each Codex session a unique sound identity -- a name on the terminal tab and a notification tone after every task completion. It solves the "bash, bash, bash" tab problem for power users running multiple sessions.

- **25 copyright-free sounds** ship with the default pool (gitignored WAVs, not tracked in version control)
- **Zero dependencies** -- stdlib only, Python 3.10+
- **Watcher-based** -- Codex hooks are disabled on Windows (`cfg!(windows)` in Rust), so a JSONL watcher thread handles per-response events
- **Personal themes** -- users add their own WAVs to a gitignored personal directory

## Architecture (Codex)

```
PowerShell/bash wrapper -> agent_launcher.py -> codex
     |                          |                   |
     |                          +-> _pick_sound()   +-> Codex writes rollout JSONL
     |                          +-> _claim_reservation() (immediate -- no hooks)
     |                          +-> startup sound
     |                          +-> env vars
     |                          +-> lock file
     |                          +-> spinner thread (10-frame braille at 80ms)
     |                          +-> watcher thread (tails rollout JSONL)
     |
     +-> Shell wrapper only calls: python agent_launcher.py codex @args
```

### Watcher lifecycle

1. **Launcher** calls `_pick_sound()` -> reserves a sound via atomic JSON file
2. **Launcher** claims reservation immediately via `_claim_reservation()` (removes `reserved_at` key) -- unlike Claude, there are no hooks to do this later
3. **Launcher** sets env vars: `CLAUDE_SOUND_TITLE`, `CLAUDE_SOUND_RESERVATION`, `SESSION_SOUND_HOST=codex`
4. **Launcher** plays startup sound in daemon thread, sets terminal title, starts spinner thread
5. **Launcher** holds `.lock_{reservation_id}` open for session lifetime (liveness detection)
6. **Launcher** discovers rollout JSONL via `~/.codex/state_5.sqlite` DB or filesystem scan of `~/.codex/sessions/`
7. **Watcher thread** tails rollout JSONL using `FindFirstChangeNotificationW` (Windows) or polling (elsewhere)
8. On `{"type": "event_msg", "payload": {"type": "task_started"}}` -> spinner activates
9. On `{"type": "event_msg", "payload": {"type": "task_complete"}}` -> `play(completion)` + spinner idles
10. On session exit -> `release()` + rollout claim freed

### No hookSpecificOutput

Since Codex has no hooks, there is no `hookSpecificOutput` injection. The sound name is visible in the terminal tab title but is NOT injected into the Codex session context.

### Codex config requirement

The installer sets `terminal_title = []` under `[tui]` in `~/.codex/config.toml`. Without this, Codex's native 80ms title animation fights with the session-sounds spinner.

### Spinner thread

The launcher runs a daemon thread that animates the terminal tab title:
- **Working**: 10-frame braille spinner at 80ms (matches Codex's own style)
- **Idle**: static `\u25cb Sound Name` (open circle -- visually distinct from Claude's \u2733)

### Codex-specific files (beyond shared inventory)

```
~/.codex/config.toml      # terminal_title = [] under [tui]
~/.codex/skills/session-sounds/
  SKILL.md                # This file (Codex-specific skill)
```

### Rollout discovery

Two strategies, tried in order until one succeeds:
1. **DB query**: `~/.codex/state_5.sqlite` -> `threads` table -> `rollout_path WHERE created_at >= launch_epoch`
2. **Filesystem scan**: `~/.codex/sessions/` -> `rglob("rollout-*.jsonl")` -> newest by mtime

Rollout claims prevent multiple launchers from watching the same JSONL (atomic `O_CREAT|O_EXCL` on `.rollout_claim_{hash}` file).

## Sound Pool & Assignment


### Pool loading priority

1. **Pack system** -- `pack_loader.load_pack_pool()` if `CLAUDE_SOUND_PACK` env var or `~/.claude/sounds/active_pack.txt`
2. **Theme directory** -- WAVs from `~/.claude/sounds/themes/{SESSION_SOUNDS_THEME}/` with optional `theme.json` display name mappings
3. **Legacy fallback** -- loose WAVs in `~/.claude/sounds/` with `_DISPLAY_NAMES` dict lookup

### Deduplication

Assignments tracked as JSON files in `~/.claude/sounds/assignments/`. Each maps a session ID to a sound. New sessions only pick from unassigned sounds. If all 25 are taken, pool resets.

### Cleanup (NEVER time-based)

- **Lock-file liveness** (primary): each launcher holds `.lock_{id}` open. If the lock file can be deleted (Windows: `PermissionError` = alive) or locked (Unix: `fcntl.flock` fails = alive), owner is alive.
- **Pressure-based eviction** (backstop): when fewer than 5 sounds remain available, oldest assignments by mtime are evicted. NEVER use time-based cleanup -- sessions can sit idle overnight.
- **Orphaned reservations**: removed after 2 minutes (pick ran, agent never started).

### Self-healing play()

When `play()` finds no assignment for a session_id, it auto-assigns a random available sound. This handles ghost sessions and leaked orphans.

## Packs & Themes

### Sound packs

Directories under `~/.claude/sounds/packs/` with a `pack.json` manifest. Five ship with the repo:

| Pack | Mode | Platform | Description |
|------|------|----------|-------------|
| `default` | bundled | all | 25 copyright-free sounds |
| `windows-native` | system | windows | Windows Media system sounds |
| `macos-native` | platform-native | darwin | /System/Library/Sounds |
| `linux-native` | platform-native | linux | freedesktop XDG sounds |
| `recipe-retro` | recipe | all | Template for user arcade pack |

Activate: `python pack_loader.py activate windows-native`
Deactivate: `python pack_loader.py deactivate`

### Themes

Themes live in `~/.claude/sounds/themes/{name}/` with an optional `theme.json`:

```json
{
    "schema_version": 1,
    "name": "Personal",
    "description": "My custom sounds",
    "author": "you",
    "sounds": {
        "filename_stem": "Display Name"
    }
}
```

Sounds not in the `sounds` dict auto-title from filename: `cool_cat.wav` -> "Cool Cat".

Activate: `export SESSION_SOUNDS_THEME=personal` (or set in `~/.claude/sounds/config.json`).

### Event sounds

| Event | When | Resolution |
|-------|------|------------|
| completion | Agent finishes response | Always primary sound |
| error | API error or failure | Per-sound variant -> default.wav -> primary |
| approval | Needs user permission | Per-sound variant -> default.wav -> primary |
| end | Session closes | Per-sound variant -> default.wav -> silence |

Three synthesized event defaults ship with the repo (tracked in git -- non-copyrighted):
- `events/error/default.wav`
- `events/approval/default.wav`
- `events/end/default.wav`

## Personal Sound Customization

Users can add their own sounds. **Personal WAVs are always gitignored** -- they never leave the user's machine.

### Quick method: drop WAVs

1. Drop `.wav` files into `~/.claude/sounds/` (legacy) or `~/.claude/sounds/themes/personal/`
2. Filenames become display names: `cool_cat.wav` -> "Cool Cat"
3. Optionally create `themes/personal/theme.json` for custom display names
4. Set `SESSION_SOUNDS_THEME=personal` if using themes

### From YouTube: extract_clip.py

The `tools/` directory (repo only, not installed) has `extract_clip.py` for phrase-boundary-aware clip extraction:

```bash
# 1. Download
yt-dlp -x -o "source.%(ext)s" "https://youtube.com/watch?v=..."

# 2. Convert to WAV (do NOT use -ss on webm -- produces silence)
ffmpeg -y -i source.webm -ar 44100 -ac 1 source.wav

# 3. Extract candidates (auto-snaps to phrase boundaries)
python tools/extract_clip.py source.wav 5 20 my_sound.wav --candidates 3 --boost 4

# 4. Listen, pick the best, move to sounds/
mv my_sound.wav ~/.claude/sounds/themes/personal/
```

**Critical**: never use ffmpeg `-ss` on webm files -- it produces silent output. Convert the full file first, then cut with Python.

### Sound file requirements

- WAV format, 44100 Hz, mono, 16-bit PCM
- Under 5 seconds
- Sparse/staccato sounds need full-scale peak (32767) for audibility
- Continuous sounds at 32767 will be painfully loud -- normalize to ~16383 peak
- 80ms fade-in, 150ms fade-out on clips >= 4.4s

## Critical Constraints

### 1. Codex hooks are disabled on Windows

`cfg!(windows)` in the Rust binary. The watcher thread is the ONLY way to detect task completion on Windows. Do not attempt to configure Codex hooks.

### 2. Codex title must be disabled

`~/.codex/config.toml` must have `terminal_title = []` under `[tui]`. Without this, Codex's native title animation fights the spinner at 80ms frequency.

### 3. Codex claims reservations immediately

Unlike Claude (which claims via `assign()` hook), Codex launcher calls `_claim_reservation()` synchronously before launch. The reservation file has its `reserved_at` key removed in place.

### 4. Pool cleanup is pressure-based, NOT time-based

NEVER use time-based stale cleanup. Sessions can sit idle 10+ hours overnight.

### 5. pick() must NEVER read from stdin

`pick` is called from `agent_launcher.py`, not from a hook. If it reads stdin, the launcher hangs.

### 6. Rollout claim prevents duplicate watchers

Each launcher atomically claims its rollout JSONL via `O_CREAT|O_EXCL` on `.rollout_claim_{hash}`. Multiple launchers watching the same rollout would double-fire sounds.

### 7. Kill switch

`SESSION_SOUNDS_DISABLED=1` or `config.json {"enabled": false}` disables everything.

### 8. VS Code requires `${sequence}`

`terminal.integrated.tabs.title` must be set to `${sequence}` in VS Code settings. Without this, ANSI title sequences are ignored. Side effect: MCP server startup causes brief title cycling -- expected and acceptable.

## Gitignore Rules

The `.gitignore` in the repo root ensures:

**NEVER tracked (gitignored):**
- `sounds/*.wav` -- all session sound WAV files
- `sounds/themes/**/*.wav` -- all theme WAV files (including personal)
- `assignments/*.json` -- runtime session state

**Tracked (exceptions):**
- `!sounds/events/*/default.wav` -- synthesized event defaults (non-copyrighted)
- All `.py` source files
- All `pack.json` manifests
- `theme.json` configs and `theme.json.example`
- `pack_schema.json`

**Users who clone this repo get:**
- All Python source code
- Pack manifests and theme configs
- Synthesized event default WAVs
- The `personal/` theme directory with `.gitkeep` and `theme.json.example`
- Zero session sound WAVs (they bring their own or generate them)

## File Inventory (After Install)

```
~/.claude/sounds/
  agent_launcher.py       # Entry point -- launches claude or codex
  sound_manager.py        # Sound pool, assignment, playback, event resolution
  pack_loader.py          # Pack discovery, validation, activation
  native_pack_loader.py   # Platform-native system sound packs
  pack_schema.json        # JSON Schema for pack.json manifests
  terminal_title.py       # Per-terminal title setter (10+ terminals)
  title_hook.py           # Claude hook for spinner state signaling
  tool_context.py         # Command parser and error/outcome detection
  status_line.py          # Claude Code status bar integration
  config.json             # {"enabled": true, "theme": "default"}
  assignments/            # Per-session assignment files (auto-managed)
  events/                 # Event-type sounds
    error/default.wav
    approval/default.wav
    end/default.wav
  packs/                  # Sound pack manifests
  themes/
    default/theme.json
    personal/             # User's custom sounds (gitignored)
      .gitkeep
      theme.json.example
  *.wav                   # Sound pool files (local-only)

~/.claude/skills/session-sounds/
  SKILL.md                # This file (installed by installer)

~/.claude/settings.json   # Hooks: SessionStart, Stop, Notification, StopFailure, SessionEnd
```

## Terminal Title Dispatch

`terminal_title.py` detects the terminal and uses the best mechanism:

| Terminal | Method |
|----------|--------|
| Windows | SetConsoleTitleW + CONOUT$ OSC |
| Windows Terminal | CONOUT$ OSC + SetConsoleTitleW |
| VS Code | OSC via hooks (requires `${sequence}` setting) |
| Kitty | `kitten @ set-tab-title` IPC |
| WezTerm | `wezterm cli set-tab-title` IPC |
| iTerm2 | OSC 1 via /dev/tty |
| tmux | `tmux rename-window` + DCS passthrough |
| Terminal.app | OSC 1 + OSC 2 via /dev/tty |
| Ghostty | OSC 0 via /dev/tty |
| JetBrains | OSC 0 via /dev/tty (requires manual setting) |
| Generic Unix | /dev/tty OSC 0 + stderr fallback |
