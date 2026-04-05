# session-sounds

<!-- Badges -->
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)
![Platform: Windows | macOS | Linux](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-lightgrey?style=flat-square)
![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![Dependencies: zero](https://img.shields.io/badge/dependencies-zero-brightgreen?style=flat-square)

Stop confusing your terminal tabs when running parallel AI sessions.
session-sounds assigns each [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or [Codex](https://openai.com/index/introducing-codex/) session a unique sound identity -- a name on the terminal tab and a notification tone after every response -- so you always know which session is which.

<!-- Demo GIF: record 15-sec showing 3-4 named tabs -->

---

## What you get

- **Named terminal tabs** -- each session gets a unique label (no more "bash", "bash", "bash")
- **Per-response notification sounds** -- distinct audio for each session so you can work elsewhere and know which one finished
- **Event-type sounds** -- different tones for completion, errors, and approval prompts
- **Sound deduplication** -- concurrent sessions never share the same sound; assignments are reclaimed when sessions end
- **Agent-agnostic** -- one launcher for both Claude Code and Codex
- **Zero dependencies** -- stdlib only, Python, nothing to install beyond Python itself
- **Windows-first** -- built and tested on Windows, works on macOS and Linux too

---

## Why session-sounds?

Terminal tab naming is the [most-discussed UX pain point](https://github.com/anthropics/claude-code/issues/7229) for Claude Code power users running multiple sessions. Most tools in this space focus on entertainment or gamification. session-sounds focuses on a simpler problem: telling your sessions apart.

| | session-sounds | Notification-only tools | No tooling |
|---|---|---|---|
| Named terminal tabs | Yes | No | No |
| Sound deduplication across sessions | Yes | No | N/A |
| Event-type sounds (error/approval) | Yes | Some | N/A |
| Windows support | Native | Rare | N/A |
| Codex support (incl. Windows) | Yes | No | N/A |
| Dependencies | 0 (stdlib) | Varies | N/A |
| Approach | Session identity | Entertainment | -- |

---

## Install

```bash
git clone https://github.com/ChrisPachulski/session-sounds.git
cd session-sounds
python install_claude_sounds.py
```

The installer does five things:

1. Copies sound files and scripts to `~/.claude/sounds/`
2. Adds hooks to `~/.claude/settings.json` (merged with your existing config)
3. Adds `claude` and `codex` shell wrappers to your profile (PowerShell, bash, or zsh)
4. Configures VS Code terminal tab titles (if VS Code is installed)
5. Disables Codex's native title animation in `~/.codex/config.toml` (prevents title fight)

Open a new terminal and type `claude`. That is it.

To uninstall or check status:

```bash
python install_claude_sounds.py uninstall
python install_claude_sounds.py status
```

### Quick reference

| Want | Do |
|---|---|
| Disable everything | Set `SESSION_SOUNDS_DISABLED=1` in your environment |
| Re-enable | Unset the variable |
| Disable for one session | `SESSION_SOUNDS_DISABLED=1 claude` |
| Switch to personal sounds | Set `SESSION_SOUNDS_THEME=personal` + add WAVs to `sounds/themes/personal/` |
| Create a new theme | Create `sounds/themes/<name>/` with WAVs + `theme.json` |
| Sounds + tab names | `claude` or `codex` -- just launch normally |

### Requirements

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or [Codex](https://openai.com/index/introducing-codex/) on your PATH
- Audio playback:

| Platform | Player | Notes |
|---|---|---|
| Windows | `winsound` | Built-in, nothing to install |
| macOS | `afplay` | Built-in |
| Linux | `paplay`, `pw-play`, or `aplay` | Auto-detected: PulseAudio, PipeWire, or ALSA |

---

## Adding your own sounds

Drop `.wav` files into `~/.claude/sounds/`. The filename becomes the display name:

```
cool_cat.wav      ->  "Cool Cat"
late_night.wav    ->  "Late Night"
my_sound.wav      ->  "My Sound"
```

For custom display names, add entries to the `_DISPLAY_NAMES` dictionary in `sound_manager.py`.

Sound file requirements:
- WAV format (44100 Hz, mono, 16-bit PCM recommended)
- Under 5 seconds
- Normalize to comfortable volume -- synthesized tones peak around 30-45%, extracted clips may need higher peaks for equivalent perceived loudness

### Sound themes

session-sounds ships with a `default` theme and an empty `personal` theme.

To use your own sounds, copy WAV files into `~/.claude/sounds/themes/personal/`, create a `theme.json` with display name mappings, and set:

```bash
export SESSION_SOUNDS_THEME=personal
```

**theme.json format:**

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

Sounds not listed in the `sounds` dict auto-title from filename: `cool_cat.wav` becomes "Cool Cat".

To disable all sounds temporarily:

```bash
export SESSION_SOUNDS_DISABLED=1
```

### Creating a personal theme from YouTube

session-sounds ships with authoring tools in `tools/` and AI skills in `.claude/skills/` and `.codex/skills/` so Claude Code or Codex can walk you through the process. But here is the manual workflow:

**1. Download the source audio**

```bash
yt-dlp -x -o "source.%(ext)s" "https://youtube.com/watch?v=VIDEO_ID"
```

**2. Convert the full file to WAV (do NOT seek into webm -- it produces silence)**

```bash
ffmpeg -y -i source.webm -ar 44100 -ac 1 source_full.wav
```

**3. Extract phrase-aligned candidates**

```bash
python tools/extract_clip.py source_full.wav 10 20 my_sound.wav --candidates 3 --boost 4
```

This analyzes the waveform energy envelope and snaps cuts to natural phrase boundaries instead of arbitrary timestamps. It produces `my_sound_a.wav`, `my_sound_b.wav`, `my_sound_c.wav` for you to audition.

**4. Pick the best clip and move it to your personal theme**

```bash
cp my_sound_a.wav ~/.claude/sounds/themes/personal/my_sound.wav
```

**5. Optionally register a custom display name**

Create or edit `~/.claude/sounds/themes/personal/theme.json`:

```json
{
    "schema_version": 1,
    "name": "Personal",
    "description": "My custom sounds",
    "author": "you",
    "sounds": {
        "my_sound": "My Custom Sound"
    }
}
```

**6. Activate your personal theme**

```bash
export SESSION_SOUNDS_THEME=personal
```

Or set it permanently in `~/.claude/sounds/config.json`:

```json
{"enabled": true, "theme": "personal"}
```

Personal theme WAV files are gitignored and never leave your machine.

**Sound requirements:** WAV, 44100 Hz, mono, 16-bit PCM, under 5 seconds. Sparse/staccato sounds (keyboard stabs, clicks) need full-scale peak (32767) for audibility. Continuous sounds should peak around 16383.

### Sound packs

Sound packs are directories under `~/.claude/sounds/packs/` with a `pack.json` manifest:

```
packs/
  retro-arcade/
    pack.json
    coin_insert.wav
    extra_life.wav
    ...
```

See the `packs/` directory for examples, including a `windows-native` pack that uses system sounds with zero additional files.

---

## Event-type sounds

Different events play different sounds:

| Event | When | Sound |
|---|---|---|
| Completion | Agent finishes a response | Your session's primary sound |
| Error | API error or hard failure | Short error tone (or primary as fallback) |
| Approval | Agent needs your permission | Rising two-note chime (or primary as fallback) |
| Session end | You close the session | Soft descending tone (or silence) |

Event sounds resolve through a three-tier fallback:
1. Per-sound variant: `events/error/pixel_rise.wav` (custom variant for that session sound)
2. Universal default: `events/error/default.wav` (generic error tone)
3. Primary sound: your session's identity sound (completion always uses this)

Add custom event variants by dropping WAV files into `~/.claude/sounds/events/{event_type}/`.

---

<details>
<summary><strong>How it works</strong></summary>

### Architecture

A single Python launcher (`agent_launcher.py`) manages the full session lifecycle for both agents. No background daemons, no watchdog processes, no external services.

```
You type "claude" or "codex"
         |
         v
  Shell wrapper calls agent_launcher.py
         |
         +-- picks a random sound from the pool
         +-- reserves it (file lock prevents duplicates)
         +-- plays startup sound in background thread
         +-- sets terminal tab name via ANSI escape
         +-- launches the agent as a child process
         |
         v
  Agent runs, you work
         |
         +-- [Claude] hooks fire on each response:
         |      play sound + refresh tab name
         |
         +-- [Codex/Windows] watcher thread tails rollout JSONL:
         |      detects task_complete events via FindFirstChangeNotificationW
         |      plays sound + refreshes tab name
         |
         v
  Session ends
         |
         +-- assignment file deleted
         +-- sound returned to the available pool
```

### Claude Code: hook-based

Claude Code supports [hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) -- shell commands that fire on session events. The installer configures five:

| Hook | Event | Action |
|------|-------|--------|
| `SessionStart` | Session opens | Claims the sound reservation from the launcher |
| `Stop` | After each response | Plays the completion sound (async) + refreshes tab title |
| `Notification` | Needs user approval | Plays the approval sound (async) |
| `StopFailure` | Error or failure | Plays the error sound (async) |
| `SessionEnd` | Session closes | Plays end sound, releases assignment back to pool |

Hooks work for tab naming because they run as Claude Code subprocesses with PTY access -- even after the TUI takes over the terminal, hook processes can still write ANSI title sequences.

### Codex: JSONL watcher + config

On Windows, Codex has hooks disabled in its Rust binary (`cfg!(windows)`). The launcher compensates with two mechanisms:

1. **Title suppression**: The installer sets `terminal_title = []` in `~/.codex/config.toml`, disabling Codex's native title animation. Without this, Codex's 80ms title updates fight with the session-sounds spinner.
2. **Watcher thread**: Discovers the active rollout file via `~/.codex/state_5.sqlite` or filesystem scan, then tails the JSONL for `task_started` and `task_complete` events using `FindFirstChangeNotificationW` (Windows) or polling (elsewhere).

On `task_started`, the spinner activates. On `task_complete`, the sound plays and the spinner stops.

### Tab title icons

Each agent has a distinct idle icon so you can tell them apart at a glance:

| Agent | Thinking | Idle |
|-------|----------|------|
| Claude | `⠂` / `⠐` alternating at 960ms | `✳ Sound Name` |
| Codex | 10-frame braille spinner at 80ms | `○ Sound Name` |

### Sound deduplication

Assignments are tracked as JSON files in `~/.claude/sounds/assignments/`. Each file maps a session ID to a sound. When a new session starts, only unassigned sounds are candidates. If all sounds are taken, the pool resets.

Orphan cleanup uses two mechanisms:
1. **Lock-file liveness detection** (primary): each launcher holds a `.lock_{id}` file open for the session duration. On cleanup, if the lock file can be deleted, the owning process is dead and the assignment is evicted.
2. **Pressure-based eviction** (backstop): when fewer than 5 sounds remain available, the oldest assignments by mtime are evicted first.

### Process detach

Sound playback uses process detachment so hooks return instantly to Claude Code while the sound plays in the background:
- Windows: `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW`
- Unix: `start_new_session=True`

### Tab naming on VS Code

VS Code requires this setting for ANSI title sequences to reach the tab:

```json
{
  "terminal.integrated.tabs.title": "${sequence}"
}
```

The installer sets this automatically. Works in iTerm2, Terminal.app, and Windows Terminal without additional configuration.

### File inventory

```
~/.claude/sounds/
  agent_launcher.py      # Entry point -- launches claude or codex
  sound_manager.py       # Sound pool, assignment, playback, event resolution
  pack_loader.py         # Pack discovery, validation, activation
  native_pack_loader.py  # Platform-native system sound packs
  pack_schema.json       # JSON Schema for pack.json manifests
  terminal_title.py      # Per-terminal title setter (Win/Mac/Linux/tmux/kitty)
  title_hook.py          # Claude hook for terminal title + spinner state
  tool_context.py        # Command parser and error/outcome detection
  status_line.py         # Claude Code status bar integration
  assignments/           # Per-session assignment files (auto-managed)
  events/                # Event-type default sounds
    error/default.wav    # Two-note descending buzzy tone
    approval/default.wav # Rising two-note chime
    end/default.wav      # Soft descending fade
  packs/                 # Sound pack directories + manifests
  *.wav                  # Sound files

tools/                   # Authoring tools (not installed)
  extract_clip.py        # Phrase-boundary-aware clip extractor
  generate_event_sounds.py   # Regenerate default event sounds
  generate_all_sounds.py     # Procedural synthesis reference
```

</details>

---

## License

[MIT](LICENSE)
