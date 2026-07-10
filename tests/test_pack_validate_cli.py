import json
from pathlib import Path
import pack_loader


def _write_pack(tmp_path, manifest):
    d = tmp_path / "pack-under-test"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_validate_recipe_pack_with_pool_key_does_not_crash(tmp_path, capsys):
    # RED before fix: raises KeyError: 'sounds' at pack_loader.py:413
    d = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Recipe Pool Test",
        "mode": "recipe",
        "pool": [
            {"file": "missing1.wav", "name": "Sound One"},
            {"file": "missing2.wav", "name": "Sound Two"},
        ],
    })
    pack_loader.validate_pack_cli(str(d))  # must not raise
    out = capsys.readouterr().out
    assert "Valid pack: Recipe Pool Test" in out
    # both pool entries are missing on disk -> reported as needed
    assert "Sound One" in out
    assert "Sound Two" in out


def test_validate_platform_pack_with_pool_key_counts_total(tmp_path, capsys):
    # RED before fix: total is 0 -> prints "0/0" instead of "N/N"
    d = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Pool Count Test",
        "mode": "bundled",
        "pool": [
            {"file": "a.wav", "name": "A"},
            {"file": "b.wav", "name": "B"},
            {"file": "c.wav", "name": "C"},
        ],
    })
    pack_loader.validate_pack_cli(str(d))
    out = capsys.readouterr().out
    # total must reflect the 3 pool entries, not 0
    assert "/3 playable" in out


def test_validate_sounds_key_still_works(tmp_path, capsys):
    # Regression guard: the canonical `sounds` key must keep working
    d = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Sounds Key Test",
        "mode": "bundled",
        "sounds": [
            {"file": "x.wav", "name": "X"},
            {"file": "y.wav", "name": "Y"},
        ],
    })
    pack_loader.validate_pack_cli(str(d))
    out = capsys.readouterr().out
    assert "/2 playable" in out
