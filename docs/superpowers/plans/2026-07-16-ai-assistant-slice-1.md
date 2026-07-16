# AI Assistant — Slice 1: Reactive Grounded Recommendation (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first executable unit of the Red Robin AI assistant — a production-quality "reactive grounded recommendation" capability that reacts to `enrichment.completed`, reasons over the pushed working-set with a native Tool Runner agent loop calling `kb_retrieve`, and publishes a 5-part `advisor.recommendation` the TUI renders — laying the FINAL extensible seams (`AgentLLM` Protocol, tool registry, push/pull memory contract, typed-event output, degraded lifecycle) proven end-to-end by that one capability.

**Architecture:** Follows the enricher exactly — a bus-subscriber consumer that PUBLISHES (never writes memory), runs degraded-None without an API key, and never crashes the daemon. Pure types live in `domain/assistant.py`; the agentic tool-loop is a native Anthropic **Tool Runner** (`client.beta.messages.tool_runner`) insulated behind a narrow `AgentLLM` **Protocol** in `infrastructure/llm/`; the assistant, its trigger gate, its tools, and its parser live in `application/agents/assistant/`. The `advisor.recommendation` event is an **internal bus event** (assistant → TUI), like `working_memory_snapshot` — no `/v1/events` wire schema.

**Tech Stack:** Python 3.11, `uv`, `anthropic` SDK (Tool Runner beta helpers `beta_async_tool` / `tool_runner`), Voyage embeddings + numpy `VectorStore` (existing KB), `asyncio` EventBus, Textual TUI, `pytest` + `pytest-asyncio` + `pytest-textual-snapshot`, `ruff`, `mypy --strict` (domain + application), `import-linter`.

## Global Constraints

_Every task's requirements implicitly include this section._

- **The 5 CI gates (all must pass — verbatim from `CLAUDE.md`):**
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy redrobin/domain redrobin/application`
  - `uv run lint-imports --config importlinter.cfg`
  - `uv run pytest tests/ -v --tb=short`
- **Native Tool Runner, NOT the Agent SDK.** The agent loop is Anthropic's `client.beta.messages.tool_runner` on the SAME `anthropic.AsyncAnthropic` client, wrapped behind an `AgentLLM` **Protocol** so the beta API is insulated. Zero Claude Agent SDK, zero `~/.claude`/MCP harness.
- **One ABC only = `LLMProvider`** (`redrobin/domain/llm.py`). `AgentLLM` is a structural **Protocol**, NOT a second ABC (mirrors `EnricherLLM` / `L5LLM`). Everything else concrete + duck typing.
- **4 import-linter contracts (do not violate — `importlinter.cfg`):** (1) layered `api|tui → application → domain`; (2) domain never imports infrastructure/application/api/tui; (3) application never imports api/tui; (4) presentation (api/tui) never imports infrastructure directly (application facade only). The assistant stays `redrobin.application.*`, so all four hold unchanged. Application MAY import infrastructure (lazily, as `build_enricher_llm` does); infrastructure MAY import domain.
- **Models (from spec §6.A):** `claude-haiku-4-5` = the fast reactive path (this slice), `claude-sonnet-4-6` (or the then-current Sonnet) = deep sub-agent (later slice). **Slice 1 uses `claude-haiku-4-5`.**
- **Never-crash + degraded-None:** no `ANTHROPIC_API_KEY` → `build_agent_llm` returns `None` → the assistant is silent, the daemon still boots. An exception inside a cycle is logged and the consumer keeps running (mirror `enricher.py:87-88`, `llm.py:36-38`).
- **TDD:** failing test first → verify red → minimal implementation → verify green → commit. All new code requires tests.
- **Atomic conventional commits, NO AI co-author trailer** (`feat(scope): …`, `refactor(scope): …`, `build(deps): …`, `test(scope): …`). One logical change per commit; no `git add -A`.
- **D1 — raise the `anthropic` floor.** `pyproject.toml` currently pins `anthropic>=0.60.0` (too weak — a fresh resolve could land on a 0.6x lacking `beta.messages.tool_runner`). Raise it to **`anthropic>=0.100.0`** — the exact version locked in `uv.lock` (verified here to ship `anthropic.lib.tools.beta_async_tool` + `beta.messages.tool_runner`). This is a conservative, provably-correct floor (the known-good locked version) that cannot regress. Done as its own atomic commit in Task 0.
- **Adapter tests are N/A here.** The assistant is core, not an adapter — no `adapters/<name>/tests/` involvement.
- **Local gotchas:** `uv run --no-sync …` when the daemon is running (it locks `redrobin.exe`); `REDROBIN_ALLOW_DEGRADED=1` for tests that hit the boot-gate without API keys.

---

## File Structure

Files created / modified for THIS unit (target layout — new + moved + touched).

**Task 0 — refactor + D1 (its own atomic change, precedes the feature):**
- Move `redrobin/application/enrichment/` → `redrobin/application/agents/enricher/` (package: `__init__.py`, `enricher.py`, `llm.py`, `trigger.py`, `chunker.py`, `parser.py`, `sanitize.py`).
- Move `redrobin/application/summarizer.py` → `redrobin/application/agents/summarizer.py`.
- Create `redrobin/application/agents/__init__.py` (empty package marker).
- Modify import sites: `redrobin/api/lifespan_helpers.py` (3 imports), `redrobin/application/longterm_lifecycle.py:19`, and the enricher self-imports in `redrobin/application/agents/enricher/enricher.py` (5).
- Move tests `tests/unit/enrichment/` → `tests/unit/agents/enricher/` (6 files) + fix imports; fix imports in `tests/integration/test_enricher.py`, `tests/integration/test_l5_summary.py`.
- Modify `pyproject.toml` (`anthropic>=0.60.0` → `>=0.100.0`).

**Slice 1 — the feature (each file has ONE responsibility):**
- Create `redrobin/domain/assistant.py` — pure types: `Recommendation` (5 mandatory parts + `to_payload`), `AgentTool`.
- Create `redrobin/infrastructure/llm/anthropic_client.py` **additions** — `AnthropicAgentClient` (Tool Runner impl of `AgentLLM`) + `_to_runner_tool` + `_first_text`.
- Create `redrobin/application/agents/assistant/__init__.py`.
- Create `redrobin/application/agents/assistant/llm.py` — `AgentLLM` Protocol + `build_agent_llm` degraded factory.
- Create `redrobin/application/agents/assistant/tools.py` — `build_kb_service` + `build_tool_registry` (the `kb_retrieve` tool).
- Create `redrobin/application/agents/assistant/triggers.py` — `TriggerGate` (dedup / cooldown / novelty).
- Create `redrobin/application/agents/assistant/parser.py` — `parse_recommendation` (JSON → `Recommendation | None`).
- Create `redrobin/application/agents/assistant/agent.py` — `build_system_prompt`, `build_push_context`, `state_signature`, `run_cycle` (assemble → Tool Runner → parse).
- Create `redrobin/application/agents/assistant/assistant.py` — `Assistant` bus consumer (subscribe → cache L2 snapshot → trigger gate → cycle → publish `advisor.recommendation`).
- Create `redrobin/tui/textual_ui/advisor.py` — `format_recommendation` (pure) + `_on_recommendation` handler + `register_advisor`.
- Modify `redrobin/tui/textual_ui/app.py` — register the advisor handler on `REGISTRY`.
- Modify `redrobin/tui/textual_ui/theme.tcss` — advisor-dock child styling (dock stays `display:none`; the handler reveals it — the `#resize-hint` toggle pattern).
- Modify `redrobin/infrastructure/config.py` — assistant `Settings` (enable / model / cooldown / token budget).
- Modify `redrobin/api/lifespan_helpers.py` — `start_assistant(app, settings)`.
- Modify `redrobin/api/app.py` — wire `start_assistant` into the lifespan (startup + reverse shutdown).
- Rename + expand `scripts/smoke/rr-slice1-fixture.ps1` → `scripts/smoke/rr-assistant-smoke-fixture.ps1`; create `scripts/smoke/rr-assistant-smoke-runbook.md`.
- New tests under `tests/unit/agents/assistant/`, `tests/unit/tui_textual/`, `tests/unit/test_domain_assistant.py`, `tests/integration/test_assistant.py`.

**Decision confirmed against code (`advisor.recommendation` event, spec §4 "confirm in plan"):** it is an **internal bus event** produced by the assistant and consumed only by the TUI — exactly like `working_memory_snapshot`, `enrichment.completed`, and `working_memory.deltas`, none of which are dataclasses in `domain/events.py` or have a `protocol/schemas/` entry. It is never ingested via `POST /v1/events`. Therefore **`domain/events.py` and `protocol/schemas/` are intentionally NOT touched** — its typed contract is `Recommendation.to_payload()` (producer) + the TUI handler (consumer). Adding it to `events.py` would force a spurious `additionalProperties:false` schema + a `test_domain_events.py` round-trip, inconsistent with every other internal bus event. `Severity` (the `opsec_risk` type) is reused from `domain/events.py`.

---

## Task 0: Refactor to `application/agents/` + raise the `anthropic` floor (D1)

A small, independent refactor of working/tested code + the D1 dependency bump. NOT the first step of the feature — it lands first, as its own reviewable change, so the assistant arrives in its ready home. Acceptance = the 5 CI gates (deterministic logic → no beacon smoke). This is a **refactor, not TDD**: the existing suite is the safety net — it must stay green with zero behavior change.

**Files:**
- Move: `redrobin/application/enrichment/` → `redrobin/application/agents/enricher/`
- Move: `redrobin/application/summarizer.py` → `redrobin/application/agents/summarizer.py`
- Create: `redrobin/application/agents/__init__.py`
- Modify: `redrobin/application/agents/enricher/enricher.py:18-22` (self-imports)
- Modify: `redrobin/api/lifespan_helpers.py:31-32,45`
- Modify: `redrobin/application/longterm_lifecycle.py:19`
- Move: `tests/unit/enrichment/` → `tests/unit/agents/enricher/` (+ fix imports in all 6 files)
- Modify: `tests/integration/test_enricher.py:10`, `tests/integration/test_l5_summary.py:36`
- Modify: `pyproject.toml:10`

**Interfaces:**
- Consumes: nothing new.
- Produces: the public names are UNCHANGED, only their import paths move — `WorkingMemoryEnricher`, `build_enricher_llm`, `EnricherLLM`, `should_enrich`, `chunk_output`, `parse_deltas`, `DeltaParseError`, `strip_self_annotations` now under `redrobin.application.agents.enricher.*`; `L5Summarizer`, `L5LLM` now under `redrobin.application.agents.summarizer`.

- [ ] **Step 1: Create the `agents` package and move the two units (preserving git history)**

```bash
# create the new parent package, then move the enrichment package + summarizer into it
printf '' > redrobin/application/agents/__init__.py
git add redrobin/application/agents/__init__.py
git mv redrobin/application/enrichment redrobin/application/agents/enricher
git mv redrobin/application/summarizer.py redrobin/application/agents/summarizer.py
```

- [ ] **Step 2: Fix the enricher's 5 self-imports**

In `redrobin/application/agents/enricher/enricher.py` replace the import block (currently lines 18-22):

