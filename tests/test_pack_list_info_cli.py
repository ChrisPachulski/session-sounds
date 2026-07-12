import json
from pathlib import Path
import pack_loader


def _write_pack(tmp_path, manifest, pack_id="pack-under-test"):
    """Write a pack.json into tmp_path/<pack_id> and return a PackInfo dict.

    Mirrors the _write_pack helper in test_pack_validate_cli.py, but also
    returns a ready-to-use PackInfo so tests can monkeypatch discovery
    (list_packs/_info_pack read from _REPO_PACKS_DIR/_RUNTIME_PACKS_DIR,
    which are not overridable via a param).
    """
    d = tmp_path / pack_id
    d.mkdir()
    (d / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack_loader.PackInfo(manifest=manifest, pack_dir=d, pack_id=pack_id)


def _make_wav(pack_dir, filename):
    (pack_dir / filename).write_bytes(b"\x00")


def test_list_packs_pool_key_counts_total(tmp_path, capsys, monkeypatch):
    # RED before fix: total = len(m.get("sounds", [])) is 0 for a pool-keyed
    # pack, so the row prints "0/0 ready" instead of the true "N/N ready".
    pack = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Pool List Test",
        "mode": "bundled",
        "pool": [
            {"file": "a.wav", "name": "A"},
            {"file": "b.wav", "name": "B"},
            {"file": "c.wav", "name": "C"},
        ],
    }, pack_id="pool-list-test")
    # All three files exist on disk -> all resolvable.
    for fn in ("a.wav", "b.wav", "c.wav"):
        _make_wav(pack["pack_dir"], fn)

    monkeypatch.setattr(
        pack_loader, "discover_all_packs",
        lambda: {"pool-list-test": pack},
    )
    monkeypatch.setattr(pack_loader, "get_active_pack_id", lambda: None)

    pack_loader.list_packs()
    out = capsys.readouterr().out
    # total must reflect the 3 pool entries, not 0.
    assert "3/3 ready" in out
    assert "0/0 ready" not in out


def test_list_packs_recipe_pool_shows_needed(tmp_path, capsys, monkeypatch):
    # RED before fix: with total=0, available(2) > total(0) so the
    # "(N needed)" suffix never fires and the count reads "2/0 ready".
    pack = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Recipe Pool List",
        "mode": "recipe",
        "pool": [
            {"file": "have.wav", "name": "Have"},
            {"file": "need1.wav", "name": "Need One"},
            {"file": "need2.wav", "name": "Need Two"},
        ],
    }, pack_id="recipe-pool-list")
    # Only one of three exists -> 1/3 ready, 2 needed.
    _make_wav(pack["pack_dir"], "have.wav")

    monkeypatch.setattr(
        pack_loader, "discover_all_packs",
        lambda: {"recipe-pool-list": pack},
    )
    monkeypatch.setattr(pack_loader, "get_active_pack_id", lambda: None)

    pack_loader.list_packs()
    out = capsys.readouterr().out
    assert "1/3 ready" in out
    assert "(2 needed)" in out


def test_list_packs_sounds_key_still_works(tmp_path, capsys, monkeypatch):
    # Regression guard: the canonical `sounds` key must keep working.
    pack = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Sounds List Test",
        "mode": "bundled",
        "sounds": [
            {"file": "x.wav", "name": "X"},
            {"file": "y.wav", "name": "Y"},
        ],
    }, pack_id="sounds-list-test")
    for fn in ("x.wav", "y.wav"):
        _make_wav(pack["pack_dir"], fn)

    monkeypatch.setattr(
        pack_loader, "discover_all_packs",
        lambda: {"sounds-list-test": pack},
    )
    monkeypatch.setattr(pack_loader, "get_active_pack_id", lambda: None)

    pack_loader.list_packs()
    out = capsys.readouterr().out
    assert "2/2 ready" in out


def test_info_pack_pool_key_counts_and_lists(tmp_path, capsys, monkeypatch):
    # RED before fix: total=0 -> "Sounds: 2/0 playable" and the per-sound
    # listing loop (for s in m.get("sounds", [])) is empty, so no rows print.
    pack = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Pool Info Test",
        "mode": "bundled",
        "pool": [
            {"file": "kick.wav", "name": "Kick"},
            {"file": "snare.wav", "name": "Snare"},
        ],
    }, pack_id="pool-info-test")
    # kick exists, snare does not -> 1/2 playable, one ok + one MISSING row.
    _make_wav(pack["pack_dir"], "kick.wav")

    monkeypatch.setattr(
        pack_loader, "discover_all_packs",
        lambda: {"pool-info-test": pack},
    )
    monkeypatch.setattr(pack_loader, "get_active_pack_id", lambda: None)

    pack_loader._info_pack("pool-info-test")
    out = capsys.readouterr().out
    # Total reflects the 2 pool entries, not 0.
    assert "Sounds:      1/2 playable" in out
    # Per-sound listing must appear for pool-keyed packs.
    assert "Kick" in out
    assert "Snare" in out
    assert "[ok" in out
    assert "[MISSING" in out


def test_info_pack_sounds_key_still_works(tmp_path, capsys, monkeypatch):
    # Regression guard: canonical `sounds` key info listing unchanged.
    pack = _write_pack(tmp_path, {
        "schema_version": 1,
        "name": "Sounds Info Test",
        "mode": "bundled",
        "sounds": [
            {"file": "one.wav", "name": "One"},
            {"file": "two.wav", "name": "Two"},
        ],
    }, pack_id="sounds-info-test")
    _make_wav(pack["pack_dir"], "one.wav")
    _make_wav(pack["pack_dir"], "two.wav")

    monkeypatch.setattr(
        pack_loader, "discover_all_packs",
        lambda: {"sounds-info-test": pack},
    )
    monkeypatch.setattr(pack_loader, "get_active_pack_id", lambda: None)

    pack_loader._info_pack("sounds-info-test")
    out = capsys.readouterr().out
    assert "Sounds:      2/2 playable" in out
    assert "One" in out
    assert "Two" in out
