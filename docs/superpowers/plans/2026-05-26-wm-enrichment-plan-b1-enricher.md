# WM AI Enrichment — Plan B1: the LLM Enricher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the async `WorkingMemoryEnricher` that turns beacon command output into accurate Working Memory facts via an LLM, **alongside** the existing regex (which Plan B2 retires once this is proven on a live beacon).

**Architecture:** A new `redrobin/application/enrichment/` package: a pure `should_enrich` trigger (enrich-by-default + denylist), a pure map-reduce `chunk_output`, a `parse_deltas` (LLM JSON string → the existing `WorkingMemoryDeltas`), an `EnricherLLM` protocol + Haiku-class `AnthropicEnricherClient` (caches the static extraction prompt), and the `WorkingMemoryEnricher` async bus consumer. The enricher subscribes to `beacon.command_output`, extracts (chunked, in parallel), and **publishes a `working_memory.deltas` event** — which Plan A's reducer already consumes + merges single-writer. The enricher NEVER writes the WM. Best-effort: any failure logs + emits `enrichment.failed` and is dropped; it never crashes the daemon or blocks the gate.

**Tech Stack:** Python 3.12, asyncio EventBus, pydantic-settings, Anthropic SDK (`cache_control`), pytest (`@pytest.mark.asyncio` + a local stub LLM — no real API in CI).

**Spec:** `docs/superpowers/specs/2026-05-26-working-memory-ai-enrichment-design.md` (§3 trigger, §7 map-reduce, §8 errors, §9 observability, §10 cost).

**Branch:** `wm/enricher` (off `dev-juan`, already checked out).

**Cross-plan contract (Plan A, already merged — DO NOT change):** the reducer consumes `{"type":"working_memory.deltas","payload":{"source_beacon_id":<bid|null>,"hosts":[...],"credentials":[...],"findings":[...],"tools_used":[...],"defenses_observed":[...]}}` (the 5 arrays = `WorkingMemoryDeltas.to_payload()`). The enricher must publish exactly this.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `redrobin/infrastructure/config.py` | typed Settings | **Modify** — add 5 `enrichment_*` fields |
| `redrobin/application/enrichment/__init__.py` | package marker | **Create** (empty) |
| `redrobin/application/enrichment/trigger.py` | `should_enrich` (pure) | **Create** |
| `redrobin/application/enrichment/chunker.py` | `chunk_output` (pure map-reduce split) | **Create** |
| `redrobin/application/enrichment/parser.py` | `parse_deltas` + `DeltaParseError` | **Create** |
| `redrobin/application/enrichment/llm.py` | `EnricherLLM` Protocol + `build_enricher_llm()` | **Create** |
| `redrobin/infrastructure/llm/anthropic_client.py` | LLM clients | **Modify** — add `AnthropicEnricherClient` |
| `redrobin/application/enrichment/enricher.py` | `WorkingMemoryEnricher` async consumer | **Create** |
| `redrobin/api/lifespan_helpers.py` | lifespan start_* helpers | **Modify** — add `start_enricher` |
| `redrobin/api/app.py` | lifespan orchestrator | **Modify** — call + close `start_enricher` |
| `tests/integration/test_settings.py` | settings tests | **Modify** — enrichment defaults/override |
| `tests/unit/enrichment/test_trigger.py` | trigger unit tests | **Create** |
| `tests/unit/enrichment/test_chunker.py` | chunker unit tests | **Create** |
| `tests/unit/enrichment/test_parser.py` | parser unit tests | **Create** |
| `tests/integration/test_enricher.py` | enricher + lifespan + golden fixtures | **Create** |