```python
from redrobin.application.agents.enricher.chunker import chunk_output
from redrobin.application.agents.enricher.llm import EnricherLLM
from redrobin.application.agents.enricher.parser import DeltaParseError, parse_deltas
from redrobin.application.agents.enricher.sanitize import strip_self_annotations
from redrobin.application.agents.enricher.trigger import should_enrich
```

- [ ] **Step 3: Fix the two production import sites**

In `redrobin/api/lifespan_helpers.py` change lines 31-32 and 45:

```python
from redrobin.application.agents.enricher.enricher import WorkingMemoryEnricher
from redrobin.application.agents.enricher.llm import build_enricher_llm
```

```python
from redrobin.application.agents.summarizer import L5Summarizer
```

In `redrobin/application/longterm_lifecycle.py` change line 19:

```python
from redrobin.application.agents.summarizer import L5LLM
```

- [ ] **Step 4: Move the enricher tests and fix all test imports**

```bash
mkdir -p tests/unit/agents
git mv tests/unit/enrichment tests/unit/agents/enricher
```

Then update the import in each moved file — replace every `redrobin.application.enrichment.` with `redrobin.application.agents.enricher.`:
- `tests/unit/agents/enricher/test_sanitize.py`, `test_trigger.py`, `test_llm.py`, `test_parser.py`, `test_command_recovery.py`, `test_chunker.py`

And in `tests/integration/test_enricher.py:10`:

```python
from redrobin.application.agents.enricher.enricher import WorkingMemoryEnricher
```

And in `tests/integration/test_l5_summary.py:36`:

```python
from redrobin.application.agents.summarizer import L5Summarizer
```

- [ ] **Step 5: Run the full gate — verify GREEN (no behavior change)**

Run:
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy redrobin/domain redrobin/application
uv run lint-imports --config importlinter.cfg
uv run pytest tests/ -v --tb=short
```
Expected: all PASS. If `mypy`/`import-linter`/`pytest` report an unresolved `redrobin.application.enrichment.*` or `redrobin.application.summarizer`, a self-import or test import was missed — fix it (grep: `uv run python -c "import subprocess,sys; sys.exit(0)"` is not needed; use `git grep -n 'application\.enrichment\|application\.summarizer'` — expect ZERO hits).

- [ ] **Step 6: Commit the refactor**

```bash
git add redrobin/application/agents redrobin/api/lifespan_helpers.py redrobin/application/longterm_lifecycle.py tests/unit/agents tests/integration/test_enricher.py tests/integration/test_l5_summary.py
git commit -m "refactor(agents): move enrichment + summarizer under application/agents/"
```

- [ ] **Step 7: Raise the `anthropic` floor (D1)**

In `pyproject.toml` change the dependency line (currently line 10) from `"anthropic>=0.60.0",` to:

```toml
    "anthropic>=0.100.0",
```

- [ ] **Step 8: Verify the locked version satisfies the floor and ships the Tool Runner helpers**

Run:
```bash
uv run python -c "import importlib.metadata as m; print(m.version('anthropic'))"
uv run python -c "from anthropic.lib.tools import beta_async_tool; from anthropic import AsyncAnthropic; print('tool_runner helpers OK')"
```
Expected: prints `0.100.0` then `tool_runner helpers OK`. (The lock already pins 0.100.0, so raising the floor to 0.100.0 does not re-resolve.)

- [ ] **Step 9: Commit the floor bump**

```bash
git add pyproject.toml
git commit -m "build(deps): raise anthropic floor to >=0.100.0 for tool_runner (D1)"
```

---

## Task 1: `Recommendation` + `AgentTool` domain types

**Files:**
- Create: `redrobin/domain/assistant.py`
- Test: `tests/unit/test_domain_assistant.py`

**Interfaces:**
- Consumes: `Severity` from `redrobin.domain.events`.
- Produces:
  - `Recommendation(suggested_action: str, rationale: str, opsec_risk: Severity, confidence: str, references: list[str] = [], correlation_id: str | None = None)` with `to_payload() -> dict[str, Any]` (serializes `opsec_risk` to its `.value` string).
  - `AgentTool(name: str, description: str, input_schema: dict[str, Any], handler: Callable[[dict[str, Any]], Awaitable[str]])`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_domain_assistant.py`:

```python
from redrobin.domain.assistant import AgentTool, Recommendation
from redrobin.domain.events import Severity


def test_recommendation_to_payload_serializes_severity_and_fields():
    rec = Recommendation(
        suggested_action="Validate svc_reports against SQL01",
        rationale="CrowdStrike observed; a bind auth is quiet vs a loud dump",
        opsec_risk=Severity.WARN,
        confidence="High the cred is valid; unsure the SQL host is in scope",
        references=["vault/Techniques/mssql.md#chunk_0", "type db_backup.config"],
        correlation_id="18ff",
    )
    p = rec.to_payload()
    assert p["opsec_risk"] == "WARN"  # enum -> wire string
    assert p["suggested_action"].startswith("Validate")
    assert p["rationale"].startswith("CrowdStrike")
    assert p["confidence"].startswith("High")
    assert p["references"][0].endswith("#chunk_0")
    assert p["correlation_id"] == "18ff"


def test_recommendation_defaults_are_empty_refs_and_none_corr():
    rec = Recommendation(
        suggested_action="a", rationale="b", opsec_risk=Severity.INFO, confidence="c"
    )
    p = rec.to_payload()
    assert p["references"] == []
    assert p["correlation_id"] is None


def test_agent_tool_is_a_pure_spec():
    async def _handler(args: dict) -> str:
        return "ok"

    tool = AgentTool(
        name="kb_retrieve",
        description="Search tradecraft.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_handler,
    )
    assert tool.name == "kb_retrieve"
    assert tool.input_schema["type"] == "object"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_domain_assistant.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.domain.assistant'`.

- [ ] **Step 3: Write minimal implementation**

Create `redrobin/domain/assistant.py`:

```python
"""redrobin/domain/assistant.py — pure AI-assistant types.

`Recommendation` is the 5-mandatory-part advisory output (spec §6.C): the WHAT,
the tradecraft WHY, the OPSEC detection risk (reusing `Severity`), a calibrated
confidence, and the grounding references. `to_payload` serializes it to a
JSON-native dict for the `advisor.recommendation` bus event (mirrors
`WorkingMemoryDeltas.to_payload` + `Envelope.to_dict`'s Severity handling).

`AgentTool` is the provider-agnostic tool spec the assistant hands to the
`AgentLLM` each cycle; the Anthropic Tool Runner adapts it (see
`infrastructure/llm/anthropic_client.py`). Pure types only — no infra imports.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from redrobin.domain.events import Severity


@dataclass(frozen=True)
class Recommendation:
    """An advisory next-step (proposes, never acts). Every part embodies the
    thesis: advice with the WHY, not a command."""

    suggested_action: str
    rationale: str
    opsec_risk: Severity
    confidence: str
    references: list[str] = field(default_factory=list)
    correlation_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "suggested_action": self.suggested_action,
            "rationale": self.rationale,
            "opsec_risk": self.opsec_risk.value,
            "confidence": self.confidence,
            "references": list(self.references),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class AgentTool:
    """A single pull-tool the agent may call during a cycle. Provider-agnostic:
    ``handler`` takes the parsed tool arguments and returns a text result the
    model reads. The Anthropic client adapts this into a Tool Runner tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]


__all__ = ["Recommendation", "AgentTool"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_domain_assistant.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify mypy strict (domain is in scope)**

Run: `uv run mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add redrobin/domain/assistant.py tests/unit/test_domain_assistant.py
git commit -m "feat(assistant): add Recommendation + AgentTool domain types"
```

---

## Task 2: `AnthropicAgentClient` — native Tool Runner behind `AgentLLM`

The infrastructure adapter. Appends to the existing `anthropic_client.py` (co-located with `AnthropicClient` / `AnthropicEnricherClient`). The network `run()` is not unit-tested (like `AnthropicEnricherClient.extract` isn't) — the deterministic surfaces (`_to_runner_tool`, `_first_text`) are. Infra is gradual-typed (`disallow_untyped_defs = false`), so loose `Any` typing is fine.

**Files:**
- Modify: `redrobin/infrastructure/llm/anthropic_client.py` (append)
- Test: `tests/unit/agents/assistant/test_anthropic_agent_client.py`
- Create: `tests/unit/agents/assistant/__init__.py` is NOT needed (pytest needs no package init); create the directory implicitly via the test file.

**Interfaces:**
- Consumes: `AgentTool` from `redrobin.domain.assistant`; `get_anthropic_key` from `redrobin.infrastructure.llm.api_keys`; `anthropic.lib.tools.beta_async_tool` + `anthropic.AsyncAnthropic`.
- Produces:
  - `AnthropicAgentClient(*, model="claude-haiku-4-5", max_retries=5, max_iterations=8)` with `async run(*, system: str, user_message: str, tools: list[AgentTool], max_tokens=2048) -> str`.
  - `_to_runner_tool(tool: AgentTool) -> BetaAsyncFunctionTool` (module-level, testable).
  - `_first_text(message: Any) -> str` (module-level, testable).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/assistant/test_anthropic_agent_client.py`:

```python
import pytest

from redrobin.domain.assistant import AgentTool
from redrobin.infrastructure.llm.anthropic_client import _first_text, _to_runner_tool

_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def test_to_runner_tool_carries_name_description_schema():
    async def _handler(args: dict) -> str:
        return "ok"

    tool = AgentTool(
        name="kb_retrieve", description="Search tradecraft.", input_schema=_SCHEMA, handler=_handler
    )
    d = _to_runner_tool(tool).to_dict()
    assert d["name"] == "kb_retrieve"
    assert d["description"] == "Search tradecraft."
    assert d["input_schema"] == _SCHEMA


@pytest.mark.asyncio
async def test_to_runner_tool_handler_receives_kwargs_as_dict():
    seen: dict = {}

    async def _handler(args: dict) -> str:
        seen.update(args)
        return "done"

    tool = AgentTool(name="kb_retrieve", description="d", input_schema=_SCHEMA, handler=_handler)
    runner_tool = _to_runner_tool(tool)
    out = await runner_tool.call({"query": "svc_reports"})
    assert out == "done"
    assert seen == {"query": "svc_reports"}


def test_first_text_returns_first_text_block():
    class _Block:
        def __init__(self, type_: str, text: str = ""):
            self.type = type_
            self.text = text

    class _Msg:
        content = [_Block("tool_use"), _Block("text", "hello")]

    assert _first_text(_Msg()) == "hello"


def test_first_text_empty_when_no_text_block():
    class _Block:
        def __init__(self, type_: str):
            self.type = type_

    class _Msg:
        content = [_Block("tool_use")]

    assert _first_text(_Msg()) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agents/assistant/test_anthropic_agent_client.py -v`
Expected: FAIL with `ImportError: cannot import name '_to_runner_tool'`.

- [ ] **Step 3: Write minimal implementation**

Append to `redrobin/infrastructure/llm/anthropic_client.py` (the `import anthropic` and `get_anthropic_key` import are already at the top of the file; add the two new imports and the class):

