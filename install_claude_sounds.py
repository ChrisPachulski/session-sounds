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


def _sounds_path() -> str:
    return str(SOUNDS_DST).replace("\\", "/")


def _hook_commands() -> dict:
    py = "python" if sys.platform == "win32" else "python3"
    sm = f'{py} "{_sounds_path()}/sound_manager.py"'
    th = f'{py} "{_sounds_path()}/title_hook.py"'
    return {
        "SessionStart": [{"hooks": [
            {"type": "command", "command": f'{sm} assign', "timeout": 5},
            {"type": "command", "command": th, "timeout": 5},
        ]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": f'{sm} play', "async": True, "timeout": 10},
            {"type": "command", "command": th, "timeout": 5},
        ]}],
        "SessionEnd": [{"hooks": [
            {"type": "command", "command": f'{sm} release', "timeout": 5},
        ]}],
    }


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
        if src.is_dir():
            continue
        dst = SOUNDS_DST / src.name
        shutil.copy2(src, dst)
    wav_count = len(list(SOUNDS_DST.glob("*.wav")))
    print(f"  Installed {wav_count} sounds + scripts")

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
        has_sound = any(
            "sound_manager.py" in h.get("command", "")
            for group in existing for h in group.get("hooks", [])
        )
        if not has_sound:
            existing.extend(hook_list)
            hooks[event] = existing

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

    # 5. Shell wrapper
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
            if "claude()" not in content:
                with open(rc, "a") as f:
                    f.write("\n" + wrapper + "\n")
                print(f"  Added claude/codex wrappers to {rc}")
            else:
                print(f"  claude wrapper already in {rc}")
        else:
            print(f"  Add this to {rc}:")
            print(wrapper)

    # 6. Summary
    print()
    print("Done! Open a new terminal and type 'claude'.")
    print()
    print("Add your own sounds:")
    print(f"  Drop .wav files into {SOUNDS_DST}")
    print("  Filenames become display names: cool_cat.wav -> Cool Cat")


if __name__ == "__main__":
    install()