**Out of scope (Plan B2):** deleting the regex (`_NTLM_LINE`/`_HOSTNAME_LINE`/`_KERBEROS_TICKET`/`_on_beacon_command_output`) + orphaned `upsert_host`/`upsert_credential`; tier-3 `doctor` enrichment counters. B1 leaves the regex running (transient: `apply_deltas` merges hosts by name; the #145 kerberos-FP lingers until B2). B1's live smoke proves the enricher (#149 visibly fixed); B2's proves the clean state.

---

### Task 1: Enrichment settings

**Files:**
- Modify: `redrobin/infrastructure/config.py` (the `Settings` class, after `l5_interval_sec` at line 58)
- Test: `tests/integration/test_settings.py`

- [ ] **Step 1: Write the failing test** — append to `tests/integration/test_settings.py`:

```python
def test_enrichment_settings_defaults_and_override(monkeypatch):
    from redrobin.infrastructure.config import Settings

    s = Settings()
    assert s.enrichment_enabled is True
    assert s.enrichment_model == "claude-haiku-4-5"
    assert s.enrichment_min_output_chars == 8
    assert s.enrichment_chunk_max_chars == 16000
    assert "ls" in s.enrichment_denylist and "sleep" in s.enrichment_denylist

    monkeypatch.setenv("REDROBIN_ENRICHMENT_ENABLED", "false")
    monkeypatch.setenv("REDROBIN_ENRICHMENT_CHUNK_MAX_CHARS", "32000")
    s2 = Settings()
    assert s2.enrichment_enabled is False
    assert s2.enrichment_chunk_max_chars == 32000
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/integration/test_settings.py::test_enrichment_settings_defaults_and_override -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'enrichment_enabled'`

- [ ] **Step 3: Add the fields** — in `redrobin/infrastructure/config.py`, immediately after the `l5_interval_sec` line (line 58):

```python

    # Working-memory enrichment (Plan B)
    enrichment_enabled: bool = Field(default=True, alias="REDROBIN_ENRICHMENT_ENABLED")
    enrichment_model: str = Field(default="claude-haiku-4-5", alias="REDROBIN_ENRICHMENT_MODEL")
    enrichment_min_output_chars: int = Field(
        default=8, alias="REDROBIN_ENRICHMENT_MIN_OUTPUT_CHARS"
    )
    enrichment_chunk_max_chars: int = Field(
        default=16000, alias="REDROBIN_ENRICHMENT_CHUNK_MAX_CHARS"
    )
    enrichment_denylist: list[str] = Field(
        default_factory=lambda: [
            "ls", "dir", "pwd", "cd", "sleep", "cls", "clear", "help", "jobs", "ps",
        ],
        alias="REDROBIN_ENRICHMENT_DENYLIST",
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/integration/test_settings.py -q`
Expected: PASS (the new test + all pre-existing settings tests).

- [ ] **Step 5: Commit**

```bash
git add redrobin/infrastructure/config.py tests/integration/test_settings.py
git commit -m "feat(config): add enrichment settings (enabled/model/denylist/chunk/min-chars)"
```

---

### Task 2: `should_enrich` trigger

**Files:**
- Create: `redrobin/application/enrichment/__init__.py` (empty)
- Create: `redrobin/application/enrichment/trigger.py`
- Test: `tests/unit/enrichment/__init__.py` (empty) + `tests/unit/enrichment/test_trigger.py`

- [ ] **Step 1: Write the failing test** — create `tests/unit/enrichment/__init__.py` (empty) and `tests/unit/enrichment/test_trigger.py`:

```python
"""Unit tests for the enrichment trigger (enrich-by-default + denylist)."""

from __future__ import annotations

from redrobin.application.enrichment.trigger import should_enrich

DENY = ["ls", "pwd", "sleep", "cd"]


def test_enriches_unknown_command_by_default():
    assert should_enrich("systeminfo", "x" * 50, denylist=DENY, min_output_chars=8)


def test_skips_denylisted_bare_command():
    assert not should_enrich("ls", "a" * 50, denylist=DENY, min_output_chars=8)


def test_skips_denylisted_shell_wrapped_command():
    assert not should_enrich("shell pwd", "a" * 50, denylist=DENY, min_output_chars=8)


def test_enriches_shell_wrapped_unknown_command():
    assert should_enrich("shell systeminfo", "x" * 50, denylist=DENY, min_output_chars=8)


def test_skips_short_or_empty_output():
    assert not should_enrich("systeminfo", "  ", denylist=DENY, min_output_chars=8)
    assert not should_enrich("systeminfo", "tiny", denylist=DENY, min_output_chars=8)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_trigger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.enrichment'`

- [ ] **Step 3: Implement** — create `redrobin/application/enrichment/trigger.py`:

```python
"""Enrich-by-default trigger: skip a small denylist of trivial commands + short output.

Fails TOWARD enrichment — an unknown/novel command is enriched by default, so a
new tool is never silently missed (the failure mode an allowlist would have).
Handles both bare commands (``ls``) and shell-wrapped ones (``shell ls``).
"""

from __future__ import annotations


def should_enrich(
    command: str, output: str, *, denylist: list[str], min_output_chars: int
) -> bool:
    if len(output.strip()) < min_output_chars:
        return False
    parts = command.strip().split(maxsplit=1)
    verb = parts[0].lower() if parts else ""
    inner = ""
    if verb in ("shell", "run", "powershell", "pwsh", "execute") and len(parts) > 1:
        inner_parts = parts[1].strip().split(maxsplit=1)
        inner = inner_parts[0].lower() if inner_parts else ""
    deny = {d.lower() for d in denylist}
    if verb in deny:
        return False
    if inner and inner in deny:
        return False
    return True
```

(Also create the empty `redrobin/application/enrichment/__init__.py`.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_trigger.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/enrichment/__init__.py redrobin/application/enrichment/trigger.py tests/unit/enrichment/__init__.py tests/unit/enrichment/test_trigger.py
git commit -m "feat(enrichment): should_enrich trigger (enrich-by-default + denylist)"
```

---

### Task 3: `chunk_output` (map-reduce split)

**Files:**
- Create: `redrobin/application/enrichment/chunker.py`
- Test: `tests/unit/enrichment/test_chunker.py`

- [ ] **Step 1: Write the failing test** — create `tests/unit/enrichment/test_chunker.py`:

```python
"""Unit tests for the map-reduce output chunker."""

from __future__ import annotations

from redrobin.application.enrichment.chunker import chunk_output


def test_small_output_is_single_chunk():
    out = "line1\nline2\nline3\n"
    assert chunk_output(out, max_chars=1000) == [out]


def test_large_output_splits_on_line_boundaries_with_overlap():
    lines = [f"line-{i:03d}\n" for i in range(400)]  # ~3200 chars
    out = "".join(lines)
    chunks = chunk_output(out, max_chars=800, overlap=0.15)
    assert len(chunks) > 1
    # every chunk ends on a line boundary (newline) and is within budget-ish
    for c in chunks:
        assert c.endswith("\n")
        assert len(c) <= 800
    # overlap: a line near a boundary appears in two consecutive chunks
    joined_pairs = [chunks[i] + chunks[i + 1] for i in range(len(chunks) - 1)]
    assert any(c.count("line-") for c in chunks)
    # reconstruct coverage: every line index appears in at least one chunk
    body = "".join(chunks)
    for i in range(400):
        assert f"line-{i:03d}" in body


def test_overlap_repeats_boundary_lines():
    lines = [f"L{i}\n" for i in range(100)]
    chunks = chunk_output("".join(lines), max_chars=120, overlap=0.2)
    # consecutive chunks share at least one line (overlap > 0)
    for i in range(len(chunks) - 1):
        a = set(chunks[i].splitlines())
        b = set(chunks[i + 1].splitlines())
        assert a & b, f"chunks {i},{i+1} have no overlap"


def test_single_oversized_line_is_hard_split():
    out = "x" * 5000  # one line, no newline
    chunks = chunk_output(out, max_chars=1000)
    assert len(chunks) >= 5
    assert "".join(chunks) == out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_chunker.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`

- [ ] **Step 3: Implement** — create `redrobin/application/enrichment/chunker.py`:

```python
"""Map-reduce output chunking: split oversized command output on line boundaries
with overlap, so a record straddling a boundary appears intact in the next chunk.

Returns ``[output]`` unchanged when it fits (the 99% case). char-based budget
(~4 chars/token) avoids a tokenizer dependency. The reduce step is the existing
``WorkingMemoryRepo.apply_deltas`` (dedup collapses overlap duplicates).
"""

from __future__ import annotations


def chunk_output(output: str, *, max_chars: int, overlap: float = 0.15) -> list[str]:
    if len(output) <= max_chars:
        return [output]
    lines = output.splitlines(keepends=True)
    if len(lines) <= 1:  # a single line longer than max_chars — hard-split it
        return [output[i : i + max_chars] for i in range(0, len(output), max_chars)]

    chunks: list[str] = []
    start = 0
    while start < len(lines):
        end = start
        size = 0
        while end < len(lines) and size + len(lines[end]) <= max_chars:
            size += len(lines[end])
            end += 1
        if end == start:  # one line over budget — hard-split that line, then continue
            chunks.extend(
                lines[start][i : i + max_chars] for i in range(0, len(lines[start]), max_chars)
            )
            start += 1
            continue
        chunks.append("".join(lines[start:end]))
        if end >= len(lines):
            break
        step = max(1, (end - start) - int((end - start) * overlap))
        start += step
    return chunks
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_chunker.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/enrichment/chunker.py tests/unit/enrichment/test_chunker.py
git commit -m "feat(enrichment): chunk_output map-reduce split (line-boundary + overlap)"
```

---

### Task 4: `parse_deltas` (LLM JSON → WorkingMemoryDeltas)

**Files:**
- Create: `redrobin/application/enrichment/parser.py`
- Test: `tests/unit/enrichment/test_parser.py`

- [ ] **Step 1: Write the failing test** — create `tests/unit/enrichment/test_parser.py`:

```python
"""Unit tests for the LLM-output delta parser."""

from __future__ import annotations

import pytest

from redrobin.application.enrichment.parser import DeltaParseError, parse_deltas


def test_parses_plain_json_and_attaches_provenance():
    raw = '{"hosts": [{"name": "WS01", "domain": "ACME"}], "credentials": [], "findings": []}'
    deltas = parse_deltas(raw, source_beacon_id="B-1")
    assert deltas.hosts[0].name == "WS01"
    assert deltas.hosts[0].domain == "ACME"


def test_strips_json_code_fences():
    raw = '```json\n{"credentials": [{"domain": "A", "username": "u", "secret_type": "plaintext", "secret": "pw"}]}\n```'
    deltas = parse_deltas(raw, source_beacon_id="B-2")
    assert deltas.credentials[0].username == "u"
    assert deltas.credentials[0].source_beacon_id == "B-2"  # provenance system-supplied


def test_malformed_json_raises_delta_parse_error():
    with pytest.raises(DeltaParseError):
        parse_deltas("not json at all {", source_beacon_id=None)


def test_non_object_json_raises_delta_parse_error():
    with pytest.raises(DeltaParseError):
        parse_deltas("[1, 2, 3]", source_beacon_id=None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_parser.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement** — create `redrobin/application/enrichment/parser.py`:

```python
"""Parse an LLM extraction response (a JSON string) into WorkingMemoryDeltas.

Tolerates ```json code fences (models often wrap JSON). Raises DeltaParseError on
unparseable / non-object input; the enricher does ONE retry, then logs + drops.
Whitelisting + provenance live in WorkingMemoryDeltas.from_payload (domain).
"""

from __future__ import annotations

import json
import re

from redrobin.domain.memory import WorkingMemoryDeltas

_FENCE_OPEN = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```\s*$")


class DeltaParseError(Exception):
    """Raised when an LLM response is not valid JSON object output."""


def parse_deltas(raw: str, *, source_beacon_id: str | None) -> WorkingMemoryDeltas:
    text = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", raw.strip()))
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DeltaParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DeltaParseError(f"expected a JSON object, got {type(data).__name__}")
    return WorkingMemoryDeltas.from_payload(data, source_beacon_id=source_beacon_id)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_parser.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/enrichment/parser.py tests/unit/enrichment/test_parser.py
git commit -m "feat(enrichment): parse_deltas (fence-tolerant LLM JSON -> WorkingMemoryDeltas)"
```

---

### Task 5: `EnricherLLM` protocol + `AnthropicEnricherClient` + `build_enricher_llm`

**Files:**
- Create: `redrobin/application/enrichment/llm.py` (protocol + factory)
- Modify: `redrobin/infrastructure/llm/anthropic_client.py` (add `AnthropicEnricherClient`)
- Test: `tests/unit/enrichment/test_llm.py`

- [ ] **Step 1: Write the failing test** — create `tests/unit/enrichment/test_llm.py`:

```python
"""The EnricherLLM protocol is structural; a stub conforms, and build_enricher_llm
returns None when the API key is absent (degraded mode)."""

from __future__ import annotations

import pytest

from redrobin.application.enrichment.llm import EnricherLLM, build_enricher_llm


class _StubLLM:
    async def extract(self, *, command: str, beacon_id: str | None, output: str) -> str:
        return "{}"


def test_stub_conforms_to_protocol():
    llm: EnricherLLM = _StubLLM()
    assert hasattr(llm, "extract")


@pytest.mark.asyncio
async def test_stub_extract_returns_str():
    out = await _StubLLM().extract(command="systeminfo", beacon_id="B-1", output="x")
    assert isinstance(out, str)


def test_build_enricher_llm_degraded_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert build_enricher_llm(model="claude-haiku-4-5") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_llm.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3a: Implement the protocol + factory** — create `redrobin/application/enrichment/llm.py`:

```python
"""Narrow LLM contract for the enricher + a degraded-aware factory.

Mirrors the L5 summarizer's L5LLM/build_summarizer_llm pattern: a structural
Protocol the enricher depends on, and a factory that returns the Anthropic-backed
client when ANTHROPIC_API_KEY is present, else None (enrichment runs degraded —
the enricher short-circuits when llm is None, daemon still boots).
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class EnricherLLM(Protocol):
    """Single-call extraction: command output -> JSON deltas (as a string)."""

    async def extract(self, *, command: str, beacon_id: str | None, output: str) -> str: ...


def build_enricher_llm(*, model: str = "claude-haiku-4-5") -> EnricherLLM | None:
    try:
        from redrobin.infrastructure.llm.anthropic_client import AnthropicEnricherClient

        return AnthropicEnricherClient(model=model)
    except RuntimeError:
        logger.info(
            "WM enricher: ANTHROPIC_API_KEY missing — running degraded (no enrichment)"
        )
        return None
```

- [ ] **Step 3b: Implement the client** — append to `redrobin/infrastructure/llm/anthropic_client.py` (after `AnthropicSummarizerClient`):

```python


class AnthropicEnricherClient:
    """Working-memory enricher LLM — one cached-prompt extraction call per chunk.

    The large STATIC system prompt (extraction instructions + the JSON delta
    schema + grounding rules + a few-shot) is sent as an ephemeral 1h cached
    block, so per-call cost is ~the dynamic command-output tokens. Implements the
    ``EnricherLLM`` protocol; Haiku-class model (extraction is not reasoning-heavy).
    """

    _SYSTEM_PROMPT = (
        "You are the Red Robin working-memory extraction engine. Read one red-team "
        "beacon command and its raw output, and return ONLY a JSON object with these "
        "keys (any may be empty):\n"
        '{"hosts":[{"name","os","os_version","domain","ip_addresses":[],'
        '"integrity_level","edr_detected","access_level"}],'
        '"credentials":[{"domain","username","secret_type","secret","ntlm_hash"}],'
        '"findings":[{"kind","summary"}],'
        '"tools_used":[{"name","used_on","detection_observed"}],'
        '"defenses_observed":[]}\n'
        "Rules: Extract ONLY facts literally present in the output. Do NOT infer, "
        "guess, or invent. If a field is absent, omit it. Understand semantics: "
        "'Cached Tickets: (0)' means ZERO tickets — do NOT emit a finding for it. "
        "secret_type is an open vocabulary (ntlm|plaintext|aes256|kerberos_ticket|"
        "aws_key|...). Return ONLY the JSON object, no prose, no markdown fences."
    )

    def __init__(
        self, *, model: str = "claude-haiku-4-5", max_retries: int = 5, max_tokens: int = 2048
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(
            api_key=get_anthropic_key(),
            max_retries=max_retries,
        )

    async def extract(self, *, command: str, beacon_id: str | None, output: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": self._SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"Command: {command}\nBeacon: {beacon_id}\nOutput:\n{output}",
                }
            ],
        )
        return response.content[0].text
```

(`anthropic` and `get_anthropic_key` are already imported at the top of this file. `get_anthropic_key()` raises `RuntimeError` when the key is missing — that's what `build_enricher_llm` catches.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_llm.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/enrichment/llm.py redrobin/infrastructure/llm/anthropic_client.py tests/unit/enrichment/test_llm.py
git commit -m "feat(enrichment): EnricherLLM protocol + AnthropicEnricherClient (cached Haiku) + factory"
```

---

### Task 6: `WorkingMemoryEnricher` (async bus consumer)

**Files:**
- Create: `redrobin/application/enrichment/enricher.py`
- Test: `tests/integration/test_enricher.py`

- [ ] **Step 1: Write the failing test** — create `tests/integration/test_enricher.py`:

```python
"""Integration: the enricher consumes beacon.command_output and publishes
working_memory.deltas + enrichment.completed (stub LLM — no real API)."""

from __future__ import annotations

import asyncio

import pytest

from redrobin.application.enrichment.enricher import WorkingMemoryEnricher
from redrobin.application.event_bus import EventBus


class _StubLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def extract(self, *, command, beacon_id, output) -> str:
        self.calls += 1
        return self._response


async def _start(bus, llm, **kw):
    enricher = WorkingMemoryEnricher(
        bus=bus,
        llm=llm,
        denylist=["ls", "pwd", "sleep"],
        min_output_chars=8,
        chunk_max_chars=16000,
        enabled=True,
        **kw,
    )
    started = asyncio.Event()
    enricher.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(enricher.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    return enricher, task


async def _drain(bus, task):
    await bus.close()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_enricher_publishes_deltas_and_completed(tmp_path):
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM(
        '{"hosts":[{"name":"ws01","os":"Windows 10","domain":"ACME",'
        '"ip_addresses":["10.0.0.5"],"os_version":"10.0.19045"}],'
        '"credentials":[{"domain":"ACME","username":"jdoe","secret_type":"plaintext","secret":"pw"}],'
        '"findings":[{"kind":"gpp","summary":"x"}]}'
    )
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {"bid": "B-7", "command": "systeminfo", "output": "Host Name: WS01\n" * 5},
            }
        )
        deltas_evt = None
        completed_evt = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "working_memory.deltas":
                deltas_evt = evt
            elif evt.get("type") == "enrichment.completed":
                completed_evt = evt
            if deltas_evt and completed_evt:
                break
        assert deltas_evt is not None, "enricher did not publish working_memory.deltas"
        p = deltas_evt["payload"]
        assert p["source_beacon_id"] == "B-7"
        assert p["hosts"][0]["domain"] == "ACME"
        assert p["hosts"][0]["ip_addresses"] == ["10.0.0.5"]
        assert any(c["secret_type"] == "plaintext" for c in p["credentials"])
        assert completed_evt is not None
        assert completed_evt["payload"]["command"] == "systeminfo"
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_enricher_skips_denylisted_command(tmp_path):
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM("{}")
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {"type": "beacon.command_output", "payload": {"bid": "B-1", "command": "ls", "output": "a" * 50}}
        )
        await asyncio.sleep(0.2)
        assert llm.calls == 0  # denylisted -> no LLM call
        # no working_memory.deltas emitted
        seen = []
        try:
            while True:
                seen.append(observer.get_nowait().get("type"))
        except asyncio.QueueEmpty:
            pass
        assert "working_memory.deltas" not in seen
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_enricher_malformed_json_emits_failed_not_crash(tmp_path):
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM("this is not json")  # both the call + retry return junk
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {"type": "beacon.command_output", "payload": {"bid": "B-9", "command": "systeminfo", "output": "x" * 50}}
        )
        failed = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "enrichment.failed":
                failed = evt
                break
        assert failed is not None, "expected enrichment.failed on unparseable output"
        assert llm.calls == 2  # one call + one retry
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/integration/test_enricher.py -v`
Expected: FAIL with `ImportError` (`WorkingMemoryEnricher` not defined)

- [ ] **Step 3: Implement** — create `redrobin/application/enrichment/enricher.py`:

```python
"""WorkingMemoryEnricher — async bus consumer that turns beacon command output
into Working Memory facts via an LLM, then PUBLISHES a working_memory.deltas event
(Plan A's reducer is the single writer that merges it). Never writes the WM, never
crashes the daemon, never blocks the gate. One retry on malformed JSON, then drop.
Emits enrichment.completed / enrichment.failed for observability (tui_ws forwards
all bus events to the TUI).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from redrobin.application.enrichment.chunker import chunk_output
from redrobin.application.enrichment.llm import EnricherLLM
from redrobin.application.enrichment.parser import DeltaParseError, parse_deltas
from redrobin.application.enrichment.trigger import should_enrich
from redrobin.application.event_bus import EventBus
from redrobin.domain.memory import WorkingMemoryDeltas

logger = logging.getLogger(__name__)


class WorkingMemoryEnricher:
    def __init__(
        self,
        *,
        bus: EventBus,
        llm: EnricherLLM | None,
        denylist: list[str],
        min_output_chars: int,
        chunk_max_chars: int,
        enabled: bool = True,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._denylist = denylist
        self._min_output_chars = min_output_chars
        self._chunk_max_chars = chunk_max_chars
        self._enabled = enabled
        self.on_started: Callable[[], None] | None = None

    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("enricher on_started callback raised")
        try:
            while True:
                evt = await sub.get()
                if evt.get("_type") == "_close":
                    return
                if evt.get("type") != "beacon.command_output":
                    continue
                try:
                    await self._enrich(evt.get("payload") or {})
                except Exception:
                    logger.exception("enricher failed to process beacon.command_output")
        finally:
            await self._bus.unsubscribe(sub)

    async def _enrich(self, payload: dict) -> None:
        if self._llm is None or not self._enabled:
            return
        command = str(payload.get("command") or payload.get("cmd") or "")
        output = str(payload.get("output", ""))
        bid = payload.get("bid")
        src = str(bid) if bid is not None else None
        if not should_enrich(
            command, output, denylist=self._denylist, min_output_chars=self._min_output_chars
        ):
            return

        t0 = time.monotonic()
        chunks = chunk_output(output, max_chars=self._chunk_max_chars)
        try:
            per_chunk = await asyncio.gather(
                *(self._extract_chunk(command, src, c) for c in chunks)
            )
        except Exception as exc:  # network/timeout/api — best-effort
            await self._emit_failed(command, src, f"llm_error: {exc}")
            return

        merged = WorkingMemoryDeltas()
        for deltas in per_chunk:
            if deltas is None:
                continue
            merged.hosts.extend(deltas.hosts)
            merged.credentials.extend(deltas.credentials)
            merged.findings.extend(deltas.findings)
            merged.tools_used.extend(deltas.tools_used)
            merged.defenses_observed.extend(deltas.defenses_observed)

        n = (
            len(merged.hosts)
            + len(merged.credentials)
            + len(merged.findings)
            + len(merged.tools_used)
            + len(merged.defenses_observed)
        )
        out_payload = merged.to_payload()
        out_payload["source_beacon_id"] = src
        await self._bus.publish({"type": "working_memory.deltas", "payload": out_payload})
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "enrichment ok: command=%s beacon=%s chunks=%d deltas=%d latency_ms=%d",
            command, src, len(chunks), n, latency_ms,
        )
        await self._bus.publish(
            {
                "type": "enrichment.completed",
                "payload": {
                    "command": command,
                    "beacon_id": src,
                    "chunks": len(chunks),
                    "n_deltas": n,
                    "latency_ms": latency_ms,
                },
            }
        )

    async def _extract_chunk(
        self, command: str, src: str | None, chunk: str
    ) -> WorkingMemoryDeltas | None:
        """Extract one chunk; ONE retry on malformed JSON, then log + drop (None)."""
        assert self._llm is not None
        for attempt in (1, 2):
            raw = await self._llm.extract(command=command, beacon_id=src, output=chunk)
            try:
                return parse_deltas(raw, source_beacon_id=src)
            except DeltaParseError as exc:
                if attempt == 2:
                    logger.warning("enrichment: unparseable LLM output, dropping chunk: %s", exc)
                    return None
        return None

    async def _emit_failed(self, command: str, src: str | None, reason: str) -> None:
        logger.error("enrichment failed: command=%s beacon=%s reason=%s", command, src, reason)
        await self._bus.publish(
            {
                "type": "enrichment.failed",
                "payload": {"command": command, "beacon_id": src, "reason": reason},
            }
        )


__all__ = ["WorkingMemoryEnricher"]
```

Note on the malformed-JSON test: when EVERY chunk fails to parse (all `None`) the enricher still publishes an empty `working_memory.deltas` AND `enrichment.completed`. To make the failed-path test deterministic, the implementer must emit `enrichment.failed` when **all** chunks dropped. Adjust `_enrich`: after the merge loop, if `all(d is None for d in per_chunk)` and `per_chunk`, call `await self._emit_failed(command, src, "all_chunks_unparseable")` and `return` BEFORE publishing the (empty) deltas. Add that guard:

```python
        if per_chunk and all(d is None for d in per_chunk):
            await self._emit_failed(command, src, "all_chunks_unparseable")
            return
```
(place it immediately after the `per_chunk = await asyncio.gather(...)` try/except block, before building `merged`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/integration/test_enricher.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/enrichment/enricher.py tests/integration/test_enricher.py
git commit -m "feat(enrichment): WorkingMemoryEnricher async consumer (chunk->extract->deltas event)"
```

---

### Task 7: Lifespan wiring (`start_enricher`)

**Files:**
- Modify: `redrobin/api/lifespan_helpers.py` (add `start_enricher` + export)
- Modify: `redrobin/api/app.py` (call it after `start_working_memory`; close it)
- Test: append to `tests/integration/test_enricher.py`

- [ ] **Step 1: Write the failing test** — append to `tests/integration/test_enricher.py`:

```python
@pytest.mark.asyncio
async def test_lifespan_starts_enricher_task(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-enr")
    # No ANTHROPIC_API_KEY -> enricher boots in degraded mode (llm None), task still runs.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.enricher_task is not None
        assert not app.state.enricher_task.done()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/integration/test_enricher.py::test_lifespan_starts_enricher_task -v`
Expected: FAIL with `AttributeError: ... has no attribute 'enricher_task'`

- [ ] **Step 3a: Add `start_enricher`** to `redrobin/api/lifespan_helpers.py` — add the import at the top (with the other application imports):

```python
from redrobin.application.enrichment.enricher import WorkingMemoryEnricher
from redrobin.application.enrichment.llm import build_enricher_llm
```

Then add the helper (after `start_l5_summarizer`, before `__all__`):

```python
async def start_enricher(app: FastAPI, settings: Settings) -> tuple[WorkingMemoryEnricher, CloseHook]:
    """Start the working-memory LLM enricher (Plan B).

    Subscribes to ``beacon.command_output`` and publishes ``working_memory.deltas``
    events the reducer (started earlier) merges. When ANTHROPIC_API_KEY is missing,
    ``build_enricher_llm`` returns None and the enricher short-circuits — daemon
    still boots (degraded), the regex path still populates working memory.
    """
    enricher = WorkingMemoryEnricher(
        bus=app.state.event_bus,
        llm=build_enricher_llm(model=settings.enrichment_model),
        denylist=settings.enrichment_denylist,
        min_output_chars=settings.enrichment_min_output_chars,
        chunk_max_chars=settings.enrichment_chunk_max_chars,
        enabled=settings.enrichment_enabled,
    )
    started = asyncio.Event()
    enricher.on_started = started.set  # type: ignore[assignment]
    enricher_task = asyncio.create_task(enricher.run(), name="wm_enricher")
    await started.wait()
    app.state.enricher = enricher
    app.state.enricher_task = enricher_task

    async def close() -> None:
        try:
            await asyncio.wait_for(enricher_task, timeout=2.0)
        except asyncio.TimeoutError:
            enricher_task.cancel()

    return enricher, close
```

Add `"start_enricher"` to the `__all__` list.

- [ ] **Step 3b: Wire it into the lifespan** — in `redrobin/api/app.py`:

Add `start_enricher` to the import block (line ~32-40, the `from redrobin.api.lifespan_helpers import (...)`).

In the `lifespan` function, after the `start_working_memory` call (line ~90, `_, close_wm = await start_working_memory(app, settings)`), add:

```python
    _, close_enricher = await start_enricher(app, settings)
```

And register its close hook so it's torn down in reverse with the others (follow the existing close-hook collection pattern — add `close_enricher` to whatever list/sequence the lifespan awaits in reverse during shutdown; if the lifespan closes hooks inline, add `await close_enricher()` symmetrically with `close_wm`/`close_promoter`/`close_summarizer`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/integration/test_enricher.py -v`
Expected: PASS (all enricher tests + the new lifespan test). Also run the full daemon-boot test to confirm no lifespan regression: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_lifespan_helpers.py tests/e2e/test_init_to_running_daemon.py -q`

- [ ] **Step 5: Commit**

```bash
git add redrobin/api/lifespan_helpers.py redrobin/api/app.py tests/integration/test_enricher.py
git commit -m "feat(enrichment): wire WorkingMemoryEnricher into the daemon lifespan"
```

---

### Task 8: Golden fixtures (#149/#145 via the enricher) + full-suite gate

**Files:**
- Test: append to `tests/integration/test_enricher.py`

- [ ] **Step 1: Write the failing test** — append to `tests/integration/test_enricher.py`. These prove the END-TO-END pipeline (output → enricher → deltas event) carries the right facts when the LLM returns the expected JSON; the LLM's actual extraction quality is validated in the live smoke, not CI.

```python
@pytest.mark.asyncio
async def test_golden_systeminfo_carries_host_fields_149(tmp_path):
    """#149: a systeminfo extraction yields a host with os/os_version/domain/ips."""
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM(
        '{"hosts":[{"name":"EC2AMAZ-IJVFMFN","os":"Microsoft Windows Server 2019",'
        '"os_version":"10.0.17763 Build 17763","domain":"CORP",'
        '"ip_addresses":["10.100.0.21"]}]}'
    )
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-149",
                    "command": "shell systeminfo",
                    "output": "Host Name: EC2AMAZ-IJVFMFN\nDomain: CORP\n" + ("x\n" * 10),
                },
            }
        )
        host = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "working_memory.deltas" and evt["payload"]["hosts"]:
                host = evt["payload"]["hosts"][0]
                break
        assert host is not None
        assert host["domain"] == "CORP"
        assert host["os_version"] == "10.0.17763 Build 17763"
        assert host["ip_addresses"] == ["10.100.0.21"]
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_golden_klist_zero_tickets_no_finding_145(tmp_path):
    """#145: klist with 'Cached Tickets: (0)' yields NO kerberos finding (LLM returns [])."""
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM('{"findings":[]}')  # the grounded LLM emits no finding for (0)
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-145",
                    "command": "klist",
                    "output": "Current LogonId is 0:0x12345\nCached Tickets: (0)\n",
                },
            }
        )
        deltas = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "working_memory.deltas":
                deltas = evt["payload"]
                break
        assert deltas is not None
        assert deltas["findings"] == []  # no spurious kerberos finding
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Run to verify it fails (then passes)**

