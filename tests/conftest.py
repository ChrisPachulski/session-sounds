import json
import os
import sys
import time
from pathlib import Path

import pytest

# Add sounds/ to path so we can import sound_manager
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sounds"))


@pytest.fixture
def sound_env(tmp_path, monkeypatch):
    """Set up an isolated sound environment with fake WAVs and assignments dir."""
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    themes_dir = sounds_dir / "themes"
    themes_dir.mkdir()

    # Sounds now load exclusively from the active theme directory (the legacy
    # loose-WAV fallback in sounds/ was removed in the "ship default theme"
    # change). Create the "default" theme (SESSION_SOUNDS_THEME's default value)
    # with 5 fake WAV stubs so _load_pool() has a 5-sound pool.
    names = ["alpha", "bravo", "charlie", "delta", "echo"]
    default_theme_dir = themes_dir / "default"
    default_theme_dir.mkdir()
    for name in names:
        (default_theme_dir / f"{name}.wav").write_bytes(b"\x00")

    # Patch sound_manager constants
    import sound_manager
    monkeypatch.setattr(sound_manager, "SOUNDS_DIR", sounds_dir)
    monkeypatch.setattr(sound_manager, "ASSIGNMENTS_DIR", assignments_dir)
    monkeypatch.setattr(sound_manager, "EVENTS_DIR", sounds_dir / "events")
    monkeypatch.setattr(sound_manager, "THEMES_DIR", themes_dir)
    monkeypatch.setattr(sound_manager, "PRESSURE_THRESHOLD", 2)
    # Prevent actual audio playback in tests
    monkeypatch.setattr(sound_manager, "_play_sound", lambda wav_path: None)

    return {
        "sounds_dir": sounds_dir,
        "assignments_dir": assignments_dir,
        "themes_dir": themes_dir,
        "names": names,
    }
