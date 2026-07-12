import json
import os
import time
from pathlib import Path

import sound_manager


def test_load_pool_discovers_wav_files(sound_env):
    pool = sound_manager._load_pool()
    assert len(pool) == 5
    # Theme pool stores absolute paths; match on basename.
    filenames = {Path(e["file"]).name for e in pool}
    assert "alpha.wav" in filenames


def test_load_pool_skips_candidate_patterns(sound_env):
    # _CANDIDATE_RE: ^(src_|.*_[a-c])$ -- skips exact stem "src_" and stems ending _a/_b/_c
    default_theme_dir = sound_env["themes_dir"] / "default"
    (default_theme_dir / "thing_a.wav").write_bytes(b"\x00")
    (default_theme_dir / "thing_b.wav").write_bytes(b"\x00")
    pool = sound_manager._load_pool()
    filenames = {Path(e["file"]).name for e in pool}
    assert "thing_a.wav" not in filenames
    assert "thing_b.wav" not in filenames


def test_load_pool_auto_titles_from_filename(sound_env):
    pool = sound_manager._load_pool()
    names = {e["name"] for e in pool}
    assert "Alpha" in names
    assert "Charlie" in names


def test_get_assigned_files_returns_correct_set(sound_env):
    adir = sound_env["assignments_dir"]
    (adir / "sess1.json").write_text(json.dumps({"file": "alpha.wav", "name": "Alpha"}))
    (adir / "sess2.json").write_text(json.dumps({"file": "bravo.wav", "name": "Bravo"}))
    assigned = sound_manager._get_assigned_files()
    assert assigned == {"alpha.wav", "bravo.wav"}


def test_cleanup_if_pressured_evicts_oldest(sound_env):
    adir = sound_env["assignments_dir"]
    pool = sound_manager._load_pool()
    # Assign 4 of 5 sounds (pressure threshold is 2, so 1 available < 2 -> cleanup
    # triggers). Use the pool entries' own "file" values so the assignment records
    # match how production assign() stores them (theme WAVs are absolute paths).
    for i, entry in enumerate(pool[:4]):
        f = adir / f"sess{i}.json"
        f.write_text(json.dumps({"file": entry["file"], "name": entry["name"]}))
        os.utime(f, (time.time() - 1000 + i, time.time() - 1000 + i))

    sound_manager._cleanup_if_pressured(pool)
    remaining = list(adir.glob("*.json"))
    assert len(remaining) < 4


def test_cleanup_skips_reservations(sound_env):
    adir = sound_env["assignments_dir"]
    pool = sound_manager._load_pool()
    # Create 4 assignments to trigger pressure. Use the pool entries' own "file"
    # values so the records match production (theme WAVs are absolute paths).
    for i, entry in enumerate(pool[:4]):
        f = adir / f"sess{i}.json"
        data = {"file": entry["file"], "name": entry["name"]}
        if i == 0:
            data["reserved_at"] = time.time() - 5  # fresh reservation
        f.write_text(json.dumps(data))
        os.utime(f, (time.time() - 1000 + i, time.time() - 1000 + i))

    sound_manager._cleanup_if_pressured(pool)
    # Reservation should survive even though it's oldest
    assert (adir / "sess0.json").exists()


def test_cleanup_orphaned_reservations_removes_old(sound_env):
    adir = sound_env["assignments_dir"]
    old_res = adir / "old-res.json"
    old_res.write_text(json.dumps({"file": "alpha.wav", "name": "Alpha", "reserved_at": time.time() - 300}))
    fresh_res = adir / "fresh-res.json"
    fresh_res.write_text(json.dumps({"file": "bravo.wav", "name": "Bravo", "reserved_at": time.time()}))

    sound_manager._cleanup_orphaned_reservations()
    assert not old_res.exists()
    assert fresh_res.exists()


def test_release_deletes_assignment(sound_env):
    adir = sound_env["assignments_dir"]
    f = adir / "sess-release.json"
    f.write_text(json.dumps({"file": "alpha.wav", "name": "Alpha"}))
    sound_manager.release("sess-release")
    assert not f.exists()


