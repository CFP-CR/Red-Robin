# Operational Journal + `search_journal` retrieval tool — Task 3 (#24) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Operational Journal — the 6th memory layer (engagement axis): index the verbatim `(command, output)` transcript that already exists in `events.jsonl` (joined by `correlation_id`) with Voyage embeddings, and expose a `search_journal` PULL tool that plugs into the tool registry Task 2 (#23) built.

**Architecture:** A new application service `JournalService` mirrors the existing `KBService` (application/kb_service.py) — same `retrieve(query, *, top_k) -> list[(text, source, score)]` shape over the same infra (`VoyageClient` + numpy `VectorStore`). It differs in its *source* (the engagement's `events.jsonl`, joined `beacon.command_input`+`beacon.command_output` by `correlation_id`) and in that it stays **current**: on each retrieve it does an **incremental refresh** — reading only the events.jsonl lines appended since the last index and embedding the new joined entries — so `search_journal` always sees the live session. A `search_journal` `AgentTool` is appended to `build_tool_registry` (additive, backward-compatible) and wired at assistant startup. This task adds **no new capture** and **no trigger changes** — it indexes existing data and adds one retrieval tool.

**Tech Stack:** Python 3.12, `voyageai` (embeddings), numpy `VectorStore`, native Anthropic Tool Runner (via the Task-2 `AgentTool`/`AgentLLM` seam), `pytest` + `pytest-asyncio`, `uv`, `ruff`, `mypy --strict` (domain+application), `import-linter`.

## Global Constraints

_Every task's requirements implicitly include this section. Values copied verbatim from the spec (`docs/superpowers/specs/2026-07-11-ai-assistant-design.md`), `CLAUDE.md`, `CONTRIBUTING.md`, and issue #24._

- **The 5 CI gates must pass** (verbatim from `CLAUDE.md`): `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy redrobin/domain redrobin/application` · `uv run lint-imports --config importlinter.cfg` · `uv run pytest tests/ -v --tb=short`. Run with `--no-sync` when the daemon is up (it locks `redrobin.exe`).
- **`ruff` default rule set is live, including `E402` (module-import-not-at-top-of-file)** — the repo has been bitten by this before (commit `cde29f6`). Every test/impl file keeps ALL imports at the very top; never place an import after a function.
- **TDD (red→green)**: failing test first, verify red, minimal implementation, verify green, commit (CONTRIBUTING).
- **Atomic conventional commits, NO AI co-author trailer** — do not add `Co-Authored-By: Claude` or any AI trailer (CONTRIBUTING; project override).
- **Layered architecture (import-linter, 4 contracts, verified KEPT during audit)**: `api|tui → application → domain`; domain never imports infra; application never imports presentation; presentation never imports infra directly. `JournalService` lives in `application/` and MAY import `infrastructure/` (VoyageClient, VectorStore) — `KBService` already does exactly this and is in the 4-KEPT graph; it is allowed.
- **`mypy --strict`** covers `redrobin/domain` + `redrobin/application` only (NOT `tests/`). `JournalService`, the join, and the tool additions must be fully typed.
- **Never-crash + degraded-None** (spec §6.E, DoD §7): missing `VOYAGE_API_KEY`, no active engagement, a missing/empty/malformed `events.jsonl`, an unreadable file, or any indexing/retrieval failure → the journal degrades (tool reports "unavailable" / "no match"), the daemon still boots, no exception escapes a cycle. Mirror `kb_retrieve` (`tools.py`) and the enricher's swallow-and-log (`enricher.py:87-88`).
- **Observability** (spec §6.E): `assistant:`-prefixed logs (e.g. `assistant: journal indexed N entries`); no new bus event type is required for Task 3.
- **Plug into the seam, do NOT re-architect** (issue #24 out-of-scope): `search_journal` is *appended* to the `list[AgentTool]` that `build_tool_registry` returns; the **output contract `run_cycle` consumes (`list[AgentTool]`) is unchanged**, and the push/pull split is unchanged. See Design Decision D1.
- **No new data capture** (spec §6.B, §9; issue #24): index the *existing* `events.jsonl`; do NOT add event producers or touch the `.cna`.
- **No trigger changes** (issue #24): add a retrieval tool only. The incremental refresh (D4) is *tool-internal* (it runs inside the `search_journal` handler / `JournalService.retrieve`) — it is NOT a bus subscription or a new trigger.
- **Local gotcha**: `REDROBIN_ALLOW_DEGRADED=1` for tests that hit the boot-gate without API keys.

## Design Decisions (locked in-plan from the spec — no re-brainstorm; audited 2026-07-20)

- **D1 — `search_journal` reaches the registry via an additive optional param** `journal: JournalService | None = None`. Issue #24 says "append without changing the signature," but Task 2's signature is `kb`-only, so a journal backend needs *some* input. This additive optional param keeps all 8 existing call sites working (audit-verified: every caller passes a single positional arg) and leaves the protected seam — the `list[AgentTool]` output `run_cycle` consumes, and the push/pull split — unchanged. The rejected alternative (self-resolve backends inside the registry) would force `build_tool_registry` to become `async` and couple pure assembly to infra; the "caller appends" alternative fragments tool assembly away from the seam. Confirmed safe by all three auditors.
- **D2 — `JournalService` lives at `application/journal_service.py`** (a sibling application module, the exact peer of `KBService`), NOT under `agents/assistant/`. It is a memory-layer service (engagement axis); placing it beside `KBService` (knowledge axis) maximizes the mirror. Audit-confirmed layering-legal.
- **D3 — The join keys on `correlation_id` only** (spec §6.B is explicit). Events lacking a `correlation_id` are skipped. Audit-verified this is *more* correct offline than the enricher's live per-`bid` fallback (which would mis-join orphan outputs to the last input on that bid), and the CS adapter emits `correlation_id` on both events in practice (`adapters/cobaltstrike/redrobin.cna:284,301`), so the journal has real data.
- **D4 — The index stays CURRENT via an incremental refresh-on-retrieve** (operator decision 2026-07-20, "production-grade and really useful"). `events.jsonl` is per-engagement and append-only (`events_router.py:103` opens `"a"`). `JournalService` tracks how many lines it has already indexed; each `retrieve` first calls `refresh()`, which reads only the newly-appended lines, joins them (continuing a persistent `correlation_id → command` map so an output pairs with an input read in an earlier chunk), embeds the new entries (batched), and appends them to the store. Cost is O(new entries) per call, not O(whole transcript). This makes `search_journal` see the current session — the reactive advisor's highest-value query — while staying tool-internal (no trigger, no bus subscription). The backlog is warmed once at boot in `build_journal_service` (off the reactive hot path).
- **D5 — Acceptance form = deterministic only, NO operator smoke** (issue #24 asks this be decided in-plan, spec §8). Task 3 adds no live surface — no TUI/dock change, no reducer change, no `.cna` change; it reads a *file*, not a live beacon. The join, the incremental refresh, and the tool are exercised deterministically over a seeded/appended `events.jsonl` fixture → the 5 CI gates + the new tests ARE the acceptance. **Recommended one-time confirmation (not a required gate):** during the *next* Task-2 reactive smoke, an operator eyeballs whether the advisor ever cites a `journal:` source — the one thing units can't prove is that *this* operator's live captures carry `correlation_id`.

## File Structure

**Create:**
- `redrobin/application/journal_service.py` — `JournalEntry` (frozen dataclass), `join_transcript` (pure, persistent-map aware), `_event_bodies` (tolerant jsonl→event-body parser), `JournalService` (incremental `refresh` + `retrieve`). Mirrors `kb_service.py`.
- `tests/unit/test_journal_service.py` — the pure join + the service's incremental refresh over a seeded+appended `events.jsonl` (fake Voyage via `AsyncMock`). Mirrors `tests/unit/test_kb_service.py`.
- `tests/unit/agents/assistant/test_tools_journal.py` — the `search_journal` tool + `build_journal_service` degraded paths.

**Modify:**
- `redrobin/application/agents/assistant/tools.py` — add `build_journal_service()` (async, degraded-None) + `_SEARCH_JOURNAL_SCHEMA`; add optional `journal` param to `build_tool_registry` and append the `search_journal` `AgentTool`; update the module docstring + `__all__`.
- `redrobin/api/lifespan_helpers.py:~536-576` (`start_assistant`) — `journal = await build_journal_service()`; pass `journal=journal` into `build_tool_registry(...)`; update the import (line 33) + the degraded-None docstring.
- `tests/unit/agents/assistant/test_tools.py` — the **one** assertion `== ["kb_retrieve"]` (line 25, `test_registry_exposes_kb_retrieve`) becomes `== ["kb_retrieve", "search_journal"]`. (Audit confirmed this is the ONLY tool-name-list assertion; the other `build_tool_registry` callers index `tools[0]` = kb and are unaffected.)

**Untouched (verify, do not modify):** `domain/assistant.py`, `agent.py`, `assistant.py`, `parser.py`, `triggers.py`, `llm.py`, `anthropic_client.py`, the TUI, the reducer, the `.cna`, `config.py` (no new Settings — the journal reuses `VOYAGE_API_KEY` and the active engagement).

---

### Task 3.1: Pure transcript join (`JournalEntry` + `join_transcript`)

Turn the two-separate-events `events.jsonl` into verbatim `(command, output)` entries, keyed by `correlation_id`. Pure — no I/O, no infra. The `commands` map is an optional caller-owned dict so the same function serves both a one-shot join (tests) and the incremental refresh (Task 3.2, which passes a persistent map).

**Files:**
- Create: `redrobin/application/journal_service.py`
- Test: `tests/unit/test_journal_service.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class JournalEntry: correlation_id: str; command: str; output: str`
  - `def join_transcript(event_bodies: Iterable[dict[str, Any]], commands: dict[str, str] | None = None) -> list[JournalEntry]`

- [ ] **Step 1: Write the failing test** (ALL imports at the top of the file — E402)

```python
# tests/unit/test_journal_service.py
from __future__ import annotations

from redrobin.application.journal_service import JournalEntry, join_transcript


def test_join_pairs_input_and_output_by_correlation_id():
    bodies = [
        {"type": "beacon.command_input", "correlation_id": "c1", "raw": "whoami /all", "command": "whoami"},
        {"type": "beacon.command_output", "correlation_id": "c1", "output": "nt authority\\system"},
    ]
    assert join_transcript(bodies) == [
        JournalEntry(correlation_id="c1", command="whoami /all", output="nt authority\\system")
    ]


def test_join_prefers_raw_then_command():
    bodies = [
        {"type": "beacon.command_input", "correlation_id": "c2", "command": "ls"},  # no raw
        {"type": "beacon.command_output", "correlation_id": "c2", "output": "file.txt"},
    ]
    assert join_transcript(bodies)[0].command == "ls"


def test_join_skips_events_without_correlation_id():
    bodies = [
        {"type": "beacon.command_input", "raw": "no-corr"},
        {"type": "beacon.command_output", "output": "orphan"},
    ]
    assert join_transcript(bodies) == []


def test_join_output_without_matching_input_yields_empty_command():
    bodies = [{"type": "beacon.command_output", "correlation_id": "c3", "output": "stray output"}]
    assert join_transcript(bodies) == [JournalEntry(correlation_id="c3", command="", output="stray output")]


def test_join_ignores_unrelated_event_types():
    bodies = [
        {"type": "beacon.initial", "correlation_id": "cx", "host": "WS01"},
        {"type": "beacon.command_input", "correlation_id": "c4", "raw": "ipconfig"},
        {"type": "beacon.command_output", "correlation_id": "c4", "output": "10.0.0.5"},
    ]
    assert [e.command for e in join_transcript(bodies)] == ["ipconfig"]


def test_join_persistent_commands_map_pairs_across_calls():
    # The input is in one batch, its output in the next — a shared `commands` map
    # lets the second call pair them (this is what the incremental refresh relies on).
    commands: dict[str, str] = {}
    first = join_transcript([{"type": "beacon.command_input", "correlation_id": "c5", "raw": "netstat"}], commands)
    assert first == []  # input only, nothing to emit yet
    second = join_transcript([{"type": "beacon.command_output", "correlation_id": "c5", "output": "LISTENING"}], commands)
    assert second == [JournalEntry(correlation_id="c5", command="netstat", output="LISTENING")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/test_journal_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'redrobin.application.journal_service'`.

- [ ] **Step 3: Write minimal implementation**

```python
# redrobin/application/journal_service.py
"""redrobin/application/journal_service.py — the Operational Journal (6th memory
layer, engagement axis).

The verbatim (command, output) transcript of the operation, so the assistant can
re-read the exact text of any past moment (the other layers are distilled/lossy).
Mirrors KBService (application/kb_service.py) — same VoyageClient + VectorStore
infra, same retrieve() shape — but its source is the engagement's events.jsonl,
NOT curated markdown, and it stays current via an incremental refresh. The raw
material already exists as TWO separate events (beacon.command_input +
beacon.command_output) correlated by correlation_id (spec §6.B), so this module
joins them; it captures no new data.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redrobin.infrastructure.llm.voyage_client import VoyageClient
from redrobin.infrastructure.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Per-entry output cap so one huge command output can't dominate the index or blow
# the agent's context (spec §6.B refinement 2/3: cap, high-signal excerpts not dumps).
_MAX_ENTRY_CHARS = 2000
# Embed in batches so a large backlog degrades to a partial index on a Voyage
# batch-limit error, never all-or-nothing (audit finding).
_EMBED_BATCH = 128


@dataclass(frozen=True)
class JournalEntry:
    """One verbatim moment: the command the operator ran and the output it produced,
    joined from the two events.jsonl events sharing this correlation_id."""

    correlation_id: str
    command: str
    output: str


def join_transcript(
    event_bodies: Iterable[dict[str, Any]], commands: dict[str, str] | None = None
) -> list[JournalEntry]:
    """Join beacon.command_input + beacon.command_output by correlation_id into
    verbatim (command, output) entries (spec §6.B). Events without a correlation_id
    are skipped (the offline index keys on the stable id; the enricher's per-bid
    fallback is a live-stream recovery, not applicable here). An output with no
    seen input yields an empty command (still a verbatim record).

    `commands` is an optional caller-owned correlation_id -> command map. When the
    same dict is passed across successive calls (the incremental refresh), an output
    pairs with an input read in an earlier call. Omit it for a one-shot join."""
    commands = {} if commands is None else commands
    entries: list[JournalEntry] = []
    for body in event_bodies:
        corr = body.get("correlation_id")
        if corr is None:
            continue
        corr = str(corr)
        etype = body.get("type")
        if etype == "beacon.command_input":
            commands[corr] = str(body.get("raw") or body.get("command") or "")
        elif etype == "beacon.command_output":
            entries.append(
                JournalEntry(
                    correlation_id=corr,
                    command=commands.get(corr, ""),
                    output=str(body.get("output") or ""),
                )
            )
    return entries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/test_journal_service.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/journal_service.py tests/unit/test_journal_service.py
git commit -m "feat(journal): pure correlation_id join of the events.jsonl transcript"
```

---

### Task 3.2: `JournalService` — incremental refresh + retrieve

Wrap the pure join with a tolerant jsonl parser + an incremental refresh (reads only newly-appended lines, embeds the delta, keeps the store current) + retrieval, mirroring `KBService.retrieve`.

**Files:**
- Modify: `redrobin/application/journal_service.py`
- Test: `tests/unit/test_journal_service.py`

**Interfaces:**
- Consumes: `join_transcript` (3.1); `VoyageClient.embed_documents(texts) -> EmbeddingsResult(.vectors)`; `VoyageClient.embed_query(text) -> list[float]`; `VectorStore.add(*, ids, texts, vectors, metadatas)` / `.search(*, query_vector, top_k) -> list[(VectorRecord, float)]` / `.count()`.
- Produces:
  - `def _event_bodies(lines: Iterable[str]) -> list[dict[str, Any]]` (lifts the `event` body out of each wire-envelope line; tolerates blank/malformed lines)
  - `class JournalService:` `__init__(self, *, voyage, store, events_path: Path)` · `async def refresh(self) -> int` · `async def retrieve(self, query: str, *, top_k: int = 5) -> list[tuple[str, str, float]]`

- [ ] **Step 1: Write the failing test** (add these imports to the TOP block of `tests/unit/test_journal_service.py`, then the test bodies below)

```python
# --- top of tests/unit/test_journal_service.py: the full import block ---
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from redrobin.application.journal_service import (
    JournalEntry,
    JournalService,
    _event_bodies,
    join_transcript,
)
from redrobin.infrastructure.vector_store import VectorStore
```

```python
# --- test bodies (after the Task 3.1 tests) ---
def _envelope(event: dict) -> str:
    return json.dumps({"spec_version": "0.1", "id": "x", "ts": "t", "source": "cs", "event": event})


def _fixed_voyage() -> AsyncMock:
    """A fake Voyage whose embeddings match the batch size on every call."""
    voyage = AsyncMock()
    voyage.embed_documents = AsyncMock(
        side_effect=lambda texts: type("R", (), {"vectors": [[1.0, 0.0]] * len(texts), "total_tokens": 0})()
    )
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    return voyage


def test_event_bodies_lifts_envelope_and_tolerates_garbage():
    lines = [
        _envelope({"type": "beacon.command_input", "correlation_id": "c1", "raw": "mimikatz"}),
        "   ",  # blank tolerated
        "{not valid json",  # malformed tolerated
        _envelope({"type": "beacon.command_output", "correlation_id": "c1", "output": "svc_sql:hash"}),
    ]
    bodies = _event_bodies(lines)
    assert [b.get("type") for b in bodies] == ["beacon.command_input", "beacon.command_output"]


@pytest.mark.asyncio
async def test_refresh_indexes_joined_entries(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope({"type": "beacon.command_input", "correlation_id": "c1", "raw": "mimikatz sekurlsa::logonpasswords"}),
                _envelope({"type": "beacon.command_output", "correlation_id": "c1", "output": "svc_sql:hash:aad3b..."}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 1
    assert store.count() == 1


@pytest.mark.asyncio
async def test_refresh_is_incremental(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope({"type": "beacon.command_input", "correlation_id": "c1", "raw": "whoami"}),
                _envelope({"type": "beacon.command_output", "correlation_id": "c1", "output": "system"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 1
    # append a NEW command; refresh picks up ONLY the new entry (not the whole file)
    with path.open("a", encoding="utf-8") as f:
        f.write(_envelope({"type": "beacon.command_input", "correlation_id": "c2", "raw": "ipconfig"}) + "\n")
        f.write(_envelope({"type": "beacon.command_output", "correlation_id": "c2", "output": "10.0.0.5"}) + "\n")
    assert await svc.refresh() == 1
    assert store.count() == 2
    # nothing appended -> no-op
    assert await svc.refresh() == 0


@pytest.mark.asyncio
async def test_refresh_joins_output_to_input_from_an_earlier_chunk(tmp_path):
    # The input is written first, refreshed (remembered, nothing emitted), then the
    # output is appended and refreshed — the persistent commands map pairs them.
    path = tmp_path / "events.jsonl"
    path.write_text(_envelope({"type": "beacon.command_input", "correlation_id": "c9", "raw": "net group"}) + "\n", encoding="utf-8")
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 0  # input only
    with path.open("a", encoding="utf-8") as f:
        f.write(_envelope({"type": "beacon.command_output", "correlation_id": "c9", "output": "Domain Admins"}) + "\n")
    assert await svc.refresh() == 1
    hits = await svc.retrieve("who is in domain admins", top_k=1)
    assert "net group" in hits[0][0] and "Domain Admins" in hits[0][0]


@pytest.mark.asyncio
async def test_retrieve_self_refreshes_then_returns_text_source_score(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope({"type": "beacon.command_input", "correlation_id": "c1", "raw": "mimikatz"}),
                _envelope({"type": "beacon.command_output", "correlation_id": "c1", "output": "svc_sql:hash"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    svc = JournalService(voyage=_fixed_voyage(), store=VectorStore(), events_path=path)
    # no explicit refresh() — retrieve() must catch the index up itself
    hits = await svc.retrieve("what credentials did we dump", top_k=1)
    assert len(hits) == 1
    text, source, _score = hits[0]
    assert "svc_sql" in text
    assert source == "journal:c1"


@pytest.mark.asyncio
async def test_refresh_missing_file_returns_zero(tmp_path):
    svc = JournalService(voyage=_fixed_voyage(), store=VectorStore(), events_path=tmp_path / "nope.jsonl")
    assert await svc.refresh() == 0


@pytest.mark.asyncio
async def test_retrieve_on_empty_store_returns_empty(tmp_path):
    svc = JournalService(voyage=_fixed_voyage(), store=VectorStore(), events_path=tmp_path / "nope.jsonl")
    assert await svc.retrieve("anything") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/test_journal_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'JournalService'` / `_event_bodies`.

- [ ] **Step 3: Write minimal implementation** (append to `redrobin/application/journal_service.py`)

```python
def _event_bodies(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Parse events.jsonl lines → event bodies. Each line is a wire envelope
    ({spec_version, id, ts, source, event:{type,...}}); lift the flat `event` body
    (which carries `type`, `correlation_id`, `raw`, `output`, ...). Tolerate a
    pre-flattened dict, blank lines, and malformed JSON (skip) so a partial or
    hand-crafted trace never crashes indexing (never-crash, spec §6.E)."""
    bodies: list[dict[str, Any]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        event = data.get("event")
        bodies.append(event if isinstance(event, dict) else data)
    return bodies


def _entry_text(entry: JournalEntry) -> str:
    """The verbatim moment as one embeddable/citable excerpt, output capped."""
    output = entry.output[:_MAX_ENTRY_CHARS]
    return f"$ {entry.command}\n{output}" if entry.command else output


class JournalService:
    """Index + retrieve the engagement's verbatim transcript, kept current by an
    incremental refresh that reads only the events.jsonl lines appended since the
    last index. Mirrors KBService's retrieve() shape."""

    def __init__(self, *, voyage: VoyageClient, store: VectorStore, events_path: Path) -> None:
        self._voyage = voyage
        self._store = store
        self._events_path = events_path
        self._consumed = 0  # number of COMPLETE lines already indexed
        self._commands: dict[str, str] = {}  # persistent corr->command across refreshes
        self._seq = 0  # monotonic, for unique store ids (multiple outputs may share a corr)
        self._lock = asyncio.Lock()  # serialize concurrent refreshes (no double-index)

    async def refresh(self) -> int:
        """Read the events.jsonl lines appended since the last refresh, join them
        (continuing the persistent corr->command map), embed the new entries in
        batches, and add them to the store. Returns entries added this call. A
        trailing partial line (no newline yet) is left for the next refresh.
        Never raises past a logged warning (never-crash, spec §6.E)."""
        async with self._lock:
            if not self._events_path.exists():
                return 0
            try:
                text = self._events_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                logger.exception("assistant: journal could not read %s", self._events_path)
                return 0
            # Split on newline; the last element is "" (file ended with \n) or a
            # still-being-written partial line — either way, don't consume it yet.
            complete = text.split("\n")[:-1]
            new_lines = complete[self._consumed :]
            self._consumed = len(complete)
            entries = join_transcript(_event_bodies(new_lines), self._commands)
            if not entries:
                return 0
            added = 0
            for i in range(0, len(entries), _EMBED_BATCH):
                batch = entries[i : i + _EMBED_BATCH]
                texts = [_entry_text(e) for e in batch]
                ids = [f"journal:{e.correlation_id}#{self._seq + j}" for j, e in enumerate(batch)]
                metas: list[dict[str, Any]] = [
                    {"source": f"journal:{e.correlation_id}", "command": e.command} for e in batch
                ]
                result = await self._voyage.embed_documents(texts)
                self._store.add(ids=ids, texts=texts, vectors=result.vectors, metadatas=metas)
                self._seq += len(batch)
                added += len(batch)
            return added

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[tuple[str, str, float]]:
        """Catch the index up to the latest events.jsonl, then return the top-K
        matching moments. Each entry: (text, source, similarity)."""
        await self.refresh()
        if self._store.count() == 0:
            return []
        query_vec = await self._voyage.embed_query(query)
        hits = self._store.search(query_vector=query_vec, top_k=top_k)
        return [(rec.text, rec.metadata.get("source", ""), score) for rec, score in hits]
```

Add to the bottom of the file:

```python
__all__ = ["JournalEntry", "JournalService", "join_transcript"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/test_journal_service.py -v`
Expected: PASS (the 6 join tests from 3.1 + the 8 new).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/journal_service.py tests/unit/test_journal_service.py
git commit -m "feat(journal): JournalService with incremental refresh + retrieve (mirrors KBService)"
```

---

### Task 3.3: `build_journal_service()` + `search_journal` tool in the registry

Add the degraded-None async factory (warms the backlog once at boot) and append the `search_journal` `AgentTool` to `build_tool_registry` (Design Decision D1).

**Files:**
- Modify: `redrobin/application/agents/assistant/tools.py`
- Test: create `tests/unit/agents/assistant/test_tools_journal.py`; modify `tests/unit/agents/assistant/test_tools.py`

**Interfaces:**
- Consumes: `JournalService` (3.2); `AgentTool` (domain); `EngagementService().current() -> str | None`; `redrobin_root() -> Path`.
- Produces:
  - `async def build_journal_service() -> JournalService | None`
  - `def build_tool_registry(kb: KBService | None, journal: JournalService | None = None) -> list[AgentTool]` (now returns `[kb_retrieve, search_journal]`)

- [ ] **Step 1: Write the failing tests** (all imports at the top)

```python
# tests/unit/agents/assistant/test_tools_journal.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from redrobin.application.agents.assistant.tools import build_journal_service, build_tool_registry
from redrobin.application.journal_service import JournalService
from redrobin.infrastructure.vector_store import VectorStore


def _seeded_journal() -> JournalService:
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    store = VectorStore()
    store.add(
        ids=["journal:c1#0"],
        texts=["$ mimikatz sekurlsa::logonpasswords\nsvc_sql:hash:aad3b..."],
        vectors=[[1.0, 0.0]],
        metadatas=[{"source": "journal:c1"}],
    )
    # events_path points nowhere -> retrieve()'s self-refresh is a no-op, so the
    # test exercises retrieval over the pre-seeded store deterministically.
    return JournalService(voyage=voyage, store=store, events_path=Path("does-not-exist.jsonl"))


def test_registry_exposes_kb_and_journal_tools():
    tools = build_tool_registry(None, _seeded_journal())
    assert [t.name for t in tools] == ["kb_retrieve", "search_journal"]
    assert tools[1].input_schema["required"] == ["query"]


@pytest.mark.asyncio
async def test_search_journal_returns_seeded_moment():
    tools = build_tool_registry(None, _seeded_journal())
    out = await tools[1].handler({"query": "what creds did we dump"})
    assert "journal:c1" in out
    assert "svc_sql" in out


@pytest.mark.asyncio
async def test_search_journal_degraded_without_journal_is_graceful():
    tools = build_tool_registry(None, None)  # journal defaults None
    out = await tools[1].handler({"query": "anything"})
    assert "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_search_journal_empty_query_is_handled():
    tools = build_tool_registry(None, _seeded_journal())
    out = await tools[1].handler({})
    assert "no query" in out.lower()


@pytest.mark.asyncio
async def test_search_journal_no_hits_reports_not_dumps():
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    empty = JournalService(voyage=voyage, store=VectorStore(), events_path=Path("does-not-exist.jsonl"))
    tools = build_tool_registry(None, empty)
    out = await tools[1].handler({"query": "nothing indexed"})
    assert out == "No matching journal entries found."


@pytest.mark.asyncio
async def test_search_journal_retrieval_failure_is_graceful():
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(side_effect=RuntimeError("voyage unreachable"))
    store = VectorStore()
    store.add(ids=["journal:c1#0"], texts=["x"], vectors=[[1.0, 0.0]], metadatas=[{"source": "journal:c1"}])
    journal = JournalService(voyage=voyage, store=store, events_path=Path("does-not-exist.jsonl"))
    tools = build_tool_registry(None, journal)
    out = await tools[1].handler({"query": "anything"})
    assert out == "Operational journal retrieval failed."


@pytest.mark.asyncio
async def test_build_journal_service_none_without_active_engagement(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    from redrobin.application.engagement_service import EngagementService

    monkeypatch.setattr(EngagementService, "current", lambda self: None)
    assert await build_journal_service() is None


@pytest.mark.asyncio
async def test_build_journal_service_degraded_without_voyage_key(monkeypatch, tmp_path):
    # Force an active engagement so the factory reaches VoyageClient() (not the
    # no-engagement early return); with VOYAGE_API_KEY unset, VoyageClient() raises
    # RuntimeError -> degrades to None. Hermetic (REDROBIN_ROOT=tmp_path).
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from redrobin.application.engagement_service import EngagementService

    monkeypatch.setattr(EngagementService, "current", lambda self: "eng-x")
    assert await build_journal_service() is None
```

Also update `tests/unit/agents/assistant/test_tools.py`: change the single assertion at `test_registry_exposes_kb_retrieve` from `assert [t.name for t in tools] == ["kb_retrieve"]` to `== ["kb_retrieve", "search_journal"]`. (Lines using `tools[0]` stay — kb is still index 0.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_tools_journal.py tests/unit/agents/assistant/test_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_journal_service'`, and the registry returns only `["kb_retrieve"]`.

- [ ] **Step 3: Write minimal implementation** (edit `redrobin/application/agents/assistant/tools.py`)

Add imports + schema + cap at the TOP (with the existing top-of-file imports — E402):

```python
from redrobin.application.journal_service import JournalService

_MAX_JOURNAL_CHARS = 6000

_SEARCH_JOURNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "A question about something already run or observed in THIS engagement, "
                "e.g. 'the exact output when we dumped LSASS' or 'when did we first see host DC01'."
            ),
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}
```

Add the factory:

```python
async def build_journal_service() -> JournalService | None:
    """Build + warm the current engagement's Operational Journal (Voyage over the
    joined events.jsonl transcript). Returns None without VOYAGE_API_KEY, with no
    active engagement, or on any indexing failure — the assistant still runs;
    search_journal reports it's unavailable. Async because it warms the backlog at
    boot (off the reactive hot path); the tool keeps it current via retrieve()'s
    incremental refresh. Uses EngagementService().current() — the same source
    events_router uses to decide where events.jsonl is written — so the journal
    reads exactly the file the events were written to. Mirrors build_kb_service."""
    try:
        from redrobin.application.engagement_service import EngagementService
        from redrobin.infrastructure.filesystem import redrobin_root
        from redrobin.infrastructure.llm.voyage_client import VoyageClient
        from redrobin.infrastructure.vector_store import VectorStore

        active = EngagementService().current()
        if active is None:
            logger.info("assistant: no active engagement — search_journal degraded")
            return None
        events_path = redrobin_root() / "engagements" / active / "events.jsonl"
        service = JournalService(voyage=VoyageClient(), store=VectorStore(), events_path=events_path)
        n = await service.refresh()
        logger.info("assistant: journal indexed %d entries (engagement=%s)", n, active)
        return service
    except RuntimeError:
        logger.info("assistant: VOYAGE_API_KEY missing — search_journal degraded")
        return None
    except Exception:
        logger.exception("assistant: journal indexing failed — search_journal degraded")
        return None
```

Change `build_tool_registry` to take the optional param and append the tool (keep `_kb_retrieve` and the `kb_retrieve` AgentTool EXACTLY as they are — do not alter them):

```python
def build_tool_registry(
    kb: KBService | None, journal: JournalService | None = None
) -> list[AgentTool]:
    """The pull-tool set for one cycle. kb_retrieve (curated tradecraft) +
    search_journal (this engagement's verbatim transcript). Adding a tool is a
    pure append — the list[AgentTool] the agent loop consumes is the seam
    (spec §6.F); `journal` is an additive, backward-compatible param (D1)."""

    async def _kb_retrieve(args: dict[str, Any]) -> str:
        ...  # UNCHANGED — leave the existing body exactly as-is

    async def _search_journal(args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "No query provided."
        if journal is None:
            return "Operational journal unavailable (no transcript indexed / VOYAGE_API_KEY not set)."
        try:
            hits = await journal.retrieve(query, top_k=5)
        except Exception:
            logger.exception("assistant: search_journal failed for query=%s", query)
            return "Operational journal retrieval failed."
        if not hits:
            return "No matching journal entries found."
        lines = [f"[source: {source} | score={score:.2f}]\n{text}" for text, source, score in hits]
        return "\n\n".join(lines)[:_MAX_JOURNAL_CHARS]

    return [
        AgentTool(
            name="kb_retrieve",
            description=(...),  # UNCHANGED — leave the existing description exactly as-is
            input_schema=_KB_RETRIEVE_SCHEMA,
            handler=_kb_retrieve,
        ),
        AgentTool(
            name="search_journal",
            description=(
                "Search the operational journal — the verbatim transcript of THIS engagement's "
                "past commands and their exact output. CALL THIS whenever you need the precise "
                "text of something already run or seen (a prior command's output, when a host or "
                "credential was first observed, what a tool actually returned) instead of relying "
                "on the distilled working-memory summary — then cite the returned journal sources."
            ),
            input_schema=_SEARCH_JOURNAL_SCHEMA,
            handler=_search_journal,
        ),
    ]
```

Update `__all__`:

```python
__all__ = ["build_journal_service", "build_kb_service", "build_tool_registry"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_tools_journal.py tests/unit/agents/assistant/test_tools.py -v`
Expected: PASS (new journal-tool tests + the updated kb-registry assertion).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/tools.py tests/unit/agents/assistant/test_tools_journal.py tests/unit/agents/assistant/test_tools.py
git commit -m "feat(journal): append search_journal to the tool registry (+ degraded-None factory)"
```

---

### Task 3.4: Wire the journal into `start_assistant`

Build + warm the journal at assistant startup and hand it to the registry, degraded-None all the way (mirrors the `build_kb_service()` wiring already present).

**Files:**
- Modify: `redrobin/api/lifespan_helpers.py` (import line 33; `start_assistant` body ~line 567-576; docstring ~542)
- Test: `tests/integration/test_assistant_lifespan.py`

**Interfaces:**
- Consumes: `build_journal_service` (3.3), `build_tool_registry(kb, journal=...)` (3.3).

_Note on TDD: a lifespan wiring line has no honest failing-first unit test — the tool's behavior is already proven red→green in Task 3.3 (`test_tools_journal.py`). This step's test is a **regression guard** (the assistant still boots with the journal wired + degraded), not a TDD red. That is the correct shape for an integration wiring change._

- [ ] **Step 1: Write the regression-guard test** (imports at top; `create_app` imported inside the function, matching the existing sibling at `test_assistant_lifespan.py:67`)

```python
# append to tests/integration/test_assistant_lifespan.py
@pytest.mark.asyncio
async def test_lifespan_starts_assistant_with_journal_degraded(tmp_path, monkeypatch):
    # No VOYAGE_API_KEY -> build_journal_service() returns None; the assistant still
    # boots with both tools present (search_journal degrades to "unavailable").
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-journal")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.assistant_task is not None
        assert not app.state.assistant_task.done()
    assert app.state.assistant_task.done()
    assert app.state.assistant_task.cancelled() is False
```

- [ ] **Step 2: Implement the wiring** (edit `redrobin/api/lifespan_helpers.py`)

Update the import (line 33):

```python
from redrobin.application.agents.assistant.tools import (
    build_journal_service,
    build_kb_service,
    build_tool_registry,
)
```

In `start_assistant`, resolve the backends before constructing `Assistant(...)`:

```python
    kb = build_kb_service()
    journal = await build_journal_service()
    assistant = Assistant(
        bus=app.state.event_bus,
        llm=build_agent_llm(model=settings.assistant_model),
        tools=build_tool_registry(kb, journal=journal),
        trigger=TriggerGate(cooldown_sec=settings.assistant_cooldown_sec),
        summary_reader=_read_summary,
        enabled=settings.assistant_enabled,
        max_tokens=settings.assistant_max_tokens,
        wm_max_items=settings.assistant_wm_max_items,
    )
```

Update the degraded-None docstring (~line 542) to name the journal:

```python
    VOYAGE_API_KEY -> ``build_kb_service`` and ``build_journal_service`` return None
    and kb_retrieve / search_journal degrade; either way the daemon still boots.
```

- [ ] **Step 3: Run the test + focused lifespan/integration suite to verify green**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_assistant_lifespan.py tests/integration/test_assistant.py -v`
Expected: PASS (existing lifespan/degraded tests + the new regression guard).

- [ ] **Step 4: Commit**

```bash
git add redrobin/api/lifespan_helpers.py tests/integration/test_assistant_lifespan.py
git commit -m "feat(journal): wire build_journal_service into start_assistant (degraded-None)"
```

---

### Task 3.5: Full-suite green + the 5 CI gates + docstring polish

Final consolidation: update the `tools.py` module docstring to reflect the shipped `search_journal`, run all five gates, confirm pristine output.

**Files:**
- Modify: `redrobin/application/agents/assistant/tools.py` (module docstring only)

- [ ] **Step 1: Update the module docstring** (top of `tools.py`)

```python
"""Retrieval tools the assistant hands to the AgentLLM each cycle.

Two PULL tools: ``kb_retrieve`` over ``KBService`` (curated cross-op tradecraft)
and ``search_journal`` over ``JournalService`` (this engagement's verbatim
events.jsonl transcript, kept current by an incremental refresh). ``build_tool_registry``
is the extensibility seam — its returned ``list[AgentTool]`` is what the agent loop
consumes; later tasks append ``get_lessons`` without changing that contract.
``build_kb_service`` / ``build_journal_service`` construct the backends (lazy infra
import, degraded-None without VOYAGE_API_KEY)."""
```

- [ ] **Step 2: Run the 5 CI gates**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q --tb=short
```

Expected: all green; import-linter 4 contracts KEPT; pytest = the prior baseline + the new journal tests, output pristine (no warnings).

- [ ] **Step 3: Commit**

```bash
git add redrobin/application/agents/assistant/tools.py
git commit -m "docs(journal): tools.py docstring reflects the shipped search_journal tool"
```

---

## Acceptance / Definition of Done (Task 3 / #24)

- [ ] **5 CI gates green** (Global Constraints).
- [ ] **TDD red→green** for: the `correlation_id` join incl. the persistent-map pairing (3.1), `JournalService`'s incremental refresh + retrieve over a seeded+appended `events.jsonl` (3.2), the `search_journal` tool + degraded factory (3.3). Task 3.4 is an integration regression guard (see its note).
- [ ] **Never-crash + degraded-None**: no `VOYAGE_API_KEY` / no active engagement / missing / empty / malformed / unreadable `events.jsonl` / retrieval failure → graceful "unavailable"/"no match", daemon boots (tested in 3.2, 3.3, 3.4).
- [ ] **Stays current** (D4): `search_journal` sees commands appended after boot — proven by `test_refresh_is_incremental` + `test_retrieve_self_refreshes...` (3.2).
- [ ] **Observability**: `assistant: journal indexed N entries` + `search_journal` degradation logs (3.3).
- [ ] **Plugs into the seam** (D1): `search_journal` appended; the `list[AgentTool]` output contract unchanged; push/pull unchanged.
- [ ] **No new capture / no trigger changes / no registry re-architecture** (verified by the diff touching only `journal_service.py`, `tools.py`, `lifespan_helpers.py` + tests).
- [ ] **Acceptance form = deterministic, no operator smoke** (D5); the `journal:`-citation eyeball is a recommended non-gating confirmation on the next reactive smoke.
- [ ] **Atomic conventional commits, no AI co-author trailer.**
- [ ] **Pillars**: ① indexing existing `events.jsonl` + one appended tool = pure plug-in; ② reuses Voyage/`VectorStore` + the enricher's join key + the KBService shape; ③ the verbatim record lets the assistant ground and cite exact past moments.

## Documented limitations (in-scope, deferred by design)

- **`correlation_id`-only join** (D3): events without a `correlation_id` are not indexed. Real CS captures carry it on both events (`redrobin.cna:284,301`).
- **In-memory, not persisted**: the index is rebuilt from `events.jsonl` each daemon boot (then kept current in-session by the refresh). Persisting the `.npz` across boots is a later refinement; a boot re-index is cheap relative to the op.
- **Boot-warm is synchronous/blocking (gated on `assistant_enabled`)**: when the assistant is ENABLED, `start_assistant` `await`s the full-backlog embed before startup completes — and because `start_adapters` runs after `start_assistant`, a large transcript (or Voyage rate-limiting) delays the C2 beacon boundary at boot. Mitigated: the warm is now skipped entirely when the assistant is disabled (the default), so the default config pays zero boot cost (whole-branch review fix, commit `8f567f0`). **Deferred fast-follow (own TDD task, zero correctness impact — `retrieve()` self-refreshes):** move the enabled-case warm to a *tracked* background task (cancelled/awaited in the lifespan `close` hook) so boot never blocks even when enabled.
- **Engagement switch mid-session**: `build_journal_service` resolves the active engagement at boot. A runtime engagement switch is not re-indexed until the next boot (matches how the KB and summary are resolved today); full per-switch re-index is deferred.
- **Multiple outputs sharing one `correlation_id`** each become their own entry (unique store id via `#seq`); the enricher's one-output-per-correlation design makes this an edge, not the common case.
- **Flat numpy index** — the same single-operator scaling ceiling already documented for the KB (v0.2 pgvector).
- **Deferred §6.B refinement**: the `response_format` concise/detailed enum and `search_journal`+`search_episodes` consolidation are NOT in Task 3 (episodes/L3 isn't built — a later task). Noted so a later task picks them up.

## Self-Review (author checklist — completed, audited 2026-07-20)

1. **Spec coverage:** §6.B Operational Journal (verbatim, engagement axis, index-not-capture, correlation_id join reusing the enricher's key) → 3.1/3.2; `search_journal` PULL tool, push/pull unchanged → D1/3.3; §9 "not new capture" → whole design; §8 acceptance form → D5; §6.E never-crash/degraded/observability → 3.2/3.3/3.4. No gaps.
2. **Placeholder scan:** no TBD/"handle edge cases"/"similar to" — every step has runnable code or an exact command. The two `...  # UNCHANGED` markers in 3.3 point at existing code the implementer must NOT alter (kb_retrieve body + description), not gaps.
3. **Type consistency:** `join_transcript(bodies, commands=None) -> list[JournalEntry]`; `_event_bodies(lines) -> list[dict[str,Any]]`; `JournalService.refresh -> int`, `retrieve -> list[tuple[str,str,float]]`; `build_journal_service() -> JournalService | None` (async); `build_tool_registry(kb, journal=None) -> list[AgentTool]` — matches every call site (existing single-arg calls still valid).
4. **Audit fixes applied:** E402 (imports hoisted in every test file); the single `["kb_retrieve"]` assertion (not "two"); hermetic degraded-factory tests (`REDROBIN_ROOT=tmp` + forced engagement); 3.4 reframed as a regression guard (no false red); D4 upgraded to incremental refresh (batched embed, unique ids, async lock).
