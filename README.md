# Session Sounds

You run six Claude sessions and four Codex sessions across three terminals. A sound plays. Which session was that? No idea.

Session Sounds fixes this. Every session gets a random notification sound and a named terminal tab. "Lightsaber" plays the lightsaber clip. "Gotcha" plays the Pokeball catch. You hear the sound, glance at the tab, and know exactly which session just finished thinking.

**The 25 included sounds are starter defaults.** Swap them, rename them, delete them, add your own. The system rebuilds the pool on every session start. Zero config beyond the initial install.

## 30-second install

```bash
git clone https://github.com/ChrisPachulski/session-sounds.git
cd session-sounds
python install_claude_sounds.py
```

That's it. The installer copies files to `~/.claude/sounds/`, merges hooks into your existing `settings.json`, drops shell wrappers into your profile, and configures VS Code tab titles. Open a new terminal, type `claude` or `codex`.

## Requirements

- Python 3.9+
- Claude Code or Codex (or both) on your PATH
- Audio playback: Windows and macOS have it built-in. Linux needs one of `paplay`, `pw-play`, or `aplay`.

## Make it yours

Drop a `.wav` into `~/.claude/sounds/`. The filename becomes the display name (`danger_zone.wav` -> "Danger Zone"). Want a custom name? Add an entry to `_DISPLAY_NAMES` in `sound_manager.py`. Want to remove a sound? Delete the `.wav`. The pool rebuilds every time.

Sound specs: WAV, 44100 Hz, mono, 16-bit PCM, under 5 seconds, peak volume under 50%.

### Pick what you want

| Want | Do |
|------|----|
| Sounds + tab names | Install and done (default) |
| Sounds only, no tab names | Remove the `title_hook.py` entries from `settings.json` |
| Tab names only, no sounds | Remove the `Stop` hook |
| Startup sound only | Remove all hooks, keep the shell wrapper |

## How it works

One Python launcher (`agent_launcher.py`) manages the full lifecycle. The shell wrapper calls it, it picks a sound, reserves it so no two concurrent sessions get the same one, plays the startup clip, sets the tab name, and launches your agent.

For **Claude Code**, hooks handle the rest. SessionStart assigns the sound, Stop plays it after each response (async, non-blocking), SessionEnd releases it back to the pool. Hooks run as Claude subprocesses, so they inherit PTY access -- that's how tab titles work even after the TUI takes over.

For **Codex**, hooks are disabled on Windows in the Rust binary (as of March 2026). The launcher compensates with a watcher thread that discovers the rollout JSONL via the Codex state DB (with a filesystem scan fallback), tails it for `task_complete` events, and plays the sound on each one.

## Known quirks

**Codex tab title flickers during "Thinking."** Codex's built-in title system fires an OSC escape sequence on every status change -- including a spinner at 100ms intervals. The watcher reasserts the custom title after each response, so it always lands in the right place. The flicker during thinking is a Codex limitation, not a bug here.

**Sessions idle for 2+ hours lose their assignment.** Stale cleanup runs at 120 minutes. If you step away and come back, the session auto-assigns a new random sound. The original is gone. This is a known tradeoff -- without cleanup, dead sessions would eventually exhaust the pool.

**Rollout discovery takes 10-20 seconds on Codex.** The launcher polls for the rollout file after launch. This is normal. If it times out (60s), per-response sounds are disabled for that session, but the startup sound and tab name still work.

## Default sounds

| File | Tab Name | Source |
|------|----------|--------|
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

Replace any or all of them. The world is your oyster.

## Troubleshooting

Check `~/.claude/sounds/debug.log` first. It logs every pick, assign, play, and release with timestamps.

| Symptom | Likely cause |
|---------|-------------|
| No sound at all | System audio muted or routed to wrong device. The log will show "played" even if you can't hear it. |
| Wrong sound playing | Another session responded. Each session has its own sound -- check which tab name matches. |
| Tab name blank in VS Code | `terminal.integrated.tabs.title` must be set to `${sequence}`. The installer does this, but verify. |
| Codex tab flickers | Normal. Codex overwrites the title during thinking. It reasserts after each response. |
| "rollout not found" in log | Codex took longer than 60s to create the session file. Startup sound and tab name still work. |

## Uninstall

1. Remove the `SessionStart`, `Stop`, and `SessionEnd` hook entries referencing `sound_manager.py` and `title_hook.py` from `~/.claude/settings.json`
2. Delete `~/.claude/sounds/`
3. Remove the `claude` and `codex` functions from your shell profile

## License

MIT