def test_release_missing_file_no_error(sound_env):
    sound_manager.release("nonexistent")  # should not raise


def test_play_corrupt_assignment_deletes_and_stays_silent(sound_env):
    adir = sound_env["assignments_dir"]
    corrupt = adir / "corrupt-sess.json"
    corrupt.write_text("{bad json")
    # play() must not raise on a corrupt assignment; it deletes the bad file and
    # stays silent (it does not re-assign -- assignment is pick()/assign()'s job).
    sound_manager.play("corrupt-sess", event="completion")
    assert not corrupt.exists()


def test_play_unknown_session_skips(sound_env):
    sound_manager.play("unknown", event="completion")  # should not raise


def test_assign_corrupt_existing_falls_through(sound_env, monkeypatch):
    adir = sound_env["assignments_dir"]
    corrupt = adir / "corrupt-assign.json"
    corrupt.write_text("{bad")
    monkeypatch.delenv("CLAUDE_SOUND_RESERVATION", raising=False)
    monkeypatch.delenv("CLAUDE_SOUND_TITLE", raising=False)
    sound_manager.assign("corrupt-assign")
    # Should have cleaned up corrupt and written a fresh assignment
    assert (adir / "corrupt-assign.json").exists()
    data = json.loads((adir / "corrupt-assign.json").read_text())
    assert "file" in data
    assert "name" in data


def _seed_events_dir(sound_env, primary_stem, event):
    """Create a fake per-sound variant AND a default.wav under EVENTS_DIR/{event}/.

    These are the exact files the removed 3-tier resolver would have consulted.
    They exist only so the pinning tests can prove _resolve_event_sound() never
    looks at them.
    """
    events_root = sound_env["sounds_dir"] / "events" / event
    events_root.mkdir(parents=True, exist_ok=True)
    variant = events_root / f"{primary_stem}.wav"
    default = events_root / "default.wav"
    variant.write_bytes(b"\x00")
    default.write_bytes(b"\x00")
    return variant, default


def test_resolve_event_sound_ignores_events_dir(sound_env):
    """Pins commit 9b8cb6f: 'error' always resolves to the identity sound.

    Even when a per-sound variant AND a universal default.wav exist under
    EVENTS_DIR/error/, _resolve_event_sound() must return the primary sound
    path -- no per-sound-variant / default.wav tier is ever consulted.
    """
    primary_file = "alpha.wav"
    variant, default = _seed_events_dir(sound_env, "alpha", "error")

    resolved = sound_manager._resolve_event_sound(primary_file, "error")

    expected = sound_env["sounds_dir"] / primary_file
    assert resolved == expected
    # Prove it did NOT reach into EVENTS_DIR for either tier.
    assert resolved != variant
    assert resolved != default
    assert sound_manager.EVENTS_DIR not in resolved.parents


def test_resolve_event_sound_approval_ignores_events_dir(sound_env):
    """Sibling of the 'error' test: 'approval' also always uses the identity sound."""
    primary_file = "bravo.wav"
    variant, default = _seed_events_dir(sound_env, "bravo", "approval")

    resolved = sound_manager._resolve_event_sound(primary_file, "approval")

    expected = sound_env["sounds_dir"] / primary_file
    assert resolved == expected
    assert resolved != variant
    assert resolved != default
    assert sound_manager.EVENTS_DIR not in resolved.parents


def test_resolve_event_sound_end_is_always_silent(sound_env):
    """Pins commit 9b8cb6f: 'end' is unconditionally silent (None).

    A per-sound variant and default.wav exist under EVENTS_DIR/end/, but the
    resolver returns None before any file check happens -- contrary to the old
    docs' 'Per-sound variant -> default.wav -> silence' chain.
    """
    _seed_events_dir(sound_env, "charlie", "end")

    resolved = sound_manager._resolve_event_sound("charlie.wav", "end")

    assert resolved is None
