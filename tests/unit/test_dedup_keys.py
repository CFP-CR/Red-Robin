"""Unit tests for DedupKeys + derive_key (application layer)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from redrobin.application.dedup_keys import DedupKeys, derive_key
from redrobin.infrastructure.persistence.dedup_keys_repo import DedupKeysRepo


# ---- derive_key ----


def test_derive_key_command_output_with_correlation_id() -> None:
    event = {
        "type": "beacon.command_output",
        "bid": "B-1",
        "correlation_id": "abc123",
        "output": "x",
    }
    assert derive_key(event) == "B-1|abc123|beacon.command_output"


def test_derive_key_command_input_with_correlation_id() -> None:
    event = {
        "type": "beacon.command_input",
        "bid": "B-1",
        "operator": "op",
        "command": "shell ls",
        "raw": "shell ls",
        "correlation_id": "abc123",
    }
    assert derive_key(event) == "B-1|abc123|beacon.command_input"


def test_derive_key_opsec_verdict_with_correlation_id() -> None:
    event = {
        "type": "opsec.verdict",
        "beacon_id": "B-1",
        "correlation_id": "abc123",
        "verdict": "BLOCK",
    }
    # opsec.verdict uses `beacon_id` (not `bid`) per its schema; derive_key
    # accepts either as the producer-attached identity.
    assert derive_key(event) == "B-1|abc123|opsec.verdict"


def test_derive_key_beacon_initial_uses_two_part_key() -> None:
    event = {"type": "beacon.initial", "bid": "B-1", "host": "DC01"}
    # No correlation_id; key is (bid, type) with empty middle slot.
    assert derive_key(event) == "B-1||beacon.initial"


def test_derive_key_missing_bid_returns_none() -> None:
    event = {"type": "beacon.command_output", "correlation_id": "abc", "output": "x"}
    assert derive_key(event) is None


def test_derive_key_missing_correlation_id_returns_none() -> None:
    """Command events without correlation_id are pass-through (no dedup)."""
    event = {"type": "beacon.command_output", "bid": "B-1", "output": "x"}
    assert derive_key(event) is None


def test_derive_key_unknown_event_type_returns_none() -> None:
    event = {"type": "something.else", "bid": "B-1"}
    assert derive_key(event) is None


def test_derive_key_missing_type_returns_none() -> None:
    assert derive_key({"bid": "B-1"}) is None


# ---- DedupKeys ----


def _build(tmp_path: Path, *, preload: set[str] | None = None) -> DedupKeys:
    repo = DedupKeysRepo(tmp_path / "dedup_keys.json")
    if preload is not None:
        repo.save(preload)
    return DedupKeys.load(repo)


@pytest.mark.asyncio
async def test_check_and_insert_returns_true_on_new_key(tmp_path: Path) -> None:
    keys = _build(tmp_path)
    assert await keys.check_and_insert("B-1|abc|beacon.command_output") is True


@pytest.mark.asyncio
async def test_check_and_insert_returns_false_on_duplicate(tmp_path: Path) -> None:
    keys = _build(tmp_path)
    await keys.check_and_insert("k1")
    assert await keys.check_and_insert("k1") is False


@pytest.mark.asyncio
async def test_check_and_insert_persists_to_disk(tmp_path: Path) -> None:
    keys = _build(tmp_path)
    await keys.check_and_insert("k1")
    # Reload from disk via a fresh DedupKeys — state must survive.
    repo = DedupKeysRepo(tmp_path / "dedup_keys.json")
    reloaded = DedupKeys.load(repo)
    assert await reloaded.check_and_insert("k1") is False


@pytest.mark.asyncio
async def test_check_and_insert_loads_preloaded_keys(tmp_path: Path) -> None:
    keys = _build(tmp_path, preload={"k1"})
    assert await keys.check_and_insert("k1") is False
    assert await keys.check_and_insert("k2") is True


@pytest.mark.asyncio
async def test_concurrent_check_and_insert_admits_exactly_one(tmp_path: Path) -> None:
    """Two coroutines racing on the same key — exactly one returns True."""
    keys = _build(tmp_path)
    results = await asyncio.gather(
        keys.check_and_insert("same-key"),
        keys.check_and_insert("same-key"),
    )
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_clear_drops_all_keys(tmp_path: Path) -> None:
    keys = _build(tmp_path, preload={"k1", "k2"})
    keys.clear()
    assert await keys.check_and_insert("k1") is True
    assert await keys.check_and_insert("k2") is True