Run: `uv run --no-sync pytest tests/integration/test_enricher.py -v`
Expected: these two pass immediately (they exercise already-built code) — they are **regression locks** for #149/#145 at the pipeline level. If they fail, the enricher's deltas-publish path has a bug; fix it.

- [ ] **Step 3: Full-suite gate (the real CI commands)**

Run:
```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: all clean; pytest green (prior count + the new enrichment tests). Notes: the enricher is in `application` → mypy-gated (type it cleanly: `EnricherLLM | None`, the `assert self._llm is not None` in `_extract_chunk` narrows it). `AnthropicEnricherClient` is in `infrastructure` → NOT mypy-gated by CI but keep it typed. `lint-imports`: the enrichment package is in `application` (imports `domain` — fine); `build_enricher_llm` lazily imports infra inside the function (mirrors `build_summarizer_llm`) so no module-level application→infra import. If `ruff format --check` flags anything, run `uv run --no-sync ruff format .` and re-commit.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_enricher.py
git commit -m "test(enrichment): golden #149/#145 pipeline regression locks"
```

---

## Self-Review

**1. Spec coverage (B1 slice):** trigger/denylist §3 → T2 ✓; map-reduce chunking §7 → T3 ✓; DeltaParser + 1-retry §8 → T4 + T6 ✓; Haiku + cached prompt §10 → T5 ✓; the enricher (async, single-writer-via-event, best-effort) → T6 ✓; lifespan wiring → T7 ✓; config → T1 ✓; observability tier-1 (logs) + tier-2 (`enrichment.completed`/`failed` bus events → TUI via forwards-all) → T6 ✓; #149/#145 pipeline locks → T8 ✓. **Deferred to B2 (correct):** regex deletion + orphaned-upsert deletion; tier-3 `doctor` counters; local-model note.