```python
# --- appended for the AI assistant (slice 1) -------------------------------
from typing import Any

from anthropic.lib.tools import BetaAsyncFunctionTool, beta_async_tool

from redrobin.domain.assistant import AgentTool


def _to_runner_tool(tool: AgentTool) -> BetaAsyncFunctionTool[Any]:
    """Adapt a provider-agnostic ``AgentTool`` into a Tool Runner async tool.

    Mirrors ``anthropic.lib.tools.mcp.async_mcp_tool``: a ``**kwargs`` coroutine
    plus an explicit name/description/input_schema (so we keep OUR schema rather
    than deriving one from the signature). The runner passes tool arguments as
    kwargs; we hand them to the AgentTool's handler as a dict."""

    async def _call(**kwargs: Any) -> str:
        return await tool.handler(dict(kwargs))

    return beta_async_tool(
        _call,
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,  # type: ignore[arg-type]
    )


def _first_text(message: Any) -> str:
    """Return the first ``text`` block of a (Parsed)BetaMessage, or '' if none
    (e.g. the model ended on a tool_use with no trailing text)."""
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return str(block.text)
    return ""


class AnthropicAgentClient:
    """AgentLLM impl — ONE native Tool Runner cycle per call.

    ``client.beta.messages.tool_runner`` drives the request -> tool -> loop cycle
    for us on the shared ``AsyncAnthropic`` client; the beta tool-loop is fully
    insulated here so the assistant (application) depends only on the ``AgentLLM``
    Protocol. Haiku-class model (the fast reactive path, spec §6.A). The system
    prompt is sent as a FROZEN 1h-cached block; the volatile working-set is in the
    user message (caching refinement: never co-locate the mutating snapshot with
    the cached prefix)."""

    def __init__(
        self, *, model: str = "claude-haiku-4-5", max_retries: int = 5, max_iterations: int = 8
    ) -> None:
        self._model = model
        self._max_iterations = max_iterations
        self._client = anthropic.AsyncAnthropic(
            api_key=get_anthropic_key(),
            max_retries=max_retries,
        )

    async def run(
        self,
        *,
        system: str,
        user_message: str,
        tools: list[AgentTool],
        max_tokens: int = 2048,
    ) -> str:
        runner = self._client.beta.messages.tool_runner(
            model=self._model,
            max_tokens=max_tokens,
            max_iterations=self._max_iterations,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            tools=[_to_runner_tool(t) for t in tools],
            messages=[{"role": "user", "content": user_message}],
        )
        message = await runner.until_done()
        return _first_text(message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agents/assistant/test_anthropic_agent_client.py -v`
Expected: PASS (4 tests). (No network — only the pure adapters are exercised.)

- [ ] **Step 5: Commit**

```bash
git add redrobin/infrastructure/llm/anthropic_client.py tests/unit/agents/assistant/test_anthropic_agent_client.py
git commit -m "feat(assistant): AnthropicAgentClient — native Tool Runner behind AgentLLM"
```

---

## Task 3: `AgentLLM` Protocol + degraded `build_agent_llm` factory

Mirrors `enrichment/llm.py` (`EnricherLLM` Protocol + `build_enricher_llm`): a structural Protocol the assistant depends on, and a factory that returns the Anthropic-backed client when `ANTHROPIC_API_KEY` is present, else `None` (degraded — the assistant stays silent, the daemon still boots).

**Files:**
- Create: `redrobin/application/agents/assistant/__init__.py`
- Create: `redrobin/application/agents/assistant/llm.py`
- Test: `tests/unit/agents/assistant/test_llm.py`

**Interfaces:**
- Consumes: `AgentTool` from `redrobin.domain.assistant`; `AnthropicAgentClient` (lazy import from Task 2).
- Produces:
  - `AgentLLM` Protocol: `async run(*, system: str, user_message: str, tools: list[AgentTool], max_tokens: int = 2048) -> str`.
  - `build_agent_llm(*, model: str = "claude-haiku-4-5") -> AgentLLM | None`.

- [ ] **Step 1: Write the failing test**

Create `redrobin/application/agents/assistant/__init__.py` (empty), then `tests/unit/agents/assistant/test_llm.py`:

```python
import pytest

from redrobin.application.agents.assistant.llm import AgentLLM, build_agent_llm


class _StubAgentLLM:
    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        return "{}"


def test_stub_conforms_to_protocol():
    llm: AgentLLM = _StubAgentLLM()
    assert hasattr(llm, "run")


@pytest.mark.asyncio
async def test_stub_run_returns_str():
    out = await _StubAgentLLM().run(system="s", user_message="u", tools=[])
    assert isinstance(out, str)


def test_build_agent_llm_degraded_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert build_agent_llm(model="claude-haiku-4-5") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agents/assistant/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.agents.assistant.llm'`.

- [ ] **Step 3: Write minimal implementation**

Create `redrobin/application/agents/assistant/llm.py`:

```python
"""Narrow LLM contract for the assistant + a degraded-aware factory.

Mirrors the enricher's ``EnricherLLM`` / ``build_enricher_llm``: a structural
Protocol the assistant depends on, and a factory returning the Anthropic-backed
Tool Runner client when ANTHROPIC_API_KEY is present, else None (the assistant
short-circuits when llm is None, daemon still boots). The Tool Runner beta API
is insulated inside ``AnthropicAgentClient`` behind this Protocol.
"""

from __future__ import annotations

import logging
from typing import Protocol

from redrobin.domain.assistant import AgentTool

logger = logging.getLogger(__name__)


class AgentLLM(Protocol):
    """Run ONE agentic tool-calling cycle: a system prompt + a user message + a
    set of tools -> the final assistant message as text (a JSON object the
    assistant parses into a Recommendation). The tool-loop (call tool -> feed
    result -> repeat until done) happens inside the impl, insulated here."""

    async def run(
        self,
        *,
        system: str,
        user_message: str,
        tools: list[AgentTool],
        max_tokens: int = 2048,
    ) -> str: ...


def build_agent_llm(*, model: str = "claude-haiku-4-5") -> AgentLLM | None:
    try:
        from redrobin.infrastructure.llm.anthropic_client import AnthropicAgentClient

        return AnthropicAgentClient(model=model)
    except RuntimeError:
        logger.info("assistant: ANTHROPIC_API_KEY missing — running degraded (no recommendations)")
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agents/assistant/test_llm.py -v`
Expected: PASS (3 tests). (`build_agent_llm` reaches `AnthropicAgentClient.__init__` → `get_anthropic_key()` raises `RuntimeError` when the key is missing → caught → `None`.)

- [ ] **Step 5: Verify mypy strict (application is in scope)**

Run: `uv run mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/agents/assistant/__init__.py redrobin/application/agents/assistant/llm.py tests/unit/agents/assistant/test_llm.py
git commit -m "feat(assistant): AgentLLM Protocol + degraded build_agent_llm factory"
```

---

## Task 4: `kb_retrieve` tool registry + KB backend factory

