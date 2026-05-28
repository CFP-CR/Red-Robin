# B3 — `.cna` Event Dedup + Auto-obs ROOT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the `.cna` re-emission amplifier (#152) and the producer-side self-ingestion of `[Red Robin]` blog lines (auto-obs ROOT) so the daemon processes each producer event exactly once.

**Architecture:** Daemon-side **Idempotent Receiver** (Hohpe & Woolf) with a producer-attached key. A new persistent per-engagement key set (`DedupKeys`) gates `events_router.ingest`/`replay_event` BEFORE append-to-`events.jsonl` and BEFORE bus publish. Composite key: `(beacon_id, correlation_id, event_type)` for command events, `(beacon_id, event_type)` for `beacon.initial`. State survives daemon restart (atomic `temp+replace` JSON; engagement-lifetime retention). The `.cna`'s `BEACON_OUTPUT` hook adds a sentinel content-check that suppresses re-emission of its own `[Red Robin]`-prefixed lines while preserving display. B1.5 T2 `strip_self_annotations` stays as enricher-side belt+suspenders.

**Tech Stack:** Python 3.12 + FastAPI + asyncio + JSON, pytest + pytest-asyncio + httpx TestClient, ruff/mypy/import-linter, `uv`. Aggressor Sleep for the `.cna` change (smoke-only, no CI).

---

## Pre-existing context (read once before starting)

- **Spec:** `docs/superpowers/specs/2026-05-28-cna-event-dedup-design.md` (commit `286fbe5`). Read it; the plan implements §4 piece by piece.
- **Atomic-write pattern reference:** `redrobin/infrastructure/persistence/working_memory_repo.py:50-69` — `save()` writes to a sibling `.tmp` then `os.replace()` with `PermissionError` retry-on-Windows. Reuse this pattern.
- **Lifespan-helper pattern reference:** `redrobin/api/lifespan_helpers.py` — every startup concern is an `async def start_X(app, settings) -> tuple[Resource, CloseHook]` that attaches `app.state.X = resource`. Follow it.
- **Events router current state:** `redrobin/api/events_router.py`
  - `POST /v1/events` (`ingest`, lines 53-93) — validates envelope + per-type schema, appends to `events.jsonl`, publishes to bus, returns `{"ok": True, "stored": bool}` (200).
  - `POST /v1/events/replay` (`replay_event`, lines 96-120) — skips schema validation, publishes to bus, returns `{"status": "accepted"}` (202).
- **`.cna` `BEACON_OUTPUT` hook current state** (post-B1.5 T9): `adapters/cobaltstrike/redrobin.cna:460-470`.

## File structure

| File | Purpose | Layer |
|---|---|---|
| `redrobin/infrastructure/persistence/dedup_keys_repo.py` | NEW. JSON-backed `set[str]` persistence with atomic `temp+replace`. | infrastructure |
| `redrobin/application/dedup_keys.py` | NEW. `DedupKeys` (set + `asyncio.Lock` + persistence delegate) and `derive_key()` event-aware helper. | application |
| `redrobin/api/lifespan_helpers.py` | MODIFY. Add `start_dedup(app, settings)` that loads keys from the active engagement and attaches `app.state.dedup_keys`. | api |
| `redrobin/api/app.py` | MODIFY. Call `start_dedup` in the lifespan orchestrator. | api |
| `redrobin/api/events_router.py` | MODIFY. Add dedup gate in `ingest` and `replay_event` before append/publish. | api |
| `redrobin/application/engagement_service.py` | MODIFY. Clear dedup file on `archive` / `delete`; new engagement starts with empty set (no file → empty load). | application |
| `adapters/cobaltstrike/redrobin.cna` | MODIFY. Add `[Red Robin]` sentinel check at the top of `BEACON_OUTPUT`. **SMOKE-ONLY (no CI).** | adapter |
| `tests/unit/test_dedup_keys_repo.py` | NEW unit tests for the JSON repo. | tests |
| `tests/unit/test_dedup_keys.py` | NEW unit tests for `DedupKeys` + `derive_key`. | tests |
| `tests/integration/test_events_router_dedup.py` | NEW integration tests: POST twice → second drops; restart-then-POST preserves; replay endpoint dedups. | tests |
| `tests/integration/test_dedup_engagement_lifecycle.py` | NEW integration tests: `archive`/`delete` clears dedup; `new` starts empty. | tests |

---

## Task 1: `DedupKeysRepo` — JSON-backed persistent set with atomic write

**Files:**
- Create: `redrobin/infrastructure/persistence/dedup_keys_repo.py`
- Test: `tests/unit/test_dedup_keys_repo.py`

