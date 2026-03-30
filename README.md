# Claude Session Sounds

Random notification sounds and named terminal tabs for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions.

Each time you start Claude, a random sound is assigned. The sound plays after every Claude response, and your terminal tab shows the sound name so you can tell sessions apart at a glance.

## What you get

- **25 notification sounds** -- retro game SFX, movie themes, ambient clips
- **Named terminal tabs** -- each session gets a unique name (e.g., "Gotcha", "The Shire", "Pentakill")
- **Works everywhere** -- Windows, macOS, Linux. VS Code integrated terminal, iTerm2, Terminal.app, etc.

## Install

```bash
python install_claude_sounds.py
```

The installer:
1. Copies sound files to `~/.claude/sounds/`
2. Adds hooks to `~/.claude/settings.json` (merged with your existing config)
3. Adds a `claude` shell wrapper to your profile (PowerShell, bash, or zsh)
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

```
Terminal opens -> shell wrapper picks a random sound
                  -> sets terminal tab name via --name flag
                  -> starts background title keeper (ANSI escape loop)
                  -> launches claude

Claude responds -> Stop hook plays the assigned sound
                   -> title hook re-asserts the tab name
                   -> async, non-blocking

Session ends   -> assignment file cleaned up
                   -> sound returned to the pool
```

### Title system

Two complementary mechanisms keep your terminal tab named:

- **title_keeper.py** -- background process started by the shell wrapper, emits ANSI title sequences on a loop. Runs outside Claude's process tree.
- **title_hook.py** -- Claude Code hook that fires on SessionStart and Stop events. Reads the assignment file and pushes the title via stderr and Windows console. Works inside Claude's PTY so VS Code picks it up immediately.

VS Code requires `terminal.integrated.tabs.title` set to `${sequence}` (the installer handles this).

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

Remove the hooks from `~/.claude/settings.json` (the `SessionStart`, `Stop`, and `SessionEnd` entries that reference `sound_manager.py` and `title_hook.py`), delete `~/.claude/sounds/`, and remove the `claude` function from your shell profile.