The first PULL tool (spec §6.B), over the already-built `KBService.retrieve` (Voyage + numpy `VectorStore`, inherited from Layer B's absorption). `build_kb_service` mirrors `verdict_pipeline_factory.py:104-116`'s KB construction (degraded to `None` without `VOYAGE_API_KEY`). `build_tool_registry` is the extensible seam — later slices append `search_journal` / `get_lessons` without changing its signature.

**Files:**
- Create: `redrobin/application/agents/assistant/tools.py`
- Test: `tests/unit/agents/assistant/test_tools.py`

**Interfaces:**
- Consumes: `KBService` from `redrobin.application.kb_service`; `AgentTool` from `redrobin.domain.assistant`; lazily `VoyageClient`, `VectorStore`, `redrobin_root` (infra).
- Produces:
  - `build_kb_service() -> KBService | None`.
  - `build_tool_registry(kb: KBService | None) -> list[AgentTool]` (slice 1: exactly one tool, `kb_retrieve`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/assistant/test_tools.py`:

```python
from unittest.mock import AsyncMock

import pytest

from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.application.kb_service import KBService
from redrobin.infrastructure.vector_store import VectorStore


def _seeded_kb() -> KBService:
    store = VectorStore()
    store.add(
        ids=["chunk0"],
        texts=["comsvcs.dll MiniDump is a quieter lsass dump than mimikatz"],
        vectors=[[1.0, 0.0]],
        metadatas=[{"source": "vault/Techniques/lsass.md"}],
    )
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    return KBService(voyage=voyage, store=store)


def test_registry_exposes_kb_retrieve():
    tools = build_tool_registry(_seeded_kb())
    assert [t.name for t in tools] == ["kb_retrieve"]
    assert tools[0].input_schema["required"] == ["query"]
    assert tools[0].input_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_kb_retrieve_returns_seeded_source_and_excerpt():
    tools = build_tool_registry(_seeded_kb())
    out = await tools[0].handler({"query": "quiet lsass dump"})
    assert "vault/Techniques/lsass.md" in out
    assert "comsvcs.dll" in out


@pytest.mark.asyncio
async def test_kb_retrieve_degraded_without_kb_is_graceful():
    tools = build_tool_registry(None)
    out = await tools[0].handler({"query": "anything"})
    assert "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_kb_retrieve_empty_query_is_handled():
    tools = build_tool_registry(_seeded_kb())
    out = await tools[0].handler({})
    assert "no query" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agents/assistant/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.agents.assistant.tools'`.

- [ ] **Step 3: Write minimal implementation**

Create `redrobin/application/agents/assistant/tools.py`:

```python
"""Retrieval tools the assistant hands to the AgentLLM each cycle.

Slice 1 ships one PULL tool — ``kb_retrieve`` over ``KBService`` (the built,
curated tradecraft layer). ``build_tool_registry`` is the extensibility seam:
later slices append ``search_journal`` / ``get_lessons`` without touching the
signature. ``build_kb_service`` mirrors ``verdict_pipeline_factory``'s KB
construction (lazy infra import, degraded-None without VOYAGE_API_KEY).
"""

from __future__ import annotations

import logging

from redrobin.application.kb_service import KBService
from redrobin.domain.assistant import AgentTool

logger = logging.getLogger(__name__)

# Return-cap so a large index can never blow the agent's context window (spec
# §6.B refinement 3: cap tool output, return high-signal excerpts not dumps).
_MAX_TOOL_CHARS = 6000

_KB_RETRIEVE_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "A tradecraft / technique / OPSEC question, e.g. 'quiet ways to "
                "validate a MSSQL service credential without triggering EDR'."
            ),
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}


def build_kb_service() -> KBService | None:
    """Build the shared KB retrieval backend (Voyage embeddings + persisted numpy
    VectorStore at ~/.redrobin/shared/kb-index). Returns None when VOYAGE_API_KEY
    is missing (degraded — the assistant still runs; kb_retrieve reports it's
    unavailable). Mirrors verdict_pipeline_factory.build_default_dependencies."""
    try:
        from redrobin.infrastructure.filesystem import redrobin_root
        from redrobin.infrastructure.llm.voyage_client import VoyageClient
        from redrobin.infrastructure.vector_store import VectorStore

        kb_index_path = redrobin_root() / "shared" / "kb-index" / "index.npz"
        store = VectorStore.load(kb_index_path) if kb_index_path.exists() else VectorStore()
        return KBService(voyage=VoyageClient(), store=store)
    except RuntimeError:
        logger.info(
            "assistant: VOYAGE_API_KEY missing — kb_retrieve degraded (no tradecraft retrieval)"
        )
        return None


def build_tool_registry(kb: KBService | None) -> list[AgentTool]:
    """The pull-tool set for one cycle. Slice 1: only ``kb_retrieve``."""

    async def _kb_retrieve(args: dict[str, object]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "No query provided."
        if kb is None:
            return "Knowledge base unavailable (VOYAGE_API_KEY not set)."
        try:
            hits = await kb.retrieve(query, top_k=5)
        except Exception:
            logger.exception("assistant: kb_retrieve failed for query=%s", query)
            return "Knowledge base retrieval failed."
        if not hits:
            return "No matching tradecraft found."
        lines = [f"[source: {source} | score={score:.2f}]\n{text}" for text, source, score in hits]
        return "\n\n".join(lines)[:_MAX_TOOL_CHARS]

    return [
        AgentTool(
            name="kb_retrieve",
            description=(
                "Search the curated red-team knowledge base for tradecraft, technique, and "
                "OPSEC guidance. CALL THIS whenever a recommendation depends on how a technique "
                "works, how to run it quietly, or how a control detects it — then cite the "
                "returned sources in your references."
            ),
            input_schema=_KB_RETRIEVE_SCHEMA,
            handler=_kb_retrieve,
        )
    ]


__all__ = ["build_kb_service", "build_tool_registry"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agents/assistant/test_tools.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify mypy strict**

Run: `uv run mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/agents/assistant/tools.py tests/unit/agents/assistant/test_tools.py
git commit -m "feat(assistant): kb_retrieve tool registry + KB backend factory"
```

---

## Task 5: signal-not-noise `TriggerGate` (dedup / cooldown / novelty)

The cheap deterministic filters of the signal-not-noise gate (spec §6.A): **dedup** (don't re-speak for a correlation already spoken on), **cooldown** (min interval between utterances), **novelty** (speak only if state changed since the last utterance). The model-assessed materiality is the second stage — handled by the model returning "stay silent" (Task 6/7), so it is deliberately NOT in this gate. Clock-injected so the cooldown is deterministic in tests.

**Files:**
- Create: `redrobin/application/agents/assistant/triggers.py`
- Test: `tests/unit/agents/assistant/test_triggers.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `TriggerGate(*, cooldown_sec: float = 30.0, clock: Callable[[], float] = time.monotonic)`.
  - `should_speak(*, correlation_id: str | None, state_signature: str) -> bool`.
  - `record_spoken(*, correlation_id: str | None, state_signature: str) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/assistant/test_triggers.py`:

```python
from redrobin.application.agents.assistant.triggers import TriggerGate


def _gate(now_ref):
    return TriggerGate(cooldown_sec=30.0, clock=lambda: now_ref[0])


def test_speaks_on_first_signal():
    gate = _gate([100.0])
    assert gate.should_speak(correlation_id="c1", state_signature="s1") is True


def test_dedup_same_correlation_is_silent():
    now = [100.0]
    gate = _gate(now)
    gate.record_spoken(correlation_id="c1", state_signature="s1")
    now[0] = 200.0  # well past the cooldown
    assert gate.should_speak(correlation_id="c1", state_signature="s2") is False


def test_novelty_unchanged_state_is_silent():
    now = [100.0]
    gate = _gate(now)
    gate.record_spoken(correlation_id="c1", state_signature="s1")
    now[0] = 200.0
    assert gate.should_speak(correlation_id="c2", state_signature="s1") is False  # same sig


def test_cooldown_blocks_then_allows():
    now = [100.0]
    gate = _gate(now)
    gate.record_spoken(correlation_id="c1", state_signature="s1")
    now[0] = 110.0  # within the 30s cooldown, new state, new corr
    assert gate.should_speak(correlation_id="c2", state_signature="s2") is False
    now[0] = 135.0  # past the cooldown
    assert gate.should_speak(correlation_id="c2", state_signature="s2") is True


def test_silent_cycle_does_not_burn_cooldown_or_dedup():
    # should_speak is a pure check; only record_spoken commits. A cycle that stays
    # silent (never records) must not block the next signal.
    now = [100.0]
    gate = _gate(now)
    assert gate.should_speak(correlation_id="c1", state_signature="s1") is True
    # no record_spoken -> the next distinct signal still speaks
    assert gate.should_speak(correlation_id="c2", state_signature="s2") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agents/assistant/test_triggers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.agents.assistant.triggers'`.

- [ ] **Step 3: Write minimal implementation**

Create `redrobin/application/agents/assistant/triggers.py`:

```python
"""Signal-not-noise gate — the cheap deterministic filters (spec §6.A).

``should_speak`` is a pure check (dedup + novelty + cooldown); ``record_spoken``
commits an utterance so the NEXT cycle dedups/cools-down against it. The split
means a cycle that stays silent (the model returns nothing material) never burns
the cooldown or dedup — only a real recommendation does. The model-assessed
materiality threshold is the second stage and lives in the model, not here.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class TriggerGate:
    _CORR_CACHE_MAX = 256

    def __init__(
        self, *, cooldown_sec: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._cooldown = cooldown_sec
        self._clock = clock
        self._seen_corr: OrderedDict[str, None] = OrderedDict()
        self._last_utterance_at: float | None = None
        self._last_state_sig: str | None = None

    def should_speak(self, *, correlation_id: str | None, state_signature: str) -> bool:
        # dedup: never re-speak for a correlation we've already spoken on
        if correlation_id is not None and correlation_id in self._seen_corr:
            return False
        # novelty: stay quiet if nothing changed since the last utterance
        if state_signature == self._last_state_sig:
            return False
        # cooldown: at most one utterance per cooldown window
        if self._last_utterance_at is not None:
            if (self._clock() - self._last_utterance_at) < self._cooldown:
                return False
        return True

    def record_spoken(self, *, correlation_id: str | None, state_signature: str) -> None:
        """Commit an utterance. Called ONLY after a recommendation is published."""
        self._last_utterance_at = self._clock()
        self._last_state_sig = state_signature
        if correlation_id is not None:
            self._seen_corr[correlation_id] = None
            self._seen_corr.move_to_end(correlation_id)
            while len(self._seen_corr) > self._CORR_CACHE_MAX:
                self._seen_corr.popitem(last=False)  # FIFO eviction


__all__ = ["TriggerGate"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agents/assistant/test_triggers.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/triggers.py tests/unit/agents/assistant/test_triggers.py
git commit -m "feat(assistant): signal-not-noise TriggerGate (dedup/cooldown/novelty)"
```

---

## Task 6: `parse_recommendation` (5-part JSON → `Recommendation`)

Mirrors `enrichment/parser.py` (`parse_deltas`): parse the agent's final message into a typed value. Difference: it NEVER raises — a silent or malformed cycle must not crash the daemon, so it returns `None` (the assistant then simply does not publish → signal-not-noise). Enforces the thesis: a recommendation missing the WHAT or the WHY is dropped (that would be outsourcing, not advising).

**Files:**
- Create: `redrobin/application/agents/assistant/parser.py`
- Test: `tests/unit/agents/assistant/test_parser.py`

**Interfaces:**
- Consumes: `Recommendation` from `redrobin.domain.assistant`; `Severity` from `redrobin.domain.events`.
- Produces: `parse_recommendation(raw: str, *, correlation_id: str | None) -> Recommendation | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/assistant/test_parser.py`:

```python
from redrobin.application.agents.assistant.parser import parse_recommendation
from redrobin.domain.events import Severity


def test_parses_full_recommendation():
    raw = (
        '{"suggested_action":"Validate svc_reports against SQL01",'
        '"rationale":"CrowdStrike observed; a bind is quiet vs a dump",'
        '"opsec_risk":"WARN","confidence":"High cred is valid; unsure scope",'
        '"references":["vault/mssql.md#chunk_0"]}'
    )
    rec = parse_recommendation(raw, correlation_id="18ff")
    assert rec is not None
    assert rec.suggested_action.startswith("Validate")
    assert rec.opsec_risk is Severity.WARN
    assert rec.correlation_id == "18ff"
    assert rec.references == ["vault/mssql.md#chunk_0"]


def test_strips_markdown_fences():
    raw = '```json\n{"suggested_action":"a","rationale":"b"}\n```'
    rec = parse_recommendation(raw, correlation_id=None)
    assert rec is not None and rec.suggested_action == "a"


def test_stay_silent_sentinel_returns_none():
    assert parse_recommendation('{"speak": false}', correlation_id="x") is None


def test_missing_action_or_rationale_returns_none():
    assert parse_recommendation('{"suggested_action":"a"}', correlation_id="x") is None
    assert parse_recommendation('{"rationale":"b"}', correlation_id="x") is None


def test_empty_and_malformed_return_none():
    assert parse_recommendation("", correlation_id="x") is None
    assert parse_recommendation("not json at all", correlation_id="x") is None
    assert parse_recommendation("[1, 2, 3]", correlation_id="x") is None  # not an object


def test_unknown_severity_falls_back_to_info():
    rec = parse_recommendation(
        '{"suggested_action":"a","rationale":"b","opsec_risk":"NOPE"}', correlation_id=None
    )
    assert rec is not None and rec.opsec_risk is Severity.INFO
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agents/assistant/test_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.agents.assistant.parser'`.

- [ ] **Step 3: Write minimal implementation**

Create `redrobin/application/agents/assistant/parser.py`:

```python
"""Parse the agent's final message into a Recommendation.

Mirrors enrichment.parser.parse_deltas, but NEVER raises: a silent or malformed
cycle must not crash the daemon, so it returns None (the assistant simply does
not publish -> signal-not-noise). Enforces the thesis: a recommendation missing
the WHAT (``suggested_action``) or the WHY (``rationale``) is dropped.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redrobin.domain.assistant import Recommendation
from redrobin.domain.events import Severity

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = {s.value for s in Severity}


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def parse_recommendation(raw: str, *, correlation_id: str | None) -> Recommendation | None:
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("assistant: unparseable recommendation JSON — staying silent")
        return None
    if not isinstance(data, dict):
        return None
    if data.get("speak") is False:
        return None  # the model explicitly chose to stay silent
    action = str(data.get("suggested_action", "")).strip()
    rationale = str(data.get("rationale", "")).strip()
    if not action or not rationale:
        return None  # WHAT+WHY are mandatory; without them it is outsourcing, not advice
    risk_raw = str(data.get("opsec_risk", "INFO")).strip().upper()
    risk = Severity(risk_raw) if risk_raw in _VALID_SEVERITIES else Severity.INFO
    confidence = str(data.get("confidence", "")).strip()
    refs_raw = data.get("references") or []
    references = [str(r) for r in refs_raw if isinstance(r, (str, int, float))]
    return Recommendation(
        suggested_action=action,
        rationale=rationale,
        opsec_risk=risk,
        confidence=confidence,
        references=references,
        correlation_id=correlation_id,
    )


__all__ = ["parse_recommendation"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agents/assistant/test_parser.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Verify mypy strict**

Run: `uv run mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/agents/assistant/parser.py tests/unit/agents/assistant/test_parser.py
git commit -m "feat(assistant): parse_recommendation (5-part JSON -> Recommendation)"
```

---

## Task 7: agent cycle — push-context assembly + Tool Runner call

The "how to think one cycle" module (spec's `agent.py` — the agentic loop, Tool Runner underneath the `AgentLLM`). Owns the FROZEN system prompt (the persona + the 5-part JSON output contract, cached as the 1h prefix), the VOLATILE push-context assembly (L2 working-memory snapshot + L5 summary + the trigger command, in the user message), the cheap `state_signature` for the trigger's novelty check, and `run_cycle` (assemble → `AgentLLM.run` → `parse_recommendation`).

**Files:**
- Create: `redrobin/application/agents/assistant/agent.py`
- Test: `tests/unit/agents/assistant/test_agent.py`

**Interfaces:**
- Consumes: `AgentLLM` (Task 3), `parse_recommendation` (Task 6), `AgentTool` + `Recommendation` (Task 1), `build_tool_registry` (Task 4, in tests).
- Produces:
  - `build_system_prompt() -> str`
  - `build_push_context(*, wm_snapshot: dict[str, Any], l5_summary: str, trigger_command: str) -> str`
  - `state_signature(wm_snapshot: dict[str, Any]) -> str`
  - `async run_cycle(*, llm: AgentLLM, tools: list[AgentTool], wm_snapshot: dict[str, Any], l5_summary: str, trigger_command: str, correlation_id: str | None, max_tokens: int = 2048) -> Recommendation | None`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/assistant/test_agent.py`:

```python
import pytest

from redrobin.application.agents.assistant.agent import (
    build_push_context,
    build_system_prompt,
    run_cycle,
    state_signature,
)
from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.domain.events import Severity


def test_system_prompt_names_the_five_parts_and_the_thesis():
    p = build_system_prompt()
    for field in ("suggested_action", "rationale", "opsec_risk", "confidence", "references"):
        assert field in p
    assert "ADVISORY ONLY" in p
    assert "speak" in p  # the stay-silent sentinel is documented


def test_push_context_carries_l2_l5_and_trigger():
    ctx = build_push_context(
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon complete",
        trigger_command="type db_backup.config",
    )
    assert "CrowdStrike" in ctx
    assert "recon complete" in ctx
    assert "type db_backup.config" in ctx


def test_state_signature_changes_with_state():
    assert state_signature({"hosts": []}) != state_signature({"hosts": [{"name": "ws01"}]})


class _FakeAgentLLM:
    """Exercises the kb_retrieve tool (proving the wiring) then returns canned JSON."""

    def __init__(self, response: str):
        self._response = response
        self.seen_tools: list[str] = []

    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        self.seen_tools = [t.name for t in tools]
        for t in tools:
            if t.name == "kb_retrieve":
                await t.handler({"query": "quiet cred validation"})
        return self._response


@pytest.mark.asyncio
async def test_run_cycle_returns_parsed_recommendation_and_wires_tools():
    llm = _FakeAgentLLM(
        '{"suggested_action":"Validate svc_reports vs SQL01",'
        '"rationale":"a quiet bind beats a loud dump under CrowdStrike",'
        '"opsec_risk":"WARN","confidence":"high cred, unsure scope",'
        '"references":["vault/mssql.md#chunk_0"]}'
    )
    rec = await run_cycle(
        llm=llm,
        tools=build_tool_registry(None),
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon done",
        trigger_command="type db_backup.config",
        correlation_id="18ff",
    )
    assert rec is not None
    assert rec.opsec_risk is Severity.WARN
    assert rec.correlation_id == "18ff"
    assert "kb_retrieve" in llm.seen_tools  # the tool set was wired into the cycle


@pytest.mark.asyncio
async def test_run_cycle_silent_returns_none():
    llm = _FakeAgentLLM('{"speak": false}')
    rec = await run_cycle(
        llm=llm,
        tools=build_tool_registry(None),
        wm_snapshot={},
        l5_summary="",
        trigger_command="whoami",
        correlation_id=None,
    )
    assert rec is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agents/assistant/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.agents.assistant.agent'`.

- [ ] **Step 3: Write minimal implementation**

Create `redrobin/application/agents/assistant/agent.py`:

```python
"""One agent cycle: assemble context -> run the Tool Runner loop -> parse.

The FROZEN system prompt (persona + the 5-part output contract) is sent as the
cached 1h prefix by the AgentLLM; the VOLATILE push-context (L2 working-memory
snapshot + L5 summary + the trigger command) goes in the user message, AFTER the
cached prefix (spec §6.B caching refinement 1: never co-locate the mutating
snapshot with the cached prefix). PUSH = small resident working-set; PULL =
the kb_retrieve tool. This is Anthropic's endorsed hybrid strategy (spec §6.B).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redrobin.application.agents.assistant.llm import AgentLLM
from redrobin.application.agents.assistant.parser import parse_recommendation
from redrobin.domain.assistant import AgentTool, Recommendation

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are Red Robin, an operation-aware AI copilot behind a red-team operator's C2. "
    "You are ADVISORY ONLY: you propose the next step, you never execute it and never "
    "dispatch to the beacon. You are SELECTIVE — speak only when you have something material "
    "and grounded; otherwise stay silent.\n\n"
    "You are given the operator's current engagement state (working memory) and the story so "
    "far. Use the kb_retrieve tool to ground tradecraft claims in the knowledge base; reason "
    "over what the tools and the state return, never from memory, and never invent a fact.\n\n"
    "Return ONLY a JSON object, no prose, no markdown fences. To stay silent, return "
    '{"speak": false}. To recommend, return these five fields (ALL required):\n'
    '{"suggested_action": "the concrete next step — the WHAT",\n'
    ' "rationale": "the tradecraft WHY given the engagement state (without this it is '
    'outsourcing, not advising)",\n'
    ' "opsec_risk": "one of INFO|WARN|DANGER — the detection risk, grounded in '
    'defenses_observed, with a short reason",\n'
    ' "confidence": "how sure you are AND what you are explicitly NOT sure about",\n'
    ' "references": ["the grounding you drew on — kb sources you retrieved plus the '
    'working-memory facts / command output you used"]}'
)


def build_system_prompt() -> str:
    """The frozen persona + output contract. Static, so it caches as the Tool
    Runner's 1h prefix."""
    return _SYSTEM_PROMPT


def build_push_context(*, wm_snapshot: dict[str, Any], l5_summary: str, trigger_command: str) -> str:
    """Assemble the small, token-bounded PUSH working-set: L2 (current working
    memory) + L5 (story so far) + the triggering command. The volatile block —
    lives in the user message, after the cached system prefix."""
    wm_json = json.dumps(wm_snapshot, ensure_ascii=False, sort_keys=True)
    return "\n".join(
        [
            "# Engagement working memory (L2)",
            wm_json,
            "",
            "# Story so far (L5)",
            l5_summary or "(no summary yet)",
            "",
            "# What just happened",
            f"The operator ran: {trigger_command or '(unknown command)'}",
            "",
            "Given this, is there a material, grounded next-step recommendation? If yes, "
            'retrieve any tradecraft you need and return the 5-part JSON. If not, return {"speak": false}.',
        ]
    )


def state_signature(wm_snapshot: dict[str, Any]) -> str:
    """A cheap, stable signature of engagement state for the trigger's novelty
    check — changes iff the working memory changed."""
    return json.dumps(wm_snapshot, ensure_ascii=False, sort_keys=True)


async def run_cycle(
    *,
    llm: AgentLLM,
    tools: list[AgentTool],
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    trigger_command: str,
    correlation_id: str | None,
    max_tokens: int = 2048,
) -> Recommendation | None:
    """Run ONE agent cycle: assemble -> Tool Runner loop (via AgentLLM) -> parse
    into a Recommendation, or None to stay silent."""
    raw = await llm.run(
        system=build_system_prompt(),
        user_message=build_push_context(
            wm_snapshot=wm_snapshot, l5_summary=l5_summary, trigger_command=trigger_command
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    return parse_recommendation(raw, correlation_id=correlation_id)


__all__ = ["build_system_prompt", "build_push_context", "state_signature", "run_cycle"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agents/assistant/test_agent.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify mypy strict**

Run: `uv run mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/agents/assistant/agent.py tests/unit/agents/assistant/test_agent.py
git commit -m "feat(assistant): agent cycle — push-context assembly + Tool Runner call"
```

---

## Task 8: reactive bus consumer → `advisor.recommendation`

The "when to think" module — the `Assistant` bus consumer (spec's `assistant.py`). Mirrors `WorkingMemoryEnricher.run`: subscribe, handle the close sentinel, dispatch by event type, PUBLISH (never write memory), catch every exception per event (never crash), degrade to silent when `llm is None`. It caches the latest `working_memory_snapshot` (the L2 push context — bus-native, like the enricher caches `beacon.initial`), and on each `enrichment.completed` runs the trigger gate → one cycle → publishes `advisor.recommendation`. The trigger/dedup key is `f"{beacon_id}:{command}"` (the wire `correlation_id` is not on `enrichment.completed` — see note).

**Note (correlation_id):** `enrichment.completed`'s payload carries `command` + `beacon_id` (not the wire `correlation_id`; see `enricher.py:167-175`). Slice 1 uses `f"{beacon_id}:{command}"` as the reactive attach/dedup handle — an honest handle to the triggering command. Threading the true `correlation_id` through `enrichment.completed` is a one-line enricher change deferred to a later slice.

**Files:**
- Create: `redrobin/application/agents/assistant/assistant.py`
- Test: `tests/integration/test_assistant.py`

**Interfaces:**
- Consumes: `EventBus` (`redrobin.application.event_bus`); `run_cycle` + `state_signature` (Task 7); `TriggerGate` (Task 5); `AgentLLM` (Task 3); `AgentTool` (Task 1). Bus events `working_memory_snapshot` (payload = `working_memory_to_snapshot(wm)`), `enrichment.completed` (payload `{command, beacon_id, ...}`).
- Produces:
  - `Assistant(*, bus: EventBus, llm: AgentLLM | None, tools: list[AgentTool], trigger: TriggerGate, summary_reader: Callable[[], str], enabled: bool = True, max_tokens: int = 2048)` with `on_started: Callable[[], None] | None`, `async run() -> None`.
  - Publishes `{"type": "advisor.recommendation", "payload": Recommendation.to_payload()}`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_assistant.py`:

```python
"""Integration: the assistant consumes enrichment.completed and publishes
advisor.recommendation (fake AgentLLM — no real API). Mirrors test_enricher.py."""

from __future__ import annotations

import asyncio

import pytest

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus


class _FakeAgentLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        self.calls += 1
        return self._response


class _RaisingAgentLLM:
    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        raise RuntimeError("boom")


async def _start(bus, llm):
    assistant = Assistant(
        bus=bus,
        llm=llm,
        tools=build_tool_registry(None),
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
        summary_reader=lambda: "recon done",
    )
    started = asyncio.Event()
    assistant.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(assistant.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    return assistant, task


async def _drain(bus, task):
    await bus.close()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_assistant_publishes_recommendation_on_enrichment():
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _FakeAgentLLM(
        '{"suggested_action":"Validate svc_reports vs SQL01",'
        '"rationale":"a quiet bind beats a loud dump under CrowdStrike",'
        '"opsec_risk":"WARN","confidence":"high cred, unsure scope",'
        '"references":["type db_backup.config"]}'
    )
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "working_memory_snapshot",
                "payload": {
                    "defenses_observed": ["CrowdStrike"],
                    "credentials": [{"username": "svc_reports"}],
                },
            }
        )
        await bus.publish(
            {
                "type": "enrichment.completed",
                "payload": {"command": "type db_backup.config", "beacon_id": "B-7", "n_deltas": 2},
            }
        )
        rec_evt = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "advisor.recommendation":
                rec_evt = evt
                break
        assert rec_evt is not None, "assistant did not publish advisor.recommendation"
        p = rec_evt["payload"]
        assert p["opsec_risk"] == "WARN"
        assert p["suggested_action"].startswith("Validate")
        assert p["references"]
        assert p["correlation_id"] == "B-7:type db_backup.config"
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_assistant_degraded_without_llm_is_silent():
    bus = EventBus()
    observer = await bus.subscribe()
    _, task = await _start(bus, None)  # no llm -> degraded
    try:
        await bus.publish(
            {"type": "enrichment.completed", "payload": {"command": "whoami", "beacon_id": "B-1"}}
        )
        await asyncio.sleep(0.2)
        seen = []
        try:
            while True:
                seen.append(observer.get_nowait().get("type"))
        except asyncio.QueueEmpty:
            pass
        assert "advisor.recommendation" not in seen
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_assistant_survives_cycle_exception():
    bus = EventBus()
    await bus.subscribe()
    _, task = await _start(bus, _RaisingAgentLLM())
    try:
        await bus.publish(
            {"type": "working_memory_snapshot", "payload": {"hosts": [{"name": "ws01"}]}}
        )
        await bus.publish(
            {"type": "enrichment.completed", "payload": {"command": "systeminfo", "beacon_id": "B-1"}}
        )
        await asyncio.sleep(0.2)
        assert not task.done()  # the raising cycle was caught; the consumer is alive
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_assistant_silent_model_does_not_publish():
    bus = EventBus()
    observer = await bus.subscribe()
    _, task = await _start(bus, _FakeAgentLLM('{"speak": false}'))
    try:
        await bus.publish({"type": "working_memory_snapshot", "payload": {"hosts": []}})
        await bus.publish(
            {"type": "enrichment.completed", "payload": {"command": "sleep 5", "beacon_id": "B-1"}}
        )
        await asyncio.sleep(0.2)
        seen = []
        try:
            while True:
                seen.append(observer.get_nowait().get("type"))
        except asyncio.QueueEmpty:
            pass
        assert "advisor.recommendation" not in seen
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_assistant.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.application.agents.assistant.assistant'`.

- [ ] **Step 3: Write minimal implementation**

Create `redrobin/application/agents/assistant/assistant.py`:

```python
"""Assistant — reactive grounded-recommendation bus consumer (slice 1).

Mirrors WorkingMemoryEnricher: subscribes to the bus, PUBLISHES (never writes
memory), runs degraded-None (no llm -> silent), and never crashes the daemon
(every per-event exception is logged and swallowed). It caches the latest
working_memory_snapshot (the L2 push context) and, on each enrichment.completed,
runs the signal-not-noise gate -> one agent cycle -> publishes
advisor.recommendation. Emits observability via the recommendation event + logs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from redrobin.application.agents.assistant.agent import run_cycle, state_signature
from redrobin.application.agents.assistant.llm import AgentLLM
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus
from redrobin.domain.assistant import AgentTool

logger = logging.getLogger(__name__)


class Assistant:
    def __init__(
        self,
        *,
        bus: EventBus,
        llm: AgentLLM | None,
        tools: list[AgentTool],
        trigger: TriggerGate,
        summary_reader: Callable[[], str],
        enabled: bool = True,
        max_tokens: int = 2048,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._tools = tools
        self._trigger = trigger
        self._summary_reader = summary_reader
        self._enabled = enabled
        self._max_tokens = max_tokens
        self._wm_snapshot: dict[str, Any] = {}
        self.on_started: Callable[[], None] | None = None

    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("assistant on_started callback raised")
        try:
            while True:
                evt = await sub.get()
                if evt.get("_type") == "_close":
                    return
                etype = evt.get("type")
                if etype == "working_memory_snapshot":
                    payload = evt.get("payload")
                    if isinstance(payload, dict):
                        self._wm_snapshot = payload
                    continue
                if etype != "enrichment.completed":
                    continue
                try:
                    await self._on_enrichment(evt.get("payload") or {})
                except Exception:
                    logger.exception("assistant failed to process enrichment.completed")
        finally:
            await self._bus.unsubscribe(sub)

    async def _on_enrichment(self, payload: dict[str, Any]) -> None:
        if self._llm is None or not self._enabled:
            return
        beacon_id = payload.get("beacon_id")
        trigger_command = str(payload.get("command") or "")
        # Reactive attach/dedup handle (enrichment.completed has no wire correlation_id).
        trigger_key: str | None = (
            f"{beacon_id}:{trigger_command}" if trigger_command else None
        )
        sig = state_signature(self._wm_snapshot)
        if not self._trigger.should_speak(correlation_id=trigger_key, state_signature=sig):
            return
        try:
            l5_summary = self._summary_reader()
        except Exception:
            logger.exception("assistant: summary_reader failed; proceeding with empty L5")
            l5_summary = ""
        rec = await run_cycle(
            llm=self._llm,
            tools=self._tools,
            wm_snapshot=self._wm_snapshot,
            l5_summary=l5_summary,
            trigger_command=trigger_command,
            correlation_id=trigger_key,
            max_tokens=self._max_tokens,
        )
        if rec is None:
            return  # signal-not-noise: the model stayed silent, or the output was unusable
        self._trigger.record_spoken(correlation_id=trigger_key, state_signature=sig)
        await self._bus.publish(
            {"type": "advisor.recommendation", "payload": rec.to_payload()}
        )
        logger.info(
            "assistant: published advisor.recommendation command=%s beacon=%s risk=%s",
            trigger_command,
            beacon_id,
            rec.opsec_risk.value,
        )


__all__ = ["Assistant"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_assistant.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify mypy strict**

Run: `uv run mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/agents/assistant/assistant.py tests/integration/test_assistant.py
git commit -m "feat(assistant): reactive bus consumer -> advisor.recommendation"
```

---

## Task 9: TUI advisor-dock handler (reveal + 5-part card)

The live output surface. `tui_ws` already forwards ALL bus events to the TUI (zero changes), and `#advisor-dock` already exists (reserved, `display: none`). This task adds the `advisor.recommendation` frame handler that reveals the dock and renders the 5-part card plainly (spec §6.C — polish/interaction deferred). "Un-hide" is implemented as **reveal-on-first-recommendation** (the existing `#resize-hint` toggle pattern: `widget.display = True` overrides the `.tcss` rule), so the dock shows the moment there is something to show rather than sitting empty — and the existing `test_advisor_dock_is_hidden` stays green. `register_advisor(REGISTRY)` binds the handler without a circular import back into `app.py`.

**Files:**
- Create: `redrobin/tui/textual_ui/advisor.py`
- Modify: `redrobin/tui/textual_ui/app.py` (import + `register_advisor(REGISTRY)` after `REGISTRY = Registry()`, ~line 36)
- Modify: `redrobin/tui/textual_ui/theme.tcss` (advisor-dock child styling)
- Test: `tests/unit/tui_textual/test_advisor.py`

**Interfaces:**
- Consumes: `Registry` (`redrobin.tui.textual_ui.frames`); `Static` + `NoMatches` (textual); `rich.text.Text`.
- Produces:
  - `format_recommendation(payload: dict[str, Any]) -> Text` (pure).
  - `_on_recommendation(app: Any, payload: dict[str, Any]) -> None` (FrameHandler).
  - `register_advisor(registry: Registry) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/tui_textual/test_advisor.py`:

```python
import pytest
from textual.widgets import Static

from redrobin.tui.textual_ui.advisor import format_recommendation
from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp

_PAYLOAD = {
    "suggested_action": "Validate svc_reports vs SQL01",
    "rationale": "a quiet bind beats a loud dump",
    "opsec_risk": "WARN",
    "confidence": "high cred, unsure scope",
    "references": ["vault/mssql.md#chunk_0"],
    "correlation_id": "B-7:type db_backup.config",
}


def test_format_recommendation_includes_all_five_parts():
    text = format_recommendation(_PAYLOAD).plain
    assert "Validate svc_reports" in text
    assert "quiet bind" in text
    assert "WARN" in text
    assert "unsure scope" in text
    assert "vault/mssql.md#chunk_0" in text
    assert "advisory only" in text.lower()


def test_registry_has_advisor_handler():
    assert REGISTRY.has_handler("advisor.recommendation")


@pytest.mark.asyncio
async def test_advisor_dock_reveals_and_renders_on_frame():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        # hidden until a recommendation arrives (reserved seam)
        assert app.query_one("#advisor-dock").display is False
        REGISTRY.dispatch(target=app, frame={"type": "advisor.recommendation", "payload": _PAYLOAD})
        await pilot.pause()  # let the mount settle
        dock = app.query_one("#advisor-dock")
        assert dock.display is True
        card = app.query_one("#advisor-card", Static)
        assert "Validate svc_reports" in card.renderable.plain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tui_textual/test_advisor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'redrobin.tui.textual_ui.advisor'`.

- [ ] **Step 3: Create the advisor module**

Create `redrobin/tui/textual_ui/advisor.py`:

```python
"""AI Advisor dock handler — renders advisor.recommendation frames.

`tui_ws` forwards every bus event to the TUI, so the assistant's
`advisor.recommendation` arrives here as a frame. `format_recommendation` is a
pure Rich renderer (mirrors panels.format_memory) so it unit-tests directly.
The handler reveals the reserved `#advisor-dock` (hidden until the first
recommendation — the resize-hint toggle pattern) and renders the 5-part card
plainly (spec §6.C — polish + interaction affordances deferred to later slices).
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.css.query import NoMatches
from textual.widgets import Static

from redrobin.tui.textual_ui.frames import Registry

# Mirror the $verdict-*/sev tokens in theme.tcss (Rich styles take concrete hexes).
_RISK_COLORS = {"INFO": "#3fb950", "WARN": "#e3b341", "DANGER": "#f85149", "ESCALATE": "#58a6ff"}
_ACCENT = "#58a6ff"
_MUTED = "#8b949e"


def format_recommendation(payload: dict[str, Any]) -> Text:
    """Render the 5-part advisor card as Rich Text (plain — polish deferred)."""
    action = str(payload.get("suggested_action") or "")
    rationale = str(payload.get("rationale") or "")
    risk = str(payload.get("opsec_risk") or "INFO")
    confidence = str(payload.get("confidence") or "")
    references = payload.get("references") or []
    t = Text()
    t.append("ADVISOR", style=f"bold {_ACCENT}")
    t.append("  (advisory only — nothing dispatched)\n", style=_MUTED)
    t.append("→ ", style="bold")
    t.append(f"{action}\n", style="bold")
    t.append("why    ", style=_MUTED)
    t.append(f"{rationale}\n")
    t.append("opsec  ", style=_MUTED)
    t.append(f"{risk}\n", style=f"bold {_RISK_COLORS.get(risk, 'white')}")
    t.append("conf   ", style=_MUTED)
    t.append(f"{confidence}\n")
    t.append("refs   ", style=_MUTED)
    t.append(", ".join(str(r) for r in references) if references else "(none)")
    return t


def _on_recommendation(app: Any, payload: dict[str, Any]) -> None:
    """Reveal the docked advisor pane and render the latest recommendation."""
    try:
        dock = app.query_one("#advisor-dock")
    except NoMatches:
        return
    dock.display = True  # inline style overrides theme.tcss `display: none`
    text = format_recommendation(payload)
    try:
        card = app.query_one("#advisor-card", Static)
        card.update(text)
    except NoMatches:
        dock.mount(Static(text, id="advisor-card"))


def register_advisor(registry: Registry) -> None:
    """Bind the advisor.recommendation handler onto the shared registry. Called
    once from app.py after REGISTRY is defined (keeps app.py free of a circular
    import back into this module)."""
    registry.register("advisor.recommendation")(_on_recommendation)


__all__ = ["format_recommendation", "register_advisor"]
```

- [ ] **Step 4: Register the handler in `app.py`**

In `redrobin/tui/textual_ui/app.py`, add the import near the other `redrobin.tui.textual_ui` imports (after the `frames` import, ~line 22):

```python
from redrobin.tui.textual_ui.advisor import register_advisor
```

Then, immediately after `REGISTRY = Registry()` (line 36), add:

```python
# Advisor frames register on the shared REGISTRY via a binder (avoids a circular
# import: advisor.py imports only frames, never app).
register_advisor(REGISTRY)
```

- [ ] **Step 5: Add minimal dock styling in `theme.tcss`**

In `redrobin/tui/textual_ui/theme.tcss`, replace the existing advisor-dock rule (near line 48-54) with (keeps `display: none` as the resting state; the handler reveals it):

```css
/* AI Advisor dock (slice 1). Reserved + hidden; the advisor.recommendation
   handler flips display on the first recommendation (reveal-on-first, like
   #resize-hint). Plain card — polish deferred to a later slice. */
#advisor-dock {
    display: none;
    dock: bottom;
    height: auto;
    border-top: heavy $accent;
    padding: 0 1;
}
#advisor-card {
    height: auto;
    width: 100%;
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/tui_textual/test_advisor.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Verify the existing dock test still passes (reveal-on-first didn't break it)**

Run: `uv run pytest tests/unit/tui_textual/test_app_layout.py -v`
Expected: PASS (including `test_advisor_dock_is_hidden` — no frame dispatched, so the dock stays hidden).

- [ ] **Step 8: Commit**

```bash
git add redrobin/tui/textual_ui/advisor.py redrobin/tui/textual_ui/app.py redrobin/tui/textual_ui/theme.tcss tests/unit/tui_textual/test_advisor.py
git commit -m "feat(tui): advisor-dock recommendation handler (reveal + 5-part card)"
```

---

## Task 10: assistant `Settings` + `start_assistant` lifespan wiring

Wire the assistant into the daemon. Adds assistant `Settings` (enable / model / cooldown / token budget — mirroring the enrichment settings), a `start_assistant` lifespan helper (mirroring `start_enricher`: degraded-None boot, drain-on-close), and registers it in the `app.py` lifespan orchestrator (startup after `start_enricher`; shutdown before `close_enricher` — reverse order). The L5 push context is read via the injected `summary_reader` closure over `ContextSummaryRepo.list_recent`.

**Files:**
- Modify: `redrobin/infrastructure/config.py` (add 4 fields + 2 validator entries)
- Modify: `redrobin/api/lifespan_helpers.py` (imports + `start_assistant` + `__all__`)
- Modify: `redrobin/api/app.py` (import + startup call + shutdown close)
- Test: `tests/integration/test_assistant_lifespan.py`

**Interfaces:**
- Consumes: `Assistant` (Task 8), `build_agent_llm` (Task 3), `build_kb_service` + `build_tool_registry` (Task 4), `TriggerGate` (Task 5). `app.state.event_bus`, `app.state.summary_repo`, `app.state.engagement_id` (set by earlier helpers).
- Produces:
  - `Settings.assistant_enabled: bool`, `assistant_model: str`, `assistant_cooldown_sec: float`, `assistant_max_tokens: int`.
  - `start_assistant(app: FastAPI, settings: Settings) -> tuple[Assistant, CloseHook]`; `app.state.assistant`, `app.state.assistant_task`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_assistant_lifespan.py`:

```python
"""The assistant boots (degraded, no API key) as a lifespan task, and settings
carry the assistant knobs. Mirrors test_enricher.py::test_lifespan_starts_enricher_task."""

from __future__ import annotations

import pytest


def test_settings_expose_assistant_knobs(monkeypatch):
    monkeypatch.delenv("REDROBIN_ASSISTANT_MODEL", raising=False)
    from redrobin.application.config import Settings

    s = Settings()
    assert s.assistant_enabled is True
    assert s.assistant_model == "claude-haiku-4-5"
    assert s.assistant_cooldown_sec == 30.0
    assert s.assistant_max_tokens == 2048


def test_settings_assistant_model_override(monkeypatch):
    monkeypatch.setenv("REDROBIN_ASSISTANT_MODEL", "claude-sonnet-4-6")
    from redrobin.application.config import Settings

    assert Settings().assistant_model == "claude-sonnet-4-6"


def test_start_assistant_is_importable():
    from redrobin.api.lifespan_helpers import start_assistant

    assert callable(start_assistant)


@pytest.mark.asyncio
async def test_lifespan_starts_assistant_task(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-asst")
    # No ANTHROPIC_API_KEY / VOYAGE_API_KEY -> assistant boots degraded (llm None), task still runs.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.assistant_task is not None
        assert not app.state.assistant_task.done()

    # Clean teardown: the assistant drains via the bus _close sentinel (close_bus
    # runs before close_assistant), NOT the 2.0s cancel fallback.
    assert app.state.assistant_task.done()
    assert app.state.assistant_task.cancelled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_assistant_lifespan.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'assistant_enabled'` (and the import of `start_assistant` fails).

- [ ] **Step 3: Add the assistant Settings**

In `redrobin/infrastructure/config.py`, add these fields immediately after the enrichment block (after `enrichment_self_annotation_prefix`, before the `# Operator policy` comment, ~line 82):

```python
    # AI assistant (reactive grounded recommendation — slice 1)
    assistant_enabled: bool = Field(default=True, alias="REDROBIN_ASSISTANT_ENABLED")
    assistant_model: str = Field(default="claude-haiku-4-5", alias="REDROBIN_ASSISTANT_MODEL")
    assistant_cooldown_sec: float = Field(default=30.0, alias="REDROBIN_ASSISTANT_COOLDOWN_SEC")
    assistant_max_tokens: int = Field(default=2048, alias="REDROBIN_ASSISTANT_MAX_TOKENS")
```

Add `"assistant_max_tokens"` to the `_positive_int` `@field_validator` list (currently ends with `"enrichment_max_concurrency",`):

```python
    @field_validator(
        "promoter_min_occurrences",
        "l5_window",
        "enrichment_min_output_chars",
        "enrichment_chunk_max_chars",
        "enrichment_max_concurrency",
        "assistant_max_tokens",
    )
```

Add `"assistant_cooldown_sec"` to the `_positive_float` `@field_validator` list:

```python
    @field_validator("promoter_interval_sec", "l5_interval_sec", "assistant_cooldown_sec")
```

- [ ] **Step 4: Add `start_assistant` to `lifespan_helpers.py`**

Add these imports near the top of `redrobin/api/lifespan_helpers.py` (with the other `redrobin.application` imports):

```python
from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.llm import build_agent_llm
from redrobin.application.agents.assistant.tools import build_kb_service, build_tool_registry
from redrobin.application.agents.assistant.triggers import TriggerGate
```

Add the helper (place it after `start_enricher`, before `start_dedup`):

```python
async def start_assistant(app: FastAPI, settings: Settings) -> tuple[Assistant, CloseHook]:
    """Start the reactive AI assistant (slice 1).

    Subscribes to the bus, consumes ``enrichment.completed``, and publishes
    ``advisor.recommendation``. Degraded-None: missing ANTHROPIC_API_KEY ->
    ``build_agent_llm`` returns None and the assistant stays silent; missing
    VOYAGE_API_KEY -> ``build_kb_service`` returns None and kb_retrieve degrades;
    either way the daemon still boots. Mirrors ``start_enricher``.

    Reads the L5 summary via a closure over ``app.state.summary_repo`` (the
    ContextSummaryRepo the summariser writes) so the push-context carries the
    story-so-far without coupling the assistant to persistence.
    """
    summary_repo = app.state.summary_repo
    engagement_id = app.state.engagement_id

    def _read_summary() -> str:
        try:
            rows = summary_repo.list_recent(engagement_id, limit=1)
        except Exception:
            logger.exception("assistant: failed reading L5 summary")
            return ""
        return rows[0].text if rows else ""

    assistant = Assistant(
        bus=app.state.event_bus,
        llm=build_agent_llm(model=settings.assistant_model),
        tools=build_tool_registry(build_kb_service()),
        trigger=TriggerGate(cooldown_sec=settings.assistant_cooldown_sec),
        summary_reader=_read_summary,
        enabled=settings.assistant_enabled,
        max_tokens=settings.assistant_max_tokens,
    )
    started = asyncio.Event()
    assistant.on_started = started.set  # type: ignore[attr-defined]
    assistant_task = asyncio.create_task(assistant.run(), name="assistant")
    await started.wait()
    app.state.assistant = assistant
    app.state.assistant_task = assistant_task

    async def close() -> None:
        try:
            await asyncio.wait_for(assistant_task, timeout=2.0)
        except asyncio.TimeoutError:
            assistant_task.cancel()

    return assistant, close
```

Add `"start_assistant"` to the `__all__` list at the bottom of the module (alphabetical, after `"start_adapters"`):

```python
    "start_assistant",
```

- [ ] **Step 5: Wire `start_assistant` into the `app.py` lifespan**

In `redrobin/api/app.py`, add `start_assistant` to the import block from `redrobin.api.lifespan_helpers` (alphabetical, after `start_adapters`):

```python
    start_assistant,
```

In the startup sequence, add the assistant immediately after the enricher line (`_, close_enricher = await start_enricher(app, settings)`, ~line 96):

```python
    _, close_assistant = await start_assistant(app, settings)
```

In the shutdown `finally` block, add a close call immediately BEFORE the existing `close_enricher` block (reverse-of-startup order — the assistant started after the enricher, so it closes first). Match the surrounding try/except style:

```python
        try:
            await close_assistant()
        except Exception:
            logger.exception("close_assistant failed")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_assistant_lifespan.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the full gate**

Run:
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy redrobin/domain redrobin/application
uv run lint-imports --config importlinter.cfg
uv run pytest tests/ -v --tb=short
```
Expected: all PASS. (This is the deterministic acceptance for the whole CI-testable slice.)

- [ ] **Step 8: Commit**

```bash
git add redrobin/infrastructure/config.py redrobin/api/lifespan_helpers.py redrobin/api/app.py tests/integration/test_assistant_lifespan.py
git commit -m "feat(assistant): Settings + start_assistant lifespan wiring"
```

---

## Task 11: operator-smoke fixture + runbook (the live-surface acceptance)

The live surface — the `#advisor-dock` rendering a real recommendation over a real beacon, the enricher/reducer producing real working memory — is **not** CI-testable (`development-workflow.md` §5). The ONLY acceptance for it is an **operator smoke** (skill **smoke-test**). This task renames the draft fixture, EXPANDS it to a richer recon→loot→follow-the-hint scenario (a second breadcrumb the first points to), and writes the step-by-step runbook with checkpoints tagged `[Slice 1]` (expected now) vs `[later slice]` (deliberately NOT expected). No TDD — a lab fixture + doc; acceptance is the manual smoke.

**Files:**
- Rename: `scripts/smoke/rr-slice1-fixture.ps1` → `scripts/smoke/rr-assistant-smoke-fixture.ps1` (+ expand)
- Create: `scripts/smoke/rr-assistant-smoke-runbook.md`

**Interfaces:** none (operator tooling).

- [ ] **Step 1: Rename the fixture (preserve history)**

```bash
git mv scripts/smoke/rr-slice1-fixture.ps1 scripts/smoke/rr-assistant-smoke-fixture.ps1
```

- [ ] **Step 2: Expand the fixture — add the "follow-the-hint" second breadcrumb**

Replace the whole contents of `scripts/smoke/rr-assistant-smoke-fixture.ps1` with (adds a third decoy at `exports\svc_sql_readme.txt` that the `notes_old.txt` hint points to, so the operator can demonstrate recon → loot → follow the hint across two steps):

```powershell
<#
  rr-assistant-smoke-fixture.ps1
  Red Robin - AI-assistant operator-smoke fixture (reactive grounded recommendation).

  Plants a tiny, OBVIOUSLY-FAKE loot surface so the smoke runs with ZERO
  victim-machine config: no admin, no new users, no privileges, no AD. It is a
  small folder of decoy text files, fully removed by -Cleanup.

  The scenario is a recon -> loot -> follow-the-hint chain:
    1. db_backup.config  - a FAKE service credential (svc_reports / SQL01)
    2. notes_old.txt     - a note pointing at an old file server share
    3. exports\svc_sql_readme.txt - the "followed hint": a second FAKE finding
       (svc_sql, described as broadly privileged) that the note leads to.

  All credentials are fake, for servers that do not exist. Safe on any lab
  Windows box you control.

  Run (normal PowerShell, no admin needed):
    powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1
    powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup
#>
[CmdletBinding()]
param(
  [switch]$Cleanup,
  [string]$Path
)

# No-admin, ideally cross-user-readable location: C:\Users\Public\RR-Smoke.
if (-not $Path) { $Path = Join-Path $env:PUBLIC 'RR-Smoke' }
$Fallback = Join-Path $env:USERPROFILE 'Documents\RR-Smoke'

if ($Cleanup) {
  foreach ($p in @($Path, $Fallback)) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "[rr-smoke] removed $p" }
  }
  return
}

# Create the fixture dir; fall back to the user's Documents if Public is not writable.
try {
  New-Item -ItemType Directory -Path $Path -Force -ErrorAction Stop | Out-Null
  $Root = $Path
} catch {
  New-Item -ItemType Directory -Path $Fallback -Force | Out-Null
  $Root = $Fallback
}
$Exports = Join-Path $Root 'exports'
New-Item -ItemType Directory -Path $Exports -Force | Out-Null

# Decoy 1 - a "DB backup" config carrying a FAKE service credential.
@'
# weekly DB backup job  (RR-SMOKE FIXTURE - FAKE, not real loot)
[connection]
Server   = SQL01.corp.local
Database = ReportsDW
User Id  = svc_reports
Password = R3p0rts-B@ckup!2023
'@ | Set-Content -LiteralPath (Join-Path $Root 'db_backup.config') -Encoding UTF8

# Decoy 2 - a stray note hinting at a share (a 2nd finding for the enricher, and
# the breadcrumb the operator follows to decoy 3).
@'
TODO: migrate the weekly export off the old file server.
  \\FILES01\Finance\exports\   (svc_reports has read there; see exports\svc_sql_readme.txt)
'@ | Set-Content -LiteralPath (Join-Path $Root 'notes_old.txt') -Encoding UTF8

# Decoy 3 - the "followed hint": a second, higher-value FAKE finding the note
# points to (svc_sql, described as broadly privileged -> a path-to-DA breadcrumb).
@'
readme for the migrated exports  (RR-SMOKE FIXTURE - FAKE, not real loot)
The export job runs as CORP\svc_sql. That account is a member of the SQL admins
group and is used across most of the estate. Old creds were:
  CORP\svc_sql : S3rv3r-Sql!Adm2022
'@ | Set-Content -LiteralPath (Join-Path $Exports 'svc_sql_readme.txt') -Encoding UTF8

Write-Host ""
Write-Host "[rr-smoke] fixture planted at: $Root" -ForegroundColor Green
Write-Host ""
Write-Host "From your beacon (normal user - no privesc). Two recon steps:" -ForegroundColor Cyan
Write-Host "  1) loot the config:" -ForegroundColor Cyan
Write-Host "       shell dir  `"$Root`""
Write-Host "       shell type `"$Root\db_backup.config`""
Write-Host "       shell type `"$Root\notes_old.txt`""
Write-Host "  2) follow the hint the note points to:" -ForegroundColor Cyan
Write-Host "       shell dir  `"$Exports`""
Write-Host "       shell type `"$Exports\svc_sql_readme.txt`""
Write-Host ""
Write-Host "Expect within a few seconds of each step: an advisor card in #advisor-dock" -ForegroundColor Cyan
Write-Host "with the 5 parts (WHAT / why / opsec / conf / refs), refs citing your 'type'"
Write-Host "output + a KB chunk. Nothing is dispatched. See the runbook for checkpoints."
Write-Host ""
Write-Host "Cleanup when done:  powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup" -ForegroundColor DarkGray
```

- [ ] **Step 3: Write the runbook**

Create `scripts/smoke/rr-assistant-smoke-runbook.md`:

````markdown
# Operator smoke — AI assistant slice 1 (reactive grounded recommendation)

The live surface (the `#advisor-dock` render over a real CS beacon + the
enricher/reducer over real output) is NOT CI-testable — this manual smoke is its
only acceptance (skill **smoke-test**, `development-workflow.md` §5). Checkpoints
are tagged `[Slice 1]` (must pass now) or `[later slice]` (deliberately NOT
expected yet — do not fail the smoke on these).

## Preconditions
- Daemon built + running with `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` exported
  (without them the assistant/KB run degraded — see the degraded checkpoint).
- KB index populated (`redrobin kb add ...` / `kb ingest`) so `kb_retrieve` has
  content — otherwise references cite only the command output.
- The Textual TUI connected (`redrobin tui`), a live CS beacon on a lab box, and
  the CS adapter connected to the daemon.

## Setup
1. On the lab box, plant the fixture:
   `powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1`
2. Note the printed fixture path (`C:\Users\Public\RR-Smoke` or the Documents fallback).

## Run — step 1: loot the config
From the beacon:
```
shell dir  "C:\Users\Public\RR-Smoke"
shell type "C:\Users\Public\RR-Smoke\db_backup.config"
shell type "C:\Users\Public\RR-Smoke\notes_old.txt"
```

- **[Slice 1] Checkpoint A — an advisor card appears** in `#advisor-dock` within a
  few seconds (the dock reveals itself; it was hidden before). It appears
  *reactively*, triggered by the command output — you did not ask for it.
- **[Slice 1] Checkpoint B — the card has all 5 parts:** `→` a concrete WHAT
  (e.g. validate `svc_reports` against SQL01), `why` a tradecraft rationale,
  `opsec` INFO/WARN/DANGER with a reason, `conf` a confidence that names what it
  is NOT sure about, `refs` grounding.
- **[Slice 1] Checkpoint C — references are real:** `refs` cite your `type`
  output and/or a KB chunk (e.g. `vault/Techniques/...#chunk_n`) — not invented.
- **[Slice 1] Checkpoint D — advisory only:** the card says advisory / nothing
  dispatched; NO command was sent to the beacon (read-only by construction).

## Run — step 2: follow the hint
```
shell dir  "C:\Users\Public\RR-Smoke\exports"
shell type "C:\Users\Public\RR-Smoke\exports\svc_sql_readme.txt"
```

- **[Slice 1] Checkpoint E — a second recommendation** appears for the `svc_sql`
  finding, grounded in the new output (the assistant reacted again on new state).
- **[Slice 1] Checkpoint F — signal-not-noise:** re-running the SAME `type`
  (no new state) does NOT produce a duplicate card (dedup/novelty/cooldown).

## Degraded / never-crash
- **[Slice 1] Checkpoint G:** restart the daemon with `ANTHROPIC_API_KEY` unset →
  the daemon still boots, the TUI still works, NO advisor cards appear (silent),
  nothing crashes.

## Explicitly out of scope (do NOT expect — later slices)
- **[later slice]** A *proactive* card on a lull / phase boundary with no command.
- **[later slice]** Asking the assistant a question in a Chat pane ("what's my
  path to DA?") and getting a deep answer (conversational mode).
- **[later slice]** Pushing back ("too loud") and it capturing a lesson (learning loop).
- **[later slice]** The assistant writing `current_phase` / objective status, or a
  propose-and-confirm affordance (interpretation-write).
- **[later slice]** `search_journal` / `get_lessons` tools, or the deep Sonnet sub-agent.

## Teardown
`powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup`
````

- [ ] **Step 4: Commit the fixture + runbook**

```bash
git add scripts/smoke/rr-assistant-smoke-fixture.ps1 scripts/smoke/rr-assistant-smoke-runbook.md
git commit -m "test(assistant): operator-smoke fixture + runbook (rename + expand)"
```

- [ ] **Step 5: Run the operator smoke (acceptance — manual, live beacon)**

Invoke the **smoke-test** skill and walk the runbook against a live CS beacon.
Acceptance = all `[Slice 1]` checkpoints (A–G) pass. This is the ONLY acceptance
for the live surface; CI cannot exercise it. (No commit — this is a verification
gate, not a code change.)

---

## Remaining slices (this planning pass — separate plan docs)

The design is fully locked (spec §6 all-closed); only the per-slice PLAN docs
remain — **no re-brainstorming**. Each plugs into the seams this slice proved
(register a tool / add a trigger / add an output handler) without re-architecture:

1. **Operational Journal + its tool** — index the existing `events.jsonl` (join
   `beacon.command_input` + `beacon.command_output` by `correlation_id`, Voyage
   index) + a `search_journal` PULL tool appended to `build_tool_registry` (spec §6.B).
2. **Proactive mode** — a debounced background check (lull / material state-change)
   through the SAME `TriggerGate`, rate-limited; new tunable `Settings` (spec §6.A).
3. **Conversational mode + learning loop** — the TUI Chat pane +
   `operator.question`/`operator.answer`; correction → a context-scoped `Lesson`
   (engagement vs operator scope) + a `get_lessons` tool that closes the
   L1-written-but-never-read gap (spec §6.A, §6.D).
4. **Interpretation-write + propose-and-confirm** — publish `engagement.milestone`
   (the reducer merge-case already exists) for soft auto-writes; async non-blocking
   binary confirm for close/commit changes (spec §6.C).

Cross-cutting deferred (documented decisions, not open questions): the two-speeds
Sonnet sub-agent (spec §6.A), LLM-boundary secret redaction (spec §6.E), and
`#advisor-dock` visual polish + dismiss/ask/ack affordances (spec §6.C, §10).

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-07-16-ai-assistant-slice-1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration (REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`; fresh subagent per task + a spec-and-quality review gate per task + a whole-branch review at the end, per `development-workflow.md` §4).

**2. Inline Execution** — execute tasks in this session with checkpoints (REQUIRED SUB-SKILL: `superpowers:executing-plans`).

Recommended order is Task 0 → 11 as written (Task 0 lands first as its own reviewable change; Tasks 1→10 are the CI-tested feature; Task 11 is the live-surface operator smoke).
