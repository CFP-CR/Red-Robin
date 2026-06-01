"""Unit tests for DedupKeysRepo — JSON-backed persistent dedup-key set."""

from __future__ import annotations

import json
from pathlib import Path

from redrobin.infrastructure.persistence.dedup_keys_repo import DedupKeysRepo


def test_load_missing_file_returns_empty_set(tmp_path: Path) -> None:
    repo = DedupKeysRepo(tmp_path / "dedup_keys.json")
    assert repo.load() == set()


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    repo = DedupKeysRepo(tmp_path / "dedup_keys.json")
    keys = {"B-1|abc|beacon.command_output", "B-2||beacon.initial"}
    repo.save(keys)
    assert repo.load() == keys


def test_save_writes_atomic_temp_replace(tmp_path: Path) -> None:
    """Atomic write contract: no stale .tmp left after a successful save."""
    repo = DedupKeysRepo(tmp_path / "dedup_keys.json")
    repo.save({"B-1|abc|beacon.command_output"})
    # The .tmp sibling must NOT linger.
    assert not (tmp_path / "dedup_keys.json.tmp").exists()
    # Target file is well-formed JSON.
    data = json.loads((tmp_path / "dedup_keys.json").read_text(encoding="utf-8"))
    assert data == {"keys": ["B-1|abc|beacon.command_output"]}


def test_save_overwrites_stale_tmp(tmp_path: Path) -> None:
    """If a prior crash left a stale .tmp, the next save must still succeed."""
    target = tmp_path / "dedup_keys.json"
    stale_tmp = tmp_path / "dedup_keys.json.tmp"
    stale_tmp.write_text("garbage", encoding="utf-8")
    repo = DedupKeysRepo(target)
    repo.save({"B-1|abc|beacon.command_output"})
    assert not stale_tmp.exists()
    assert repo.load() == {"B-1|abc|beacon.command_output"}


def test_load_corrupt_file_returns_empty_set(tmp_path: Path, caplog) -> None:
    """A corrupt JSON file degrades gracefully to empty (logs ERROR)."""
    target = tmp_path / "dedup_keys.json"
    target.write_text("not-json {", encoding="utf-8")
    repo = DedupKeysRepo(target)
    with caplog.at_level("ERROR"):
        assert repo.load() == set()
    assert any("dedup_keys" in rec.message for rec in caplog.records)


def test_load_wrong_shape_returns_empty_set(tmp_path: Path, caplog) -> None:
    """A JSON file with the wrong shape (list, missing 'keys') loads as empty."""
    target = tmp_path / "dedup_keys.json"
    target.write_text(json.dumps({"wrong": []}), encoding="utf-8")
    repo = DedupKeysRepo(target)
    with caplog.at_level("ERROR"):
        assert repo.load() == set()


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    """Like WorkingMemoryRepo, save() creates parent dirs if missing."""
    target = tmp_path / "engagements" / "x" / "dedup_keys.json"
    repo = DedupKeysRepo(target)
    repo.save({"k1"})
    assert target.exists()
    assert repo.load() == {"k1"}


def test_clear_removes_file(tmp_path: Path) -> None:
    """clear() deletes the underlying file; load() then returns empty."""
    repo = DedupKeysRepo(tmp_path / "dedup_keys.json")
    repo.save({"k1"})
    repo.clear()
    assert not (tmp_path / "dedup_keys.json").exists()
    assert repo.load() == set()


def test_clear_missing_file_is_noop(tmp_path: Path) -> None:
    """clear() on a missing file does not raise."""
    repo = DedupKeysRepo(tmp_path / "dedup_keys.json")
    repo.clear()  # must not raise
    assert repo.load() == set()
