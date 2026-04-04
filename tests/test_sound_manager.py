import json
import os
import time
from pathlib import Path

import sound_manager


def test_load_pool_discovers_wav_files(sound_env):
    pool = sound_manager._load_pool()
    assert len(pool) == 5
    filenames = {e["file"] for e in pool}
    assert "alpha.wav" in filenames


def test_load_pool_skips_candidate_patterns(sound_env):
    # _CANDIDATE_RE: ^(src_|.*_[a-c])$ -- skips exact stem "src_" and stems ending _a/_b/_c
    (sound_env["sounds_dir"] / "thing_a.wav").write_bytes(b"\x00")
    (sound_env["sounds_dir"] / "thing_b.wav").write_bytes(b"\x00")
    pool = sound_manager._load_pool()
    filenames = {e["file"] for e in pool}
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
    # Assign 4 of 5 sounds (pressure threshold is 2, so 1 available < 2 -> cleanup triggers)
    for i, name in enumerate(["alpha", "bravo", "charlie", "delta"]):
        f = adir / f"sess{i}.json"
        f.write_text(json.dumps({"file": f"{name}.wav", "name": name.title()}))
        os.utime(f, (time.time() - 1000 + i, time.time() - 1000 + i))

    sound_manager._cleanup_if_pressured(pool)
    remaining = list(adir.glob("*.json"))
    assert len(remaining) < 4


def test_cleanup_skips_reservations(sound_env):
    adir = sound_env["assignments_dir"]
    pool = sound_manager._load_pool()
    # Create 4 assignments to trigger pressure
    for i, name in enumerate(["alpha", "bravo", "charlie", "delta"]):
        f = adir / f"sess{i}.json"
        data = {"file": f"{name}.wav", "name": name.title()}
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


def test_play_corrupt_assignment_recovers(sound_env):
    adir = sound_env["assignments_dir"]
    corrupt = adir / "corrupt-sess.json"
    corrupt.write_text("{bad json")
    # Should not raise; should clean up corrupt file and re-assign
    sound_manager.play("corrupt-sess", event="completion")
    # The file is re-created with a valid assignment after recovery
    assert corrupt.exists()
    data = json.loads(corrupt.read_text())
    assert "file" in data
    assert "name" in data


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
