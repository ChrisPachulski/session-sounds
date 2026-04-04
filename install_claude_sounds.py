#!/usr/bin/env python3
"""
Claude Code Session Sounds -- Installer

Usage:
    python install_claude_sounds.py

Copies sounds, installs hooks, adds shell wrapper, configures VS Code.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SOUNDS_SRC = SCRIPT_DIR / "sounds"
SOUNDS_DST = Path.home() / ".claude" / "sounds"
ASSIGNMENTS_DIR = SOUNDS_DST / "assignments"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
GSD_STATUSLINE_PATH = Path.home() / ".claude" / "hooks" / "gsd-statusline.js"


def _sounds_path() -> str:
    return str(SOUNDS_DST).replace("\\", "/")


def _status_line_command() -> dict:
    """Build the statusLine config entry for settings.json."""
    return {
        "type": "command",
        "command": f'python "{_sounds_path()}/status_line.py"',
    }


def _patch_gsd_statusline() -> bool:
    """Patch the GSD status line JS to include sound name lookup.

    Returns True if the GSD script was found and patched (or already patched).
    Returns False if the GSD script does not exist.
    """
    if not GSD_STATUSLINE_PATH.is_file():
        return False
    try:
        js = GSD_STATUSLINE_PATH.read_text(encoding="utf-8")
    except OSError:
        return False

    # Already patched?
    if "Session sound name lookup" in js:
        return True

    # Find the "// Output" comment line and insert the sound block before it
    marker = "    // Output"
    if marker not in js:
        return False

    sound_block = """    // Session sound name lookup
    let soundTag = '';
    if (session) {
      try {
        const assignFile = path.join(claudeDir, 'sounds', 'assignments', `${session}.json`);
        if (fs.existsSync(assignFile)) {
          const assignment = JSON.parse(fs.readFileSync(assignFile, 'utf8'));
          const soundName = assignment.name || '';
          if (soundName) {
            soundTag = `\\x1b[36m[${soundName}]\\x1b[0m \\u2502 `;
          }
        }
      } catch (e) {
        // Silent fail -- sound lookup is best-effort
      }
    }