**2. Placeholder scan:** every step has complete code + exact commands. The one prose instruction (T6 Step 3 "add the all-chunks-unparseable guard") includes the exact code to add and where. No TBD/TODO.

**3. Type consistency:** `EnricherLLM.extract(*, command, beacon_id, output) -> str` (T5) is called identically in `_extract_chunk` (T6). `parse_deltas(raw, *, source_beacon_id) -> WorkingMemoryDeltas` (T4) used in T6. `should_enrich(command, output, *, denylist, min_output_chars)` (T2) used in T6 + `start_enricher` passes `settings.enrichment_denylist`/`enrichment_min_output_chars` (T1/T7). `chunk_output(output, *, max_chars, overlap)` (T3) called with `max_chars=self._chunk_max_chars` (T6). The published event matches Plan A's reducer contract: `{"type":"working_memory.deltas","payload":{...to_payload(), "source_beacon_id":src}}`. Consistent.

**4. Contract check:** the enricher publishes `working_memory.deltas` with top-level `source_beacon_id` + the 5 `to_payload()` arrays — exactly what Plan A's `_on_enrichment_deltas` reads (`WorkingMemoryDeltas.from_payload(payload, source_beacon_id=payload["source_beacon_id"])`). ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-26-wm-enrichment-plan-b1-enricher.md`. Per the operator's standing workflow: **`/auditing-plans`** on this plan (verify against the real codebase — esp. the `app.py` lifespan close-hook wiring, the `anthropic_client.py` import surface, and the `Settings` list-from-env parsing), then **subagent-driven-development** (Opus 4.7, per-task implementer→spec→quality + full-suite gate + final whole-branch review), then **operator live-beacon smoke** (the real Plan-B acceptance: `systeminfo` → host fully populated #149; `klist` (0) → no FP; `mimikatz` → plaintext cred; big `dir /s` → chunked; induced LLM failure → red `enrichment.failed` in the TUI Events panel). Then finishing-a-development-branch → PR into `dev-juan`. **B2** (retire the regex + orphaned upserts + `doctor` counters) follows after B1 merges + smokes.