The repo is a thin wrapper over a JSON file containing `{"keys": [...]}`. It exposes `load() -> set[str]` and `save(keys: set[str]) -> None`. Save uses the same atomic `temp+replace` pattern as `WorkingMemoryRepo.save` (`working_memory_repo.py:50-69`) so a concurrent reader never sees a half-written file. Missing or corrupt files load as the empty set (log + accept the temporary regression).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dedup_keys_repo.py` with EXACTLY this content:

```python
"""Unit tests for DedupKeysRepo — JSON-backed persistent dedup-key set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_dedup_keys_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.infrastructure.persistence.dedup_keys_repo'`.

- [ ] **Step 3: Implement `DedupKeysRepo`**

Create `redrobin/infrastructure/persistence/dedup_keys_repo.py` with EXACTLY this content:

```python
"""redrobin/infrastructure/persistence/dedup_keys_repo.py — JSON-backed set
of dedup keys, persistent per engagement.

The dedup gate in ``events_router.ingest`` uses this set to drop duplicate
re-emissions of producer events (the `.cna` re-fires Aggressor hooks on every
CS-client reconnect, multiplying ingest by hundreds — see spec
``docs/superpowers/specs/2026-05-28-cna-event-dedup-design.md``).

Atomic write contract: serialize to a sibling ``.tmp`` then ``os.replace()`` it
onto the target. A concurrent reader therefore sees either the old or the new
complete file — never the empty window a plain truncate-then-write exposes.
``os.replace`` is atomic on POSIX; on Windows it raises ``PermissionError``
while a reader holds the target open, so we retry briefly before giving up.
Pattern mirrors ``WorkingMemoryRepo.save`` (working_memory_repo.py).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class DedupKeysRepo:
    """Read/write a single-engagement JSON file holding a set of dedup keys.

    Format on disk: ``{"keys": ["<key>", ...]}``. The in-memory representation
    is ``set[str]``; keys are opaque strings produced by the application layer
    (``redrobin.application.dedup_keys.derive_key``).
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("dedup_keys: corrupt file at %s; loading empty", self.path)
            return set()
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            logger.error("dedup_keys: wrong shape in %s; loading empty", self.path)
            return set()
        return {str(k) for k in data["keys"]}

    def save(self, keys: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(json.dumps({"keys": sorted(keys)}, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 9:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.02)

    def clear(self) -> None:
        """Delete the underlying file; missing-file is a no-op."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_dedup_keys_repo.py -v`
Expected: 9 passed.

- [ ] **Step 5: Run the full CI gate**

```bash
uv run --no-sync ruff check redrobin/infrastructure/persistence/dedup_keys_repo.py tests/unit/test_dedup_keys_repo.py
uv run --no-sync ruff format --check redrobin/infrastructure/persistence/dedup_keys_repo.py tests/unit/test_dedup_keys_repo.py
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: every gate green. Pytest = pre-B3 baseline + 9 new tests.

- [ ] **Step 6: Commit**

```bash
git add redrobin/infrastructure/persistence/dedup_keys_repo.py tests/unit/test_dedup_keys_repo.py
git commit -m "feat(dedup): DedupKeysRepo — JSON-backed persistent key set with atomic write"
```

---

## Task 2: `DedupKeys` + `derive_key` — application-layer dedup with asyncio.Lock

**Files:**
- Create: `redrobin/application/dedup_keys.py`
- Test: `tests/unit/test_dedup_keys.py`

`DedupKeys` wraps a `set[str]`, an `asyncio.Lock`, and a `DedupKeysRepo`. Its sole async method is `check_and_insert(key)` which returns `True` if the key is new (and persists the updated set) or `False` if the key was already present. `derive_key(event)` is a pure function that returns the dedup key string for a wire event payload, or `None` if the event type is unmappable (pass-through).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dedup_keys.py` with EXACTLY this content:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_dedup_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.dedup_keys'`.

- [ ] **Step 3: Implement `DedupKeys` + `derive_key`**

Create `redrobin/application/dedup_keys.py` with EXACTLY this content:

```python
"""redrobin/application/dedup_keys.py — application-layer dedup state.

``DedupKeys`` holds an in-memory ``set[str]`` and an ``asyncio.Lock``,
delegates persistence to ``DedupKeysRepo``, and exposes a single async
``check_and_insert(key)`` that the ingest path calls before applying side
effects (append-to-jsonl, bus publish).

``derive_key(event)`` is a pure function that maps a wire event payload to
a stable dedup key string, or returns ``None`` for events that should
pass-through unfiltered (events without ``correlation_id`` other than
``beacon.initial``).

Pattern: Idempotent Receiver (Hohpe & Woolf). Producer-attached key
(``correlation_id`` from the `.cna`'s Aggressor ``$5``), composite with
``beacon_id`` + ``event_type`` to defend against cross-event-type collisions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from redrobin.infrastructure.persistence.dedup_keys_repo import DedupKeysRepo

logger = logging.getLogger(__name__)

# Event types that carry a correlation_id and that we dedup with the
# (bid, correlation_id, type) composite key.
_CORRELATED_TYPES = frozenset(
    {"beacon.command_input", "beacon.command_output", "opsec.verdict"}
)


def derive_key(event: dict[str, Any]) -> str | None:
    """Return the dedup key for a wire event payload, or ``None`` for
    pass-through (event types we choose not to dedup).

    Key shape: ``"<bid>|<correlation_id>|<event_type>"`` for command events,
    ``"<bid>||<event_type>"`` for ``beacon.initial`` (no correlation_id; exactly
    one initial per beacon by definition).
    """
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None

    # opsec.verdict uses `beacon_id`; the beacon.* events use `bid`.
    bid_raw = event.get("bid") or event.get("beacon_id")
    if not bid_raw:
        return None
    bid = str(bid_raw)

    if event_type == "beacon.initial":
        return f"{bid}||{event_type}"

    if event_type in _CORRELATED_TYPES:
        corr_raw = event.get("correlation_id")
        if not corr_raw:
            return None
        return f"{bid}|{corr_raw}|{event_type}"

    return None


class DedupKeys:
    """In-memory set of seen dedup keys, persisted via ``DedupKeysRepo``.

    Use :meth:`load` to hydrate from disk at lifespan boot. ``check_and_insert``
    is the single async entry point: it returns ``True`` if the key was new
    (and the state was persisted) or ``False`` if the key was already present.
    """

    def __init__(self, repo: DedupKeysRepo, keys: set[str]) -> None:
        self._repo = repo
        self._keys = keys
        self._lock = asyncio.Lock()

    @classmethod
    def load(cls, repo: DedupKeysRepo) -> "DedupKeys":
        return cls(repo=repo, keys=repo.load())

    async def check_and_insert(self, key: str) -> bool:
        """Atomically check + insert + persist. Returns True iff key is new.

        Save-first-then-mutate: persist the candidate set BEFORE mutating the
        in-memory set, so a ``save()`` failure leaves disk and memory both
        unchanged (no drift where memory has the key but disk doesn't, which
        would silently un-dedup the next process restart).
        """
        async with self._lock:
            if key in self._keys:
                return False
            new_keys = self._keys | {key}
            self._repo.save(new_keys)  # may raise — both stores unchanged on failure
            self._keys = new_keys
            return True

    def clear(self) -> None:
        """Drop the in-memory set and the on-disk file."""
        self._keys.clear()
        self._repo.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_dedup_keys.py -v`
Expected: 14 passed.

- [ ] **Step 5: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: every gate green.

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/dedup_keys.py tests/unit/test_dedup_keys.py
git commit -m "feat(dedup): DedupKeys + derive_key — async key set with composite-key derivation"
```

---

## Task 3: Wire `start_dedup` into the lifespan

**Files:**
- Modify: `redrobin/api/lifespan_helpers.py` (add `start_dedup`)
- Modify: `redrobin/api/app.py` (call `start_dedup` in the orchestrator)
- Test: extend an existing lifespan integration test OR add a focused test in `tests/integration/test_events_router_dedup.py` (deferred to Task 4 — for Task 3, write a minimal lifespan-boot test inline below)

`start_dedup` runs after the engagement is resolved (so we know which engagement's `dedup_keys.json` to load). On a degraded boot (no active engagement), we attach an in-memory-only `DedupKeys` backed by a no-op repo so the events router can always call `app.state.dedup_keys.check_and_insert(...)`.

- [ ] **Step 1: Write the failing test (lifespan boot)**

Append this test to a NEW file `tests/integration/test_events_router_dedup.py` (the bulk of the file lands in Task 4, but the lifespan test belongs here):

```python
"""Integration tests for the events_router dedup gate (B3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Boot the daemon in degraded mode against an isolated root."""
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-b3")
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token
    return get_or_create_token()


def test_lifespan_attaches_dedup_keys(daemon) -> None:
    app, _ = daemon
    from redrobin.application.dedup_keys import DedupKeys

    assert hasattr(app.state, "dedup_keys")
    assert isinstance(app.state.dedup_keys, DedupKeys)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_events_router_dedup.py::test_lifespan_attaches_dedup_keys -v`
Expected: FAIL with `AttributeError: 'State' object has no attribute 'dedup_keys'`.

- [ ] **Step 3: Add `start_dedup` to `lifespan_helpers.py`**

In `redrobin/api/lifespan_helpers.py`, add this import near the top imports block (alphabetically grouped under `redrobin.application`):

```python
from redrobin.application.dedup_keys import DedupKeys
from redrobin.infrastructure.persistence.dedup_keys_repo import DedupKeysRepo
```

Then append this function at the bottom of the module (after the last existing `start_*` helper):

```python
async def start_dedup(
    app: FastAPI, settings: Settings
) -> tuple[DedupKeys, CloseHook]:
    """Load the per-engagement dedup-key set into ``app.state.dedup_keys``.

    Reads the engagement already resolved by ``start_engagement`` (which
    handles the ``REDROBIN_ENGAGEMENT_SLUG`` env var → ``.active_engagement``
    file precedence). MUST be called AFTER ``start_engagement`` so that
    ``app.state.engagement`` is populated — calling it earlier silently falls
    to the degraded path and writes the dedup file under root instead of
    under the engagement dir.

    Degraded path (no engagement resolved): a DedupKeys backed by a per-root
    file (``<root>/dedup_keys.json``) so the events router can always call
    into ``app.state.dedup_keys`` without a None-check.
    """
    active_engagement = getattr(app.state, "engagement", None)
    if active_engagement is not None and settings.engagements_dir is not None:
        keys_path = (
            settings.engagements_dir / active_engagement.slug / "dedup_keys.json"
        )
    else:
        keys_path = settings.root / "dedup_keys.json"
    repo = DedupKeysRepo(keys_path)
    dedup_keys = DedupKeys.load(repo)
    app.state.dedup_keys = dedup_keys
    logger.info("dedup_keys: loaded %d keys from %s", len(repo.load()), keys_path)

    async def _close_dedup() -> None:
        # No background tasks / sockets to close; in-memory + disk state is
        # already durable. A no-op closer matches the existing helper
        # convention — every other resource-returning helper provides a real
        # or no-op CloseHook (never ``None``).
        return None

    return dedup_keys, _close_dedup