"""
    js = js.replace(marker, sound_block + marker)

    # Add ${soundTag} to output lines
    js = js.replace("${gsdUpdate}\\x1b[2m", "${gsdUpdate}${soundTag}\\x1b[2m")

    tmp = GSD_STATUSLINE_PATH.with_suffix(".js.tmp")
    tmp.write_text(js, encoding="utf-8")
    tmp.replace(GSD_STATUSLINE_PATH)
    return True


def _python_cmd() -> str:
    """Return 'python3' on Unix (macOS/Linux ship python3), 'python' on Windows."""
    return "python" if sys.platform == "win32" else "python3"


def _hook_commands() -> dict:
    py = _python_cmd()
    sm = f'{py} "{_sounds_path()}/sound_manager.py"'
    th = f'{py} "{_sounds_path()}/title_hook.py"'
    return {
        "SessionStart": [{"hooks": [
            {"type": "command", "command": f'{sm} assign', "timeout": 5},
            {"type": "command", "command": th, "timeout": 5},
        ]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": f'{sm} play completion', "async": True, "timeout": 10},
            {"type": "command", "command": th, "timeout": 5},
        ]}],
        "Notification": [{"hooks": [
            {"type": "command", "command": f'{sm} play approval', "async": True, "timeout": 5},
        ]}],
        "StopFailure": [{"hooks": [
            {"type": "command", "command": f'{sm} play error', "async": True, "timeout": 5},
        ]}],
        "SessionEnd": [{"hooks": [
            {"type": "command", "command": f'{sm} play end', "async": True, "timeout": 5},
            {"type": "command", "command": f'{sm} release', "timeout": 5},
        ]}],
    }


def _configure_codex_title() -> None:
    """Disable Codex's native terminal title so session-sounds controls it.

    Sets terminal_title = [] under [tui] in ~/.codex/config.toml, preserving
    any existing config. Without this, Codex's built-in title animation
    fights with the session-sounds spinner at high frequency.
    """
    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CODEX_CONFIG_PATH.is_file():
        content = CODEX_CONFIG_PATH.read_text()
        # Check if already set to empty -- the only correct value
        if "terminal_title = []" in content:
            print("  Codex terminal_title already disabled")
            return
        # Remove any existing terminal_title line (upgrade path)
        import re
        content = re.sub(r"^\s*terminal_title\s*=.*$", "", content, flags=re.MULTILINE)
        content = re.sub(r"\n{3,}", "\n\n", content)  # collapse blank lines
        # Add under [tui] section
        if "[tui]" in content:
            content = content.replace("[tui]", "[tui]\nterminal_title = []")
        else:
            content = content.rstrip() + "\n\n[tui]\nterminal_title = []\n"
        CODEX_CONFIG_PATH.write_text(content)
    else:
        CODEX_CONFIG_PATH.write_text("[tui]\nterminal_title = []\n")
    print("  Disabled Codex native title in ~/.codex/config.toml")


def _launcher_path() -> str:
    return str(SOUNDS_DST / "agent_launcher.py").replace("\\", "\\\\")


def _powershell_wrapper() -> str:
    launcher = str(SOUNDS_DST / "agent_launcher.py").replace("/", "\\")
    return f'''
function claude {{ python "{launcher}" claude @args }}
function codex {{ python "{launcher}" codex @args }}'''


def _bash_wrapper() -> str:
    launcher = SOUNDS_DST / "agent_launcher.py"
    return f'''
claude() {{ python3 "{launcher}" claude "$@"; }}
codex() {{ python3 "{launcher}" codex "$@"; }}'''


def _configure_apple_terminal() -> bool:
    """Configure macOS Terminal.app to show escape-set titles instead of process name.

    Sets the 'Window Settings' for the default profile to show the icon name
    (set by OSC 1) as the tab title, rather than the active process name.
    """
    if sys.platform != "darwin":
        return False
    term_program = os.environ.get("TERM_PROGRAM", "")
    if "Apple_Terminal" not in term_program:
        return False
    try:
        # Tell Terminal.app to use the window/icon title (set by escape sequences)
        # instead of the running process name for tab labels
        subprocess.run(
            ["defaults", "write", "com.apple.Terminal", "ShowActiveProcessArgumentsInTabTitle", "-bool", "false"],
            check=True, capture_output=True, timeout=5,
        )
        print("  Configured Terminal.app to show session sound names in tabs")
        print("  (restart Terminal.app for this to take effect)")
        return True
    except Exception as e:
        print(f"  Warning: could not configure Terminal.app: {e}")
        return False


def _update_vscode_settings() -> bool:
    paths = [
        Path.home() / "AppData" / "Roaming" / "Code" / "User" / "settings.json",
        Path.home() / "Library" / "Application Support" / "Code" / "User" / "settings.json",
        Path.home() / ".config" / "Code" / "User" / "settings.json",
    ]
    for settings_path in paths:
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_bytes())
                if settings.get("terminal.integrated.tabs.title") == "${sequence}":
                    return True
                settings["terminal.integrated.tabs.title"] = "${sequence}"
                tmp = settings_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(settings, indent=2))
                tmp.replace(settings_path)
                return True
            except Exception as e:
                print(f"  Warning: could not update VS Code settings: {e}")
    return False


def install() -> None:
    print("Claude Code Session Sounds -- Installer")
    print("=" * 45)
    print()

    # 1. Create directories
    SOUNDS_DST.mkdir(parents=True, exist_ok=True)
    ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Created {SOUNDS_DST}")

    # 2. Copy sound files + scripts
    for src in SOUNDS_SRC.iterdir():
        if src.is_dir() and src.name == "events":
            # Copy event sound defaults
            for event_dir in src.iterdir():
                if event_dir.is_dir():
                    dst_event = SOUNDS_DST / "events" / event_dir.name
                    dst_event.mkdir(parents=True, exist_ok=True)
                    for wav in event_dir.glob("*.wav"):
                        shutil.copy2(wav, dst_event / wav.name)
            continue
        if src.is_dir() and src.name == "packs":
            # Copy pack manifests
            for pack_dir in src.iterdir():
                if pack_dir.is_dir():
                    dst_pack = SOUNDS_DST / "packs" / pack_dir.name
                    dst_pack.mkdir(parents=True, exist_ok=True)
                    for f in pack_dir.iterdir():
                        if f.is_file():
                            shutil.copy2(f, dst_pack / f.name)
            continue
        if src.is_dir():
            continue
        dst = SOUNDS_DST / src.name
        shutil.copy2(src, dst)

    # Create events directories (even if no defaults bundled yet)
    for event_type in ("error", "approval", "end"):
        (SOUNDS_DST / "events" / event_type).mkdir(parents=True, exist_ok=True)
    (SOUNDS_DST / "packs").mkdir(parents=True, exist_ok=True)

    wav_count = len(list(SOUNDS_DST.glob("*.wav")))
    print(f"  Installed {wav_count} sounds + scripts")

    # Copy theme directories
    themes_src = SOUNDS_SRC / "themes"
    themes_dst = SOUNDS_DST / "themes"
    if themes_src.is_dir():
        for theme_dir in themes_src.iterdir():
            if theme_dir.is_dir():
                dst_theme = themes_dst / theme_dir.name
                shutil.copytree(theme_dir, dst_theme, dirs_exist_ok=True)
        print(f"  Copied themes to {themes_dst}")

    # Write session-sounds config (preserves existing settings, adds defaults)
    config_file = SOUNDS_DST / "config.json"
    config: dict = {}
    if config_file.is_file():
        try:
            config = json.loads(config_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    config.setdefault("enabled", True)
    config.setdefault("theme", "default")
    config_file.write_text(json.dumps(config, indent=2))
    print(f"  Config: enabled={config['enabled']}, theme={config['theme']}")

    # 3. Merge hooks into settings.json
    settings: dict = {}
    if SETTINGS_PATH.is_file():
        try:
            settings = json.loads(SETTINGS_PATH.read_bytes())
        except Exception:
            pass

    hooks = settings.setdefault("hooks", {})
    for event, hook_list in _hook_commands().items():
        existing = hooks.get(event, [])
        # Remove old sound_manager/title_hook entries before adding new ones (upgrade path)
        cleaned = []
        for group in existing:
            filtered_hooks = [
                h for h in group.get("hooks", [])
                if "sound_manager.py" not in h.get("command", "")
                and "title_hook.py" not in h.get("command", "")
            ]
            if filtered_hooks:
                cleaned.append({**group, "hooks": filtered_hooks})
        cleaned.extend(hook_list)
        hooks[event] = cleaned

    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(SETTINGS_PATH)
    print("  Configured hooks in ~/.claude/settings.json")

    # 4. VS Code settings
    if _update_vscode_settings():
        print("  Set VS Code terminal tab title to ${sequence}")
    else:
        print("  VS Code not found -- set terminal.integrated.tabs.title to")
        print('  "${sequence}" manually for terminal tab naming')

    # 5. Status line
    gsd_patched = _patch_gsd_statusline()
    if gsd_patched:
        print("  Patched GSD status line to show session sound name")
    else:
        # No GSD status line -- install standalone status line config
        if "statusLine" not in settings:
            settings["statusLine"] = _status_line_command()
            tmp = SETTINGS_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(settings, indent=2))
            tmp.replace(SETTINGS_PATH)
            print("  Configured statusLine in ~/.claude/settings.json")
        elif "status_line.py" in str(settings.get("statusLine", {}).get("command", "")):
            print("  Status line already configured")
        else:
            print("  Existing statusLine found -- add sound_name manually or")
            print("  install GSD plugin for automatic integration")

    # 6a. macOS Terminal.app -- show escape-set titles instead of process name
    _configure_apple_terminal()

    # 6b. Codex config -- disable native title animation
    _configure_codex_title()

    # 7. Shell wrapper
    print()
    if sys.platform == "win32":
        wrapper = _powershell_wrapper()
        profile_paths = [
            Path.home() / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
            Path.home() / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
        ]
        installed = False
        for profile in profile_paths:
            if profile.is_file():
                content = profile.read_text()
                if "function claude" not in content:
                    lines = [l for l in content.split("\n") if "Set-Alias claude" not in l]
                    lines.append(wrapper)
                    profile.write_text("\n".join(lines))
                    print(f"  Added claude/codex wrappers to {profile}")
                else:
                    print(f"  claude wrapper already in {profile}")
                installed = True
                break
        if not installed:
            print("  Add this to your PowerShell profile ($PROFILE):")
            print(wrapper)
    else:
        wrapper = _bash_wrapper()
        shell = os.environ.get("SHELL", "/bin/bash")
        rc = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")
        if rc.is_file():
            content = rc.read_text()
            if "claude()" not in content and "function claude" not in content:
                with open(rc, "a") as f:
                    f.write("\n" + wrapper + "\n")
                print(f"  Added claude/codex wrappers to {rc}")
            else:
                print(f"  claude wrapper already in {rc}")
        else:
            print(f"  Add this to {rc}:")
            print(wrapper)

    # 8. Summary
    print()
    print("Done! Open a new terminal and type 'claude'.")
    print()
    print("Add your own sounds:")
    print(f"  Drop .wav files into {SOUNDS_DST}")
    print("  Filenames become display names: cool_cat.wav -> Cool Cat")


def uninstall() -> None:
    print("Claude Code Session Sounds -- Uninstaller")
    print("=" * 45)
    print()

    # 1. Remove hooks from settings.json
    if SETTINGS_PATH.is_file():
        try:
            settings = json.loads(SETTINGS_PATH.read_bytes())
            hooks = settings.get("hooks", {})
            changed = False
            for event in list(hooks.keys()):
                existing = hooks[event]
                cleaned = []
                for group in existing:
                    filtered = [
                        h for h in group.get("hooks", [])
                        if "sound_manager.py" not in h.get("command", "")
                        and "title_hook.py" not in h.get("command", "")
                    ]
                    if filtered:
                        cleaned.append({**group, "hooks": filtered})
                if len(cleaned) != len(existing):
                    changed = True
                if cleaned:
                    hooks[event] = cleaned
                else:
                    del hooks[event]
            if changed:
                tmp = SETTINGS_PATH.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(settings, indent=2))
                tmp.replace(SETTINGS_PATH)
                print("  Removed sound hooks from ~/.claude/settings.json")
            else:
                print("  No sound hooks found in settings.json")
        except Exception as e:
            print(f"  Warning: could not update settings.json: {e}")

    # 2. Note about shell wrappers and sounds directory
    print()
    print("Manual steps remaining:")
    print(f"  1. Remove claude/codex functions from your shell profile")
    if sys.platform == "win32":
        print(f"     (PowerShell $PROFILE)")
    else:
        shell = os.environ.get("SHELL", "/bin/bash")
        rc = "~/.zshrc" if "zsh" in shell else "~/.bashrc"
        print(f"     ({rc})")
    print(f"  2. Optionally delete {SOUNDS_DST}")
    print(f"     (contains your custom sounds -- delete only if you want a clean removal)")
    print()
    print("Done.")


def status() -> None:
    print("Claude Code Session Sounds -- Status")
    print("=" * 45)
    print()

    # Check sounds directory
    if SOUNDS_DST.is_dir():
        wav_count = len(list(SOUNDS_DST.glob("*.wav")))
        print(f"  Sounds directory: {SOUNDS_DST}")
        print(f"  Sound files: {wav_count}")
    else:
        print(f"  Sounds directory: NOT FOUND ({SOUNDS_DST})")

    # Check active sessions
    if ASSIGNMENTS_DIR.is_dir():
        assignments = list(ASSIGNMENTS_DIR.glob("*.json"))
        print(f"  Active sessions: {len(assignments)}")
        for a in assignments:
            try:
                data = json.loads(a.read_text())
                print(f"    - {data.get('name', '?')} ({a.stem[:8]}...)")
            except Exception:
                pass
    else:
        print("  Active sessions: 0")

    # Check hooks
    if SETTINGS_PATH.is_file():
        try:
            settings = json.loads(SETTINGS_PATH.read_bytes())
            hooks = settings.get("hooks", {})
            sound_hooks = []
            for event, groups in hooks.items():
                for group in groups:
                    for h in group.get("hooks", []):
                        if "sound_manager.py" in h.get("command", ""):
                            sound_hooks.append(event)
            if sound_hooks:
                print(f"  Hooks configured: {', '.join(sorted(set(sound_hooks)))}")
            else:
                print("  Hooks configured: NONE")
        except Exception:
            print("  Hooks: could not read settings.json")

    # Check events directory
    events_dir = SOUNDS_DST / "events"
    if events_dir.is_dir():
        event_sounds = {}
        for d in events_dir.iterdir():
            if d.is_dir():
                wavs = list(d.glob("*.wav"))
                if wavs:
                    event_sounds[d.name] = len(wavs)
        if event_sounds:
            print(f"  Event sounds: {', '.join(f'{k}({v})' for k, v in event_sounds.items())}")
        else:
            print("  Event sounds: none installed (will fall back to primary)")

    # Check packs
    packs_dir = SOUNDS_DST / "packs"
    if packs_dir.is_dir():
        packs = [d.name for d in packs_dir.iterdir()
                 if d.is_dir() and (d / "pack.json").is_file()]
        if packs:
            print(f"  Sound packs: {', '.join(packs)}")

    print()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "install"
    if cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    elif cmd == "status":
        status()
    else:
        print(f"Usage: python {sys.argv[0]} [install|uninstall|status]")
        sys.exit(1)
