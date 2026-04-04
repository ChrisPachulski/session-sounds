import json
import os
from pathlib import Path

import sound_manager


def test_theme_with_wavs_and_names(sound_env, monkeypatch):
    theme_dir = sound_env["themes_dir"] / "mytheme"
    theme_dir.mkdir()
    (theme_dir / "horn.wav").write_bytes(b"\x00")
    (theme_dir / "bell.wav").write_bytes(b"\x00")
    (theme_dir / "theme.json").write_text(json.dumps({
        "schema_version": 1,
        "name": "My Theme",
        "sounds": {"horn": "Air Horn", "bell": "Dinner Bell"}
    }))
    monkeypatch.setattr(sound_manager, "SESSION_SOUNDS_THEME", "mytheme")
    pool = sound_manager._load_pool()
    names = {e["name"] for e in pool}
    assert "Air Horn" in names
    assert "Dinner Bell" in names
    assert len(pool) == 2


def test_theme_auto_titles_when_no_mapping(sound_env, monkeypatch):
    theme_dir = sound_env["themes_dir"] / "minimal"
    theme_dir.mkdir()
    (theme_dir / "cool_sound.wav").write_bytes(b"\x00")
    (theme_dir / "theme.json").write_text(json.dumps({
        "schema_version": 1, "name": "Minimal", "sounds": {}
    }))
    monkeypatch.setattr(sound_manager, "SESSION_SOUNDS_THEME", "minimal")
    pool = sound_manager._load_pool()
    assert pool[0]["name"] == "Cool Sound"


def test_missing_theme_falls_back_to_legacy(sound_env, monkeypatch):
    monkeypatch.setattr(sound_manager, "SESSION_SOUNDS_THEME", "nonexistent")
    pool = sound_manager._load_pool()
    assert len(pool) == 5  # legacy loose WAVs in sounds_dir


def test_empty_theme_dir_falls_back_to_legacy(sound_env, monkeypatch):
    theme_dir = sound_env["themes_dir"] / "empty"
    theme_dir.mkdir()
    monkeypatch.setattr(sound_manager, "SESSION_SOUNDS_THEME", "empty")
    pool = sound_manager._load_pool()
    assert len(pool) == 5  # legacy


def test_theme_uses_absolute_paths(sound_env, monkeypatch):
    theme_dir = sound_env["themes_dir"] / "abspath"
    theme_dir.mkdir()
    (theme_dir / "test.wav").write_bytes(b"\x00")
    (theme_dir / "theme.json").write_text(json.dumps({
        "schema_version": 1, "name": "AbsPath", "sounds": {}
    }))
    monkeypatch.setattr(sound_manager, "SESSION_SOUNDS_THEME", "abspath")
    pool = sound_manager._load_pool()
    # Theme pool should store absolute paths
    assert Path(pool[0]["file"]).is_absolute()


def test_corrupt_theme_json_falls_back(sound_env, monkeypatch):
    theme_dir = sound_env["themes_dir"] / "broken"
    theme_dir.mkdir()
    (theme_dir / "sound.wav").write_bytes(b"\x00")
    (theme_dir / "theme.json").write_text("{bad json")
    monkeypatch.setattr(sound_manager, "SESSION_SOUNDS_THEME", "broken")
    pool = sound_manager._load_pool()
    # Should still find the WAV even with broken theme.json (auto-title)
    assert len(pool) == 1
    assert pool[0]["name"] == "Sound"


def test_default_theme_value(sound_env):
    assert sound_manager.SESSION_SOUNDS_THEME == "default"