```

- [ ] **Step 4: Wire `start_dedup` into the lifespan orchestrator**

In `redrobin/api/app.py`, find the `lifespan(app)` async-context block and add a call to `start_dedup(app, settings)` immediately after the existing engagement-resolving call (`start_engagement`) and BEFORE `start_working_memory`. **Ordering is load-bearing**: `start_dedup` reads `app.state.engagement` (populated by `start_engagement`); calling it earlier silently degrades to the per-root path. `start_dedup` returns `(DedupKeys, CloseHook)` — capture the closer to match the file's existing helper-registration shape (the closer is a no-op so it's harmless to call at shutdown but stays consistent with `close_bus` / `close_wm`).

Example (pattern only — match the file's actual orchestration shape; if `app.py` uses a different close-hook list shape, adopt that shape):

```python
from redrobin.api.lifespan_helpers import (
    ...,
    start_dedup,
    ...,
)

async with ...:
    ...
    await start_engagement(app, settings)             # populates app.state.engagement
    _, close_dedup = await start_dedup(app, settings) # NEW: must be after engagement
    _, close_wm = await start_working_memory(app, settings)
    ...
    # On shutdown (LIFO):
    #   await close_wm()
    #   await close_dedup()   # NEW — no-op but stays in the close list
    #   await close_bus()
    ...
```

If `app.py` uses a different list/loop shape for closers (e.g., appending to a list and awaiting in reverse), append `close_dedup` to the same list in the same position relative to the existing helpers. Keep the diff minimal — do NOT reorder existing helpers.

- [ ] **Step 5: Run the test to verify it passes**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_events_router_dedup.py::test_lifespan_attaches_dedup_keys -v`
Expected: 1 passed.

- [ ] **Step 6: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: every gate green.

- [ ] **Step 7: Commit**

```bash
git add redrobin/api/lifespan_helpers.py redrobin/api/app.py tests/integration/test_events_router_dedup.py
git commit -m "feat(dedup): start_dedup lifespan helper — load per-engagement key set at boot"
```

---

## Task 4: Wire dedup gate into `events_router.ingest` and `replay_event`

**Files:**
- Modify: `redrobin/api/events_router.py`
- Test: extend `tests/integration/test_events_router_dedup.py`

The dedup gate runs AFTER schema validation (in `ingest`) or directly at the top of `replay_event` (no schema there), and BEFORE the existing side effects: append-to-`events.jsonl` (ingest only) + bus publish (both). On a hit, return early with a body that signals duplicate; on a miss, fall through to the existing side effects.

- [ ] **Step 1: Write the failing tests**

Append these tests to `tests/integration/test_events_router_dedup.py`:

```python
def _envelope(
    *,
    bid: str = "B-1",
    correlation_id: str = "abc123",
    event_type: str = "beacon.command_output",
    output: str = "hello",
) -> dict[str, Any]:
    """Build a minimal canonical wire envelope for tests."""
    return {
        "spec_version": "0.1.0",
        "id": "evt_test_1",
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": event_type,
            "bid": bid,
            "task_id": "",
            "output": output,
            "correlation_id": correlation_id,
        },
    }


def test_ingest_dedups_repeated_envelope(daemon) -> None:
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = _envelope()

    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)

    assert r1.status_code == 200
    assert r1.json().get("stored") is True
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True
    # Duplicate must not have appended a second line.
    events_jsonl = Path(__import__("os").environ["REDROBIN_ROOT"]) / "engagements" / "eng-b3" / "events.jsonl"
    if events_jsonl.exists():
        lines = events_jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1


def test_replay_dedups_repeated_bus_event(daemon) -> None:
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    bus_event = {
        "type": "beacon.command_output",
        "bid": "B-2",
        "task_id": "",
        "output": "x",
        "correlation_id": "xyz789",
    }

    r1 = client.post("/v1/events/replay", json=bus_event, headers=headers)
    r2 = client.post("/v1/events/replay", json=bus_event, headers=headers)

    assert r1.status_code == 202
    assert r1.json()["status"] == "accepted"
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"


def test_ingest_passes_through_event_without_correlation_id(daemon) -> None:
    """An event whose derive_key returns None (no correlation_id) is NOT
    deduped — both POSTs should reach the bus + jsonl."""
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    # beacon.command_output WITHOUT correlation_id → derive_key returns None.
    payload = {
        "spec_version": "0.1.0",
        "id": "evt_test_p",
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.command_output",
            "bid": "B-9",
            "task_id": "",
            "output": "no-corr",
        },
    }

    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)

    # Both stored (no dedup for events without correlation_id).
    assert r1.status_code == 200 and r1.json().get("stored") is True
    assert r2.status_code == 200 and r2.json().get("stored") is True
    assert "duplicate" not in r2.json()


def test_beacon_initial_dedups_on_bid_and_type(daemon) -> None:
    """beacon.initial has no correlation_id; dedup key is (bid, type)."""
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "spec_version": "0.1.0",
        "id": "evt_test_i",
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.initial",
            "bid": "B-3",
            "host": "DC01",
            "user": "CORP\\admin",
            "integrity_level": "high",
            "arch": "x64",
            "pid": 1234,
            "ppid": 5678,
        },
    }

    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)

    assert r1.status_code == 200 and r1.json().get("stored") is True
    assert r2.status_code == 200 and r2.json().get("duplicate") is True


def test_state_survives_test_client_restart(tmp_path: Path, monkeypatch) -> None:
    """Persist + reload: a second TestClient over the same root sees keys."""
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-b3")
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.api.app import create_app
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    payload = {
        "spec_version": "0.1.0",
        "id": "evt_test_r",
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.command_output",
            "bid": "B-4",
            "task_id": "",
            "output": "x",
            "correlation_id": "persist1",
        },
    }
    headers = {"Authorization": f"Bearer {get_or_create_token()}"}

    # Boot #1: POST once.
    app1 = create_app()
    with TestClient(app1) as c1:
        r1 = c1.post("/v1/events", json=payload, headers=headers)
        assert r1.status_code == 200 and r1.json().get("stored") is True

    # Boot #2 (same root): POST again → should be deduped.
    app2 = create_app()
    with TestClient(app2) as c2:
        r2 = c2.post("/v1/events", json=payload, headers=headers)
        assert r2.status_code == 200 and r2.json().get("duplicate") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_events_router_dedup.py -v`
Expected: 5 new tests FAIL (no dedup wired yet).

- [ ] **Step 3: Wire the dedup gate into `events_router.ingest`**

In `redrobin/api/events_router.py`:

1. Add the import near the top:

```python
from redrobin.application.dedup_keys import DedupKeys, derive_key
```

2. Replace the `ingest` function body. The new function reads `app.state.dedup_keys`, derives the key from the event, and if `check_and_insert` returns `False` (duplicate), returns early with `{"ok": True, "stored": False, "duplicate": True}` — BEFORE the jsonl append and bus publish. Full replacement:

```python
@router.post("")
async def ingest(
    envelope: dict[str, Any],
    request: Request,
    svc: EngagementService = Depends(_engagement_service_dep),
) -> dict[str, Any]:
    # Validate envelope shape
    errors = validate_envelope(envelope)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"envelope_errors": errors},
        )
    # Validate event payload against per-type schema
    event_body = envelope["event"]
    event_type = event_body["type"]
    payload_errors = validate_event(event_type, event_body)
    if payload_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"event_errors": payload_errors},
        )

    # Dedup gate (B3): drop re-emissions BEFORE append + bus publish.
    dedup_keys: DedupKeys | None = getattr(request.app.state, "dedup_keys", None)
    if dedup_keys is not None:
        key = derive_key(event_body)
        if key is not None and not await dedup_keys.check_and_insert(key):
            logger.debug("dedup hit: %s", key)
            return {"ok": True, "stored": False, "duplicate": True}

    # Append to events.jsonl for the active engagement.
    active = svc.current()
    stored = False
    if active is not None:
        engagement_path = redrobin_root() / "engagements" / active
        if engagement_path.exists():
            events_jsonl = engagement_path / "events.jsonl"
            with events_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(envelope) + "\n")
            stored = True

    # Republish onto the bus so the reducer + TUI WS see the wire event.
    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        await bus.publish(_envelope_to_bus_event(envelope))

    return {"ok": True, "stored": stored}
```

Add `import logging` and `logger = logging.getLogger(__name__)` near the top of the module if not already present.

3. Replace the `replay_event` body to also dedup. Full replacement:

```python
@router.post("/replay")
async def replay_event(event: dict[str, Any], request: Request) -> dict[str, str]:
    """Forward a JSONL trace line onto the in-process event bus.

    Accepts either a full wire envelope (the shape ``events.jsonl`` actually
    stores) or a pre-flattened bus-shape dict (older traces, hand-crafted
    fixtures). Envelopes are translated to bus shape so subscribers see the
    same ``{type, payload}`` dicts regardless of how the trace was captured.

    Schema validation is intentionally skipped — the operator owns the trace
    contents — but the B3 dedup gate still runs.
    """
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="event_bus not initialized",
        )

    if _is_envelope(event):
        event_body = event["event"]
        bus_event = _envelope_to_bus_event(event)
    else:
        event_body = event
        bus_event = event

    # Dedup gate (B3).
    dedup_keys: DedupKeys | None = getattr(request.app.state, "dedup_keys", None)
    if dedup_keys is not None:
        key = derive_key(event_body)
        if key is not None and not await dedup_keys.check_and_insert(key):
            logger.debug("dedup hit (replay): %s", key)
            return JSONResponse(
                status_code=200, content={"status": "duplicate"}
            )

    await bus.publish(bus_event)
    return JSONResponse(status_code=202, content={"status": "accepted"})
```

Add the `JSONResponse` import:

```python
from fastapi.responses import JSONResponse
```

Note: the `replay_event` decorator must drop its hard-coded `status_code=202` so the function can return either 200 (duplicate) or 202 (accepted). Change `@router.post("/replay", status_code=202)` to `@router.post("/replay")` and let the function set the status explicitly via `JSONResponse`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_events_router_dedup.py -v`
Expected: 6 passed (1 lifespan from Task 3 + 5 new).

- [ ] **Step 5: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: every gate green. The miss-path response bodies are **byte-identical to today** (`{"ok": True, "stored": <bool>}` for `ingest`; `{"status": "accepted"}` at 202 for `replay_event`) — the `"duplicate": True` field is ONLY added on the hit path (200). The audit confirmed two existing exact-equality assertions that depend on this invariant: `tests/integration/test_cli_commands.py:256` (`assert r.json() == {"status": "accepted"}` on a fresh replay) and `tests/integration/test_e2e_path3.py:63` (`assert r.json() == {"ok": True, "stored": True}` on a fresh ingest) — both pass under this plan because their POSTs are first-seen events that hit the miss path. If implementation reality diverges from "miss-path body unchanged", relax those assertions to subset checks (e.g. `r.json()["status"] == "accepted"`) — do NOT change the miss-path body.

- [ ] **Step 6: Commit**

```bash
git add redrobin/api/events_router.py tests/integration/test_events_router_dedup.py
git commit -m "feat(events): dedup gate in ingest + replay_event before append/publish"
```

---

## Task 5: Clear the dedup file on engagement archive / delete

**Files:**
- Modify: `redrobin/application/engagement_service.py`
- Test: `tests/integration/test_dedup_engagement_lifecycle.py` (NEW)

`new` already starts an engagement with an empty `working_memory.json`; the dedup file doesn't exist for a fresh engagement so it loads empty automatically — no code change there. `archive` tarballs the engagement (keeps the file IN the tarball, fine) and is also a no-op for our concern. `delete` `rm -rf`s the entire engagement dir, which sweeps the dedup file too — also a no-op.

So Task 5's only actionable scope is **adding an integration test that documents this behavior**, so a future change cannot regress it silently. We do NOT add new code in `engagement_service.py` because the existing semantics already satisfy the spec.

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_dedup_engagement_lifecycle.py` with EXACTLY this content:

```python
"""Integration tests: engagement lifecycle correctly handles the dedup_keys
file (no code in engagement_service.py — the existing semantics suffice; this
test locks the behavior so a future change cannot silently regress it)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redrobin.application.engagement_service import EngagementService


@pytest.fixture()
def svc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    return EngagementService(root=root), root


def test_new_engagement_has_no_dedup_file(svc_root) -> None:
    svc, root = svc_root
    svc.new("eng-x", name="x", client="acme", in_scope=["host-1"])
    dedup_path = root / "engagements" / "eng-x" / "dedup_keys.json"
    assert not dedup_path.exists()


def test_archive_keeps_dedup_file_inside_tarball(svc_root, tmp_path: Path) -> None:
    """archive() tarballs the engagement dir; the dedup file goes WITH it."""
    svc, root = svc_root
    svc.new("eng-y", name="y", client="acme", in_scope=["host-1"])
    dedup_path = root / "engagements" / "eng-y" / "dedup_keys.json"
    dedup_path.write_text(json.dumps({"keys": ["B-1|abc|beacon.command_output"]}),
                          encoding="utf-8")
    assert dedup_path.exists()
    tarball = svc.archive("eng-y")
    assert tarball.exists()
    # The engagement dir itself is unchanged by archive() — only the tarball
    # is produced. The dedup file is still present locally.
    assert dedup_path.exists()


def test_delete_removes_dedup_file(svc_root) -> None:
    """delete() rm -rf's the engagement dir; dedup_keys.json goes too."""
    svc, root = svc_root
    svc.new("eng-z", name="z", client="acme", in_scope=["host-1"])
    dedup_path = root / "engagements" / "eng-z" / "dedup_keys.json"
    dedup_path.write_text(json.dumps({"keys": ["B-1|abc|beacon.command_output"]}),
                          encoding="utf-8")
    assert dedup_path.exists()
    svc.delete("eng-z")
    assert not dedup_path.exists()
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run --no-sync pytest tests/integration/test_dedup_engagement_lifecycle.py -v`
Expected: 3 passed. (If `EngagementService(root=...)` doesn't accept the kwarg, adjust the fixture to use the env-driven default; the existing `engagement_service.py` constructor signature is the source of truth — read it at implementation time and adapt the fixture to match.)

- [ ] **Step 3: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: every gate green.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_dedup_engagement_lifecycle.py
git commit -m "test(dedup): lock engagement lifecycle behavior for dedup_keys.json"
```

---

## Task 6: `.cna` `BEACON_OUTPUT` sentinel content-check (SMOKE-ONLY)

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (the `set BEACON_OUTPUT { ... }` hook, currently at lines 460-470 post-B1.5 T9)

The `.cna` already has a working `BEACON_OUTPUT` filter that forwards `$5` (correlation_id) to `redrobin_emit_command_output`. We add a single early-return for lines starting with `[Red Robin]` — display is preserved (the filter still returns `$2`), but the emission to `/v1/events` is skipped.

**No CI for this change** — Aggressor isn't testable in pytest. Acceptance is the live-beacon smoke in Task 7.

- [ ] **Step 1: Replace the BEACON_OUTPUT hook**

In `adapters/cobaltstrike/redrobin.cna`, locate the `set BEACON_OUTPUT { ... }` block (the existing code is:

```sleep
set BEACON_OUTPUT {
    local('$task_id $correlation_id');
    $task_id = iff($4 !is $null, $4, "");
    $correlation_id = iff($5 !is $null, $5, "");
    redrobin_emit_command_output($1, $task_id, $2, $correlation_id);
    return $2;
}
```

) and REPLACE it with EXACTLY this:

```sleep
set BEACON_OUTPUT {
    local('$task_id $correlation_id');
    # B3 auto-obs ROOT: skip emit for Red Robin's own blogged lines.
    # Display is preserved (return $2 unchanged); only the emission to
    # /v1/events is suppressed, breaking the self-ingestion loop where CS
    # treats our blog() output as beacon output and the hook re-POSTs it.
    #
    # Uses `!is $null` + `ismatch` — both are the file's established Sleep
    # idioms (`!is $null` x15 occurrences; `ismatch` x10+ for verdict parsing
    # at lines ~86-92, ~202-212, ~373). The earlier candidate using
    # `[$str startsWith "X"]` (Sleep's Java-HOA-on-scalar) had ZERO precedent
    # in this `.cna` — and since this hook is smoke-only (no CI), the first
    # regression would surface on the operator's keyboard. Stay in-idiom.
    #
    # Regex anchor `^` makes the prefix intent explicit; `\[` and `\]` are
    # required because `[` is a regex metacharacter.
    if ($2 !is $null && $2 ismatch '^\[Red Robin\].*') {
        return $2;
    }
    $task_id = iff($4 !is $null, $4, "");
    # $5 is the per-command correlation id (verified live 2026-05-27).
    $correlation_id = iff($5 !is $null, $5, "");
    redrobin_emit_command_output($1, $task_id, $2, $correlation_id);
    return $2;
}
```

- [ ] **Step 2: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): suppress re-emit of [Red Robin] blog lines in BEACON_OUTPUT (auto-obs ROOT)"
```

(No CI run on this commit — `.cna` isn't covered. The live smoke in Task 7 is the acceptance gate.)

---

## Task 7: Full CI gate + grep-prove zero refs + smoke runbook handoff

**Files:** none (verification + handoff).

- [ ] **Step 1: Run every CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: every gate green. Pytest count = pre-B3 baseline + 9 (T1) + 14 (T2) + 6 (T3+T4) + 3 (T5) = baseline + 32.

- [ ] **Step 2: Grep-prove the gate is the only check-and-insert path**

Run: `grep -rn "check_and_insert\|DedupKeys\b" redrobin/`
Expected: hits only in `redrobin/application/dedup_keys.py` (definition), `redrobin/api/lifespan_helpers.py` (wiring), `redrobin/api/events_router.py` (use). Anywhere else = a leak.

- [ ] **Step 3: Grep-prove no stale `[Red Robin]` self-ingest claims remain**

Run: `grep -rn "Red Robin" adapters/cobaltstrike/redrobin.cna | head -20`
Expected: occurrences are inside `blog()` strings (intentional) or the new sentinel comment, NOT a `redrobin_emit_command_output` call site that includes them.

- [ ] **Step 4: Write the smoke runbook to a temp file for the operator**

Create `docs/superpowers/runbooks/2026-05-28-b3-smoke.md` with EXACTLY this content:

```markdown
# B3 Live-Beacon Smoke Runbook

**Acceptance gate** for B3 = these checks pass live, on the merged branch.

## Pre-conditions
- Branch: `wm/b3-cna-dedup` (or after merge, `dev-juan`).
- Daemon stopped.
- `ANTHROPIC_API_KEY` set in the env; **NOT** `REDROBIN_ALLOW_DEGRADED=1`.
- `manual-smoke` engagement exists with the real-machine scope.

## Setup

```powershell
# 1. Clean manual-smoke state (same block as B2's smoke).
$e = "$env:USERPROFILE\.redrobin\engagements\manual-smoke"
Remove-Item "$e\working_memory.json","$e\events.jsonl","$e\episodic.db","$e\dedup_keys.json" -ErrorAction SilentlyContinue
Remove-Item "$env:USERPROFILE\.redrobin\working_memory.json" -ErrorAction SilentlyContinue
Get-ChildItem $e | Select-Object Name   # expect only engagement.yaml
```

```powershell
# 2. Start the daemon (PS1).
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
.\.venv\Scripts\Activate.ps1
redrobin daemon start --host 127.0.0.1 --port 7777
```

```powershell
# 3. Reload the .cna in CS Script Manager
#    (Script Manager → click redrobin.cna row → Reload).
```

## Checks

| # | Action | Expected |
|---|---|---|
| 1 | In CS beacon: `shell systeminfo` | `events.jsonl` has EXACTLY 1 `beacon.command_output` for it; `inspect_layers.py` shows the host populated |
| 2 | Close + reopen the CS client (forces a beacon reconnect) | `events.jsonl` STILL has 1 (no dup); `dedup_keys.json` contains the key for systeminfo |
| 3 | CS Script Manager → reload `redrobin.cna` | `events.jsonl` STILL has 1 |
| 4 | `mimikatz sekurlsa::logonpasswords` → Layer A BLOCK | The `[Red Robin] Blocked: mimikatz — ...` line IS visible in the beacon console; `events.jsonl` has NO `beacon.command_output` entry whose `output` starts with `[Red Robin]` |
| 5 | Stop daemon, restart, reconnect CS | `dedup_keys.json` survived; previous events still deduped on re-replay |

Verify with: `python $env:USERPROFILE\.redrobin\inspect_layers.py` after each step.

## Expected counts (per command) in events.jsonl

| Command | Pre-B3 (post-B2) | Post-B3 |
|---|---|---|
| `systeminfo` | grows by 1 + N per reconnect | grows by **exactly 1** |
| `mimikatz` (BLOCKED) | 1 command_input + N `[Red Robin]` command_output lines | 1 command_input + **zero** `[Red Robin]` command_outputs |
```

- [ ] **Step 5: Commit the runbook and tag the final whole-branch review**

```bash
git add docs/superpowers/runbooks/2026-05-28-b3-smoke.md
git commit -m "docs(b3): smoke runbook for the live-beacon acceptance gate"
```

- [ ] **Step 6: Dispatch the final whole-branch code review subagent** (subagent-driven-development handles this) and address every finding.

- [ ] **Step 7: Hand the smoke runbook to the operator**

After the final code review passes, do NOT auto-merge. Hand the runbook + branch to the operator with a summary (commits, CI status, expected count change). The operator runs the live smoke; only on green does the operator merge `wm/b3-cna-dedup` → `dev-juan`.

---

## Self-Review

**1. Spec coverage:** every section of `2026-05-28-cna-event-dedup-design.md` mapped to a task:
- §4.1 (#152 dedup) → T1 (repo), T2 (DedupKeys + derive_key), T3 (lifespan), T4 (gate in ingest + replay).
- §4.2 (auto-obs ROOT) → T6 (.cna).
- §5 (files) → covered by T1-T6 file lists.
- §6 (test plan unit + integration) → T1-T5 tests.
- §6 (smoke) → T7 runbook.
- §7 (acceptance criteria) → T7 grep-prove + smoke checks.

**2. Placeholder scan:** all code blocks contain real code; all commands have expected output; no "TBD"/"similar to X"/"add error handling" without specifics. T5's "no code change in engagement_service.py" is an explicit decision (with reasoning), not a placeholder. The `app.py` modification in T3 says "match the file's actual orchestration shape" because the orchestrator's exact layout depends on the file's state at execution time — this is a directed instruction (read the file, integrate uniformly), not a TBD.

**3. Type consistency:**
- `DedupKeys.load(repo: DedupKeysRepo) -> DedupKeys` (classmethod), `check_and_insert(key: str) -> bool`, `clear() -> None` — used identically in all referencing tasks.
- `derive_key(event: dict[str, Any]) -> str | None` — used identically.
- `DedupKeysRepo(path: Path)`, `load() -> set[str]`, `save(set[str]) -> None`, `clear() -> None` — consistent.
- Composite key format: `"<bid>|<corr>|<type>"` for command events; `"<bid>||<type>"` for `beacon.initial`; both formats appear in T1/T2 tests and the `derive_key` implementation.

**4. Keep-green-between-tasks:** T1 + T2 build new modules in isolation (no breaking change). T3 attaches `app.state.dedup_keys` but the events router doesn't yet read it. T4 wires the gate — at this point, the new integration tests pass; the existing tests that POST one envelope still pass (one event per test ⇒ no dup hits). T5 only adds tests, no code change. T6 is `.cna`-only (no CI). T7 is verification + handoff. The suite is green after every task.
