# Claude Session Sounds

Random notification sounds and named terminal tabs for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Codex](https://openai.com/index/introducing-codex/) sessions.

Each time you start a session, a random sound is assigned. The sound plays after every response, and your terminal tab shows the sound name so you can tell sessions apart at a glance.

## What you get

- **25 notification sounds** -- retro game SFX, movie themes, ambient clips
- **Named terminal tabs** -- each session gets a unique name (e.g., "Gotcha", "The Shire", "Pentakill")
- **Agent-agnostic** -- works with both Claude Code and OpenAI Codex
- **Works everywhere** -- Windows, macOS, Linux. VS Code integrated terminal, iTerm2, Terminal.app, etc.

## Requirements

- Python 3.9+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or [Codex](https://openai.com/index/introducing-codex/) installed and on your PATH
- **Audio playback** (for notification sounds):
  - Windows: built-in (winsound)
  - macOS: built-in (afplay)
  - Linux: one of `paplay` (PulseAudio), `pw-play` (PipeWire), or `aplay` (ALSA)

## Install

```bash
git clone https://github.com/ChrisPachulski/session-sounds.git
cd session-sounds
python install_claude_sounds.py
```

The installer:
1. Copies sound files and scripts to `~/.claude/sounds/`
2. Adds hooks to `~/.claude/settings.json` (merged with your existing config)
3. Adds `claude` and `codex` shell wrappers to your profile (PowerShell, bash, or zsh)
4. Configures VS Code terminal tab titles (if VS Code is installed)

Then open a new terminal and type `claude`.

## Adding your own sounds

Drop `.wav` files into `~/.claude/sounds/`. The filename becomes the display name:

```
cool_cat.wav      ->  "Cool Cat"
mario_powerup.wav ->  "Mario Powerup"
my_sound.wav      ->  "My Sound"
```

Or add entries to the `_DISPLAY_NAMES` dictionary in `sound_manager.py` for custom names.

Requirements for sound files:
- WAV format (44100 Hz, mono, 16-bit PCM recommended)
- Under 5 seconds
- Peak volume under 50%

## How it works

The system uses a single Python launcher (`agent_launcher.py`) that manages the full session lifecycle:

```
Shell wrapper calls agent_launcher.py
  -> picks a random sound from the pool
  -> reserves it (prevents duplicates across sessions)
  -> plays startup sound in background thread
  -> sets terminal tab name immediately
  -> launches the agent as a child process

Agent responds
  -> Claude: hooks fire (play sound + refresh tab name)
  -> Codex: watcher thread detects task_complete in rollout JSONL

Session ends
  -> assignment file cleaned up
  -> sound returned to the pool
```

### Claude: hook-based

Claude Code supports [hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) -- shell commands that fire on session events. The installer configures three:

- **SessionStart**: assigns the sound picked by the launcher
- **Stop**: plays the sound after each response (async, non-blocking)
- **SessionEnd**: releases the assignment back to the pool

Hooks also push ANSI title sequences to keep the terminal tab name visible. This works because hooks run as Claude Code subprocesses, so they have PTY access even after the TUI takes over the terminal.

### Codex: JSONL watcher

On Windows, Codex has hooks disabled in the Rust binary (`cfg!(windows)`). The launcher compensates by spawning a watcher thread that:

1. Discovers the active rollout file via `~/.codex/state_5.sqlite`
2. Tails the JSONL for `task_complete` events using filesystem change notifications (`FindFirstChangeNotificationW` on Windows, polling elsewhere)
3. Plays the assigned sound and refreshes the title on each event

### Why hooks, not background processes

Early versions used a background `title_keeper.py` process to loop ANSI title sequences. This failed because once Claude's TUI takes over the terminal, sibling processes lose PTY access -- their writes go nowhere. Hooks work because they are launched *by* Claude Code during its session, inheriting the active PTY.

`title_keeper.py` is retained for legacy compatibility but the launcher + hooks architecture is primary.

### Tab naming

VS Code requires this setting for ANSI title sequences to work:

```json
{
  "terminal.integrated.tabs.title": "${sequence}"
}
```

The installer sets this automatically.

## Core sounds

| Sound | Display Name | Source |
|-------|-------------|--------|
| abouttime.wav | About Time | About Time soundtrack |
| africa.wav | Africa | Toto |
| bond.wav | 007 | James Bond theme |
| civ.wav | New Era | Civilization |
| coolcat.wav | Cool Cat | Cool Cat soundtrack |
| creek.wav | Bluey Creek | Ambient creek |
| dangerzone.wav | Danger Zone | Top Gun |
| gladiator.wav | Elysium | Gladiator soundtrack |
| indy.wav | Indy | Indiana Jones theme |
| jurassic.wav | Life Finds a Way | Jurassic Park |
| lightsaber.wav | Lightsaber | Star Wars |
| mangione.wav | Feels So Good | Chuck Mangione |
| mario_powerup.wav | Mario Mushroom | Super Mario |
| minecraft.wav | Minecraft | Minecraft level up |
| mission.wav | IMF | Mission Impossible |
| mohican.wav | Mohican | Last of the Mohicans |
| pacman.wav | Waka Waka | Pac-Man |
| pentakill.wav | Pentakill | League of Legends |
| pokeball.wav | Gotcha | Pokemon |
| potg.wav | POTG | Overwatch Play of the Game |
| r2d2.wav | R2-D2 | Star Wars |
| scorpion.wav | Scorpion | Mortal Kombat |
| shire.wav | The Shire | Lord of the Rings |
| takeonme.wav | Take On Me | a-ha |
| tetris.wav | Tetris | Tetris theme |

## Uninstall

1. Remove hooks from `~/.claude/settings.json` (the `SessionStart`, `Stop`, and `SessionEnd` entries referencing `sound_manager.py` and `title_hook.py`)
2. Delete `~/.claude/sounds/`
3. Remove the `claude` and `codex` functions from your shell profile

## License

MIT
