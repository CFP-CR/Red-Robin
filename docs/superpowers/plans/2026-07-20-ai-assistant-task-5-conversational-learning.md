# Conversational mode + learning loop (backend) — Task 5 (#26) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the **conversational** trigger mode (operator-initiated ask/correct) and the **learning loop** — the CI-testable BACKEND: consume `operator.question` → run an answer cycle → publish `operator.answer`, and capture an operator *correction* as a **context-scoped `Lesson`** (engagement vs operator scope) that the assistant **reads back** via a new `get_lessons` tool (closing the L1-written-but-never-read gap).

**Scope (operator decision 2026-07-20 — SPLIT):** this plan is the **backend only** (all deterministic / CI-tested). The **TUI Chat pane + bidirectional TUI→daemon WS** (the operator input surface) is a **separate follow-up** — it is live-surface / smoke-only, needs new WS plumbing, and CI cannot exercise it. `operator.question` is fed by tests/CLI here, exactly as `advisor.recommendation` predated the dock. The deep Sonnet sub-agent (two-speeds) is **deferred** (spec §6.F lists "two-speeds / subagent" as a later slice) — conversational uses the same Haiku fast path.

**Architecture:** The `operator.question`/`operator.answer` domain events already exist (`domain/events.py:101-112`); nothing produces/handles them yet. The `Assistant` gains an `operator.question` handler that runs a NEW **answer cycle** (a Q&A system prompt → free-text answer + citations + an OPTIONAL context-scoped lesson when the message is a correction), publishes `operator.answer`, and stores any captured lesson. Lessons persist in the existing L1 SQLite (`LongtermRepo`) in a NEW `operator_lessons` table (scope-tagged); read-back is a new `get_lessons` PULL tool available to ALL cycles (reactive/proactive/conversational), so the assistant applies past corrections everywhere. No re-architecture — same bus, same registry seam, same repo.

**Tech Stack:** Python 3.11+, `sqlite3` (via the existing `LongtermRepo`), native Anthropic Tool Runner (via the Task-2 `AgentLLM` seam), `pytest` + `pytest-asyncio`, `uv`, `ruff`, `mypy --strict` (domain+application), `import-linter`.

## Global Constraints

_Verbatim from the spec (§6.A/§6.D/§6.E), `CLAUDE.md`, `CONTRIBUTING.md`, issue #26._

- **The 5 CI gates must pass** (`uv run --no-sync ...` if a bare `uv run` fails on a venv lock; `REDROBIN_ALLOW_DEGRADED=1` for boot-gated tests): `ruff check .` · `ruff format --check .` · `mypy redrobin/domain redrobin/application` · `lint-imports --config importlinter.cfg` · `pytest tests/ -v --tb=short`.
- **ruff default rules incl. E402** — all imports at the very top of every file.
- **TDD (red→green)**; **atomic conventional commits, NO AI co-author trailer**.
- **Every lesson is context-scoped — NEVER blanket** (spec §6.D, issue #26 out-of-scope): a lesson carries *(what-to-do-differently + the context it applies in + the why + provenance/date)* and a `scope` tag. A single permanent bag is explicitly rejected.
- **Scope tags** (spec §6.D): `engagement` = quarantined, read back ONLY for its own engagement (prevents cross-engagement contamination); `operator` = persists cross-engagement (cumulative). Inferred at capture by the model, operator-adjustable later. Read-back: a new engagement loads only `operator`-scoped lessons + its OWN `engagement`-scoped ones.
- **Never-crash + degraded-None** (spec §6.E): no `ANTHROPIC_API_KEY` → the conversational path is silent, daemon boots; an exception in a Q/A or capture cycle is logged and swallowed, the daemon survives (mirror the reactive `try/except`); the parser NEVER raises; a missing lesson store → capture/read-back degrade gracefully.
- **Observability** (spec §6.E): `assistant:`-prefixed logs (answer published, lesson captured with its scope); the answer is the `operator.answer` bus event.
- **Plug into the seams, do NOT re-architect** (§6.F): reuse the `operator.question`/`operator.answer` events; `get_lessons` is APPENDED to `build_tool_registry` (the `list[AgentTool]` output contract is unchanged); reuse `app.state.longterm_repo`. No new gate, no new scheduler.
- **mypy --strict** on `redrobin/domain` + `redrobin/application`. To keep the assistant free of a direct infra import, the store is reached via a narrow `LessonStore` **Protocol** (mirrors the `AgentLLM` Protocol seam) that `LongtermRepo` duck-satisfies.

## Design Decisions (locked in-plan from the spec — no re-brainstorm)

- **D1 — `Lesson` is a new pure type in `domain/memory.py`** (alongside `PromotedLesson`), NOT a reuse of the Slice-12-removed free-form `Lesson`. Fields: `scope` (`"engagement"|"operator"`), `context`, `lesson` (what-to-do-differently), `why`, `engagement_id` (provenance + the quarantine key — system-attached for BOTH scopes; it only *filters* engagement-scoped rows), `created_at` (provenance/date), `id`. It is distinct from `PromotedLesson` (the verdict-aggregation L1 record keyed by command/verdict) — a different lesson-source, per §10's "extend L1 as a new lesson-source." **§4-divergence (acknowledged):** spec §4's tree named `domain/assistant.py`; placing `Lesson` in `domain/memory.py` beside the other L1 record is a deliberate choice within §4's stated latitude ("the target LAYOUT … NOT the exhaustive change manifest") and co-locates the two L1 lesson records.
- **D2 — Storage = a NEW `operator_lessons` table in the existing L1 `LongtermRepo`** (the §10 "L1-extension" option, chosen over a brand-new repo). Reuses the shared `longterm.db`; the `engagement_id` column + a scope filter on read-back implement the quarantine ("engagement dies with the op" = it is never read back outside its engagement; no physical deletion needed). New methods `add_operator_lesson` / `list_operator_lessons(*, engagement_id)` — named to avoid clashing with the existing `list_lessons()` (PromotedLesson).
- **D3 — The assistant reaches the store via a `LessonStore` Protocol** (`add_operator_lesson`, `list_operator_lessons`), defined in `application/agents/assistant/lessons.py`; `LongtermRepo` duck-satisfies it. Keeps the assistant infra-free (mirrors `AgentLLM`) and mypy-strict-clean, per the "one ABC only, else duck typing" rule.
- **D4 — Unified ask+correct in ONE answer cycle.** The conversational cycle's LLM returns `{answer, citations[], lesson}` — `lesson` is `null` for a plain question, or a `{scope, context, lesson, why}` object when the operator MESSAGE was a correction (the model classifies + extracts). The handler always publishes `operator.answer`; if a lesson is present it is captured (provenance system-attached, NOT model-supplied — the model never sets `engagement_id`/`created_at`, mirroring how the enricher system-attaches `source_beacon_id`). This unifies the pull-query and the feedback/learning loop in one mode (§6.A).
- **D5 — `get_lessons` is available to ALL cycles**, appended to `build_tool_registry`. The reactive/proactive `run_cycle` already takes the `tools` list, so adding `get_lessons` to the registry makes the assistant its first reader everywhere — no `run_cycle` change. **Scope of the "L1 gap" closed:** `get_lessons` reads the NEW `operator_lessons` (the §6.D operator-correction learning loop) — that read-back is now closed. Making the assistant the first reader of the EXISTING verdict-aggregation `PromotedLesson` rows (the §3 sense of "first reader") is a SEPARATE, still-open item — `get_lessons` does not touch `list_lessons()`/`promoted_lessons`.
- **D6 — Acceptance = deterministic, NO operator smoke in Task 5.** Lesson scoping, storage, read-back, the answer parser, and the capture are all deterministic → the 5 CI gates + the new tests ARE the acceptance. Task 5 adds **no live surface** (the Chat pane is the deferred follow-up). The live-surface smoke (asking in the Chat pane, seeing a lesson applied) belongs to the follow-up's own plan.
- **D7 — `question_id` linkage.** `operator.answer.question_id` echoes `payload.get("question_id")` from the `operator.question` bus event, falling back to `""`. Task 5 tests publish `operator.question` **directly to the bus** (bypassing wire validation), so `question_id` flows through. **Constraint the follow-up must resolve:** the wire `operator.question` schema is `{text, context_refs}` with `additionalProperties:false` — it has **no `question_id`**, so a Chat-pane producer emitting through `POST /v1/events` would have `question_id` REJECTED. The follow-up must EITHER add `question_id` to the `operator.question` schema, OR carry the ref via the existing `context_refs[]`, OR publish to the bus directly. Not a Task 5 blocker (backend fed by tests/CLI), but flagged so the follow-up makes a real schema decision, not just "supply the id."

## File Structure

**Create:**
- `redrobin/application/agents/assistant/lessons.py` — the `LessonStore` Protocol + `capture_lesson(...)` (validate a parsed lesson dict → a `Lesson` with system-attached provenance → store).
- `tests/unit/test_operator_lessons.py` — the `Lesson` type + `LongtermRepo` scoping (engagement quarantine, operator persist, read-back).
- `tests/unit/agents/assistant/test_lessons.py` — `capture_lesson` + `get_lessons` tool.
- `tests/unit/agents/assistant/test_conversational.py` — the answer cycle + parser + the `Assistant.operator.question` handler.

**Modify:**
- `redrobin/domain/memory.py` — the `Lesson` dataclass.
- `redrobin/infrastructure/persistence/longterm_repo.py` — the `operator_lessons` table + `add_operator_lesson` / `list_operator_lessons`.
- `redrobin/application/agents/assistant/parser.py` — `ParsedAnswer` + `parse_answer` (never-raises).
- `redrobin/application/agents/assistant/agent.py` — the answer system prompt + `build_answer_context` + `run_answer_cycle`.
- `redrobin/application/agents/assistant/tools.py` — `get_lessons` appended to `build_tool_registry` (additive `lesson_store` + `engagement_provider` params).
- `redrobin/application/agents/assistant/assistant.py` — the `operator.question` handler + capture wiring (`lesson_store`, `engagement_provider` on `__init__`).
- `redrobin/api/lifespan_helpers.py` — pass `app.state.longterm_repo` + the engagement provider into `Assistant(...)` and `build_tool_registry(...)`.

**Untouched (verify):** `triggers.py`, `journal_service.py`, the TUI (the Chat pane is the follow-up), the reducer, the `.cna`, `config.py` (no new Settings — reuses the L1 repo + engagement).

---

### Task 5.1: `Lesson` domain type + `operator_lessons` storage

The foundation: a pure context-scoped `Lesson` and the scope-quarantining store.

**Files:**
- Modify: `redrobin/domain/memory.py`, `redrobin/infrastructure/persistence/longterm_repo.py`
- Test: create `tests/unit/test_operator_lessons.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Lesson: scope: str; context: str; lesson: str; why: str; engagement_id: str | None; created_at: datetime; id: int | None = None`
  - `LongtermRepo.add_operator_lesson(self, lesson: Lesson) -> Lesson`
  - `LongtermRepo.list_operator_lessons(self, *, engagement_id: str | None) -> list[Lesson]` (all `operator`-scoped + `engagement`-scoped WHERE `engagement_id` matches)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_operator_lessons.py
from __future__ import annotations

from datetime import datetime

from redrobin.domain.memory import Lesson
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


def _lesson(scope: str, engagement_id: str | None, lesson: str = "prefer comsvcs") -> Lesson:
    return Lesson(
        scope=scope,
        context="EDR observed (CrowdStrike)",
        lesson=lesson,
        why="operator flagged mimikatz too loud",
        engagement_id=engagement_id,
        created_at=datetime(2026, 7, 20, 12, 0, 0),
    )


def test_add_and_read_back_operator_scoped_persists_across_engagements(tmp_path):
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("operator", "eng-A"))
    # a DIFFERENT engagement still sees the operator-scoped lesson
    got = repo.list_operator_lessons(engagement_id="eng-B")
    assert len(got) == 1
    assert got[0].scope == "operator"
    assert got[0].lesson == "prefer comsvcs"
    assert got[0].id is not None


def test_engagement_scoped_is_quarantined_to_its_own_engagement(tmp_path):
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("engagement", "eng-A", lesson="A-only"))
    # its OWN engagement sees it
    assert [l.lesson for l in repo.list_operator_lessons(engagement_id="eng-A")] == ["A-only"]
    # a different engagement does NOT (dies with the op)
    assert repo.list_operator_lessons(engagement_id="eng-B") == []


def test_read_back_mixes_operator_and_own_engagement(tmp_path):
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("operator", "eng-A", lesson="op-wide"))
    repo.add_operator_lesson(_lesson("engagement", "eng-A", lesson="A-only"))
    repo.add_operator_lesson(_lesson("engagement", "eng-B", lesson="B-only"))
    got = {l.lesson for l in repo.list_operator_lessons(engagement_id="eng-A")}
    assert got == {"op-wide", "A-only"}  # NOT B-only


def test_operator_lessons_table_is_separate_from_promoted_lessons(tmp_path):
    # adding an operator lesson does not disturb the PromotedLesson API
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("operator", None))
    assert repo.list_lessons() == []  # PromotedLesson table still empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/test_operator_lessons.py -v`
Expected: FAIL — `ImportError: cannot import name 'Lesson'` / no `add_operator_lesson`.

- [ ] **Step 3: Write the implementation**

In `redrobin/domain/memory.py`, add after `PromotedLesson`:

```python
@dataclass(frozen=True)
class Lesson:
    """L1 operator-correction lesson (spec §6.D) — a context-scoped, NEVER-blanket
    record captured when the operator corrects the assistant. Distinct from
    ``PromotedLesson`` (the verdict-aggregation record keyed by command/verdict):
    this is the operator-correction lesson-source (§10). ``scope`` is
    ``"operator"`` (persists cross-engagement — cumulative) or ``"engagement"``
    (quarantined — read back only for its own engagement, so a honeypot fact never
    contaminates the next client). Provenance (``engagement_id``, ``created_at``)
    is system-attached at capture, never model-supplied."""

    scope: str  # "operator" | "engagement"
    context: str  # the situation it applies in
    lesson: str  # what to do differently
    why: str  # the reason
    engagement_id: str | None  # provenance + the quarantine key
    created_at: datetime
    id: int | None = None  # None until persisted
```

In `redrobin/infrastructure/persistence/longterm_repo.py`, extend `_SCHEMA_SQL` (append to the string, before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS operator_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope VARCHAR(16) NOT NULL,
    context TEXT NOT NULL,
    lesson TEXT NOT NULL,
    why TEXT NOT NULL,
    engagement_id VARCHAR(128),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_operator_lessons_scope ON operator_lessons (scope);
```

Add the import at the top: `from redrobin.domain.memory import Lesson, PromotedLesson` (extend the existing PromotedLesson import). Then add the methods to `LongtermRepo`:

```python
    def add_operator_lesson(self, lesson: Lesson) -> Lesson:
        """Insert a context-scoped operator-correction lesson (append-only; each
        correction is its own record — no dedup, unlike promoted lessons)."""
        ts = lesson.created_at or datetime.now()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO operator_lessons (scope, context, lesson, why, "
                "engagement_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (lesson.scope, lesson.context, lesson.lesson, lesson.why,
                 lesson.engagement_id, _iso(ts)),
            )
            return Lesson(
                scope=lesson.scope,
                context=lesson.context,
                lesson=lesson.lesson,
                why=lesson.why,
                engagement_id=lesson.engagement_id,
                created_at=ts,
                id=cursor.lastrowid,
            )

    def list_operator_lessons(self, *, engagement_id: str | None) -> list[Lesson]:
        """Read back the lessons applicable to ``engagement_id``: every
        ``operator``-scoped lesson (cross-engagement) plus the ``engagement``-scoped
        ones for THIS engagement only (quarantine — spec §6.D)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, scope, context, lesson, why, engagement_id, created_at "
                "FROM operator_lessons "
                "WHERE scope = 'operator' OR (scope = 'engagement' AND engagement_id = ?) "
                "ORDER BY id",
                (engagement_id,),
            ).fetchall()
        return [
            Lesson(
                scope=row["scope"],
                context=row["context"],
                lesson=row["lesson"],
                why=row["why"],
                engagement_id=row["engagement_id"],
                created_at=_parse_iso(row["created_at"]),
                id=row["id"],
            )
            for row in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/test_operator_lessons.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add redrobin/domain/memory.py redrobin/infrastructure/persistence/longterm_repo.py tests/unit/test_operator_lessons.py
git commit -m "feat(lessons): context-scoped Lesson type + scope-quarantining operator_lessons store"
```

---

### Task 5.2: `get_lessons` tool + `LessonStore` Protocol

The read-back that closes the L1-written-but-never-read gap, available to all cycles.

**Files:**
- Create: `redrobin/application/agents/assistant/lessons.py` (the Protocol; `capture_lesson` lands in 5.4)
- Modify: `redrobin/application/agents/assistant/tools.py`
- Test: create `tests/unit/agents/assistant/test_lessons.py`

**Interfaces:**
- Consumes: `LongtermRepo.list_operator_lessons` (5.1) — duck-satisfies the Protocol.
- Produces:
  - `class LessonStore(Protocol)` — `add_operator_lesson(self, lesson: Lesson) -> Lesson`; `list_operator_lessons(self, *, engagement_id: str | None) -> list[Lesson]`.
  - `build_tool_registry(kb, journal=None, lesson_store=None, engagement_provider=None) -> list[AgentTool]` (now appends `get_lessons`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/agents/assistant/test_lessons.py
from __future__ import annotations

from datetime import datetime

import pytest

from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.domain.memory import Lesson
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


def _store(tmp_path) -> LongtermRepo:
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(
        Lesson(scope="operator", context="EDR observed", lesson="prefer comsvcs",
               why="mimikatz too loud", engagement_id="eng-A", created_at=datetime(2026, 7, 20))
    )
    repo.add_operator_lesson(
        Lesson(scope="engagement", context="honeypot suspected", lesson="avoid DC01",
               why="looks like a decoy", engagement_id="eng-OTHER", created_at=datetime(2026, 7, 20))
    )
    return repo


def test_registry_appends_get_lessons(tmp_path):
    tools = build_tool_registry(None, None, lesson_store=_store(tmp_path), engagement_provider=lambda: "eng-A")
    assert [t.name for t in tools] == ["kb_retrieve", "search_journal", "get_lessons"]


@pytest.mark.asyncio
async def test_get_lessons_returns_operator_plus_own_engagement(tmp_path):
    tools = build_tool_registry(None, None, lesson_store=_store(tmp_path), engagement_provider=lambda: "eng-A")
    out = await tools[2].handler({})
    assert "prefer comsvcs" in out  # operator-scoped
    assert "avoid DC01" not in out  # engagement-scoped for a DIFFERENT engagement — quarantined


@pytest.mark.asyncio
async def test_get_lessons_degraded_without_store_is_graceful(tmp_path):
    tools = build_tool_registry(None, None, lesson_store=None)
    out = await tools[2].handler({})
    assert "no operator lessons" in out.lower() or "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_get_lessons_empty_is_graceful(tmp_path):
    empty = LongtermRepo(tmp_path / "longterm.db")
    tools = build_tool_registry(None, None, lesson_store=empty, engagement_provider=lambda: "eng-A")
    out = await tools[2].handler({})
    assert "no operator lessons" in out.lower()
```

Note: exactly TWO assertions pin the tool-name list (get_lessons is appended unconditionally → always 3 tools). Change both `== ["kb_retrieve", "search_journal"]` → `== ["kb_retrieve", "search_journal", "get_lessons"]`: `tests/unit/agents/assistant/test_tools.py:25` (`test_registry_exposes_kb_retrieve`) and `tests/unit/agents/assistant/test_tools_journal.py:30` (`test_registry_exposes_kb_and_journal_tools`). No other site asserts the list; index-based `tools[0]`/`tools[1]` and `"x" in seen_tools` callers are unaffected.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_lessons.py -v`
Expected: FAIL — the registry returns only `["kb_retrieve", "search_journal"]` / no `get_lessons`.

- [ ] **Step 3: Write the implementation**

Create `redrobin/application/agents/assistant/lessons.py`:

```python
"""Operator-correction lessons (spec §6.D) — the learning loop's application side.

``LessonStore`` is the narrow Protocol the assistant depends on (``LongtermRepo``
duck-satisfies it), so the assistant stays free of a direct infra import
(mirrors the ``AgentLLM`` Protocol). ``capture_lesson`` (Task 5.4) turns a parsed
correction into a stored ``Lesson`` with system-attached provenance."""

from __future__ import annotations

from typing import Protocol

from redrobin.domain.memory import Lesson


class LessonStore(Protocol):
    def add_operator_lesson(self, lesson: Lesson) -> Lesson: ...

    def list_operator_lessons(self, *, engagement_id: str | None) -> list[Lesson]: ...


__all__ = ["LessonStore"]
```

In `redrobin/application/agents/assistant/tools.py`, add at the top: `from collections.abc import Callable` (if not present) and `from redrobin.application.agents.assistant.lessons import LessonStore`. Add the schema + cap near the others:

```python
_MAX_LESSONS_CHARS = 6000

_GET_LESSONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "description": (
                "Optional focus, e.g. 'lsass dumping' — filters the operator's past "
                "corrections. Omit to load all lessons that apply to this engagement."
            ),
        }
    },
    "required": [],
    "additionalProperties": False,
}
```

Change `build_tool_registry` to take the two additive params and append the tool:

```python
def build_tool_registry(
    kb: KBService | None,
    journal: JournalService | None = None,
    lesson_store: LessonStore | None = None,
    engagement_provider: Callable[[], str | None] | None = None,
) -> list[AgentTool]:
    """The pull-tool set for one cycle: kb_retrieve + search_journal + get_lessons.
    Appending a tool is a pure append — the list[AgentTool] the agent loop consumes
    is the seam (spec §6.F). get_lessons closes the L1-written-but-never-read gap."""

    async def _kb_retrieve(args: dict[str, Any]) -> str:
        ...  # UNCHANGED

    async def _search_journal(args: dict[str, Any]) -> str:
        ...  # UNCHANGED

    async def _get_lessons(args: dict[str, Any]) -> str:
        if lesson_store is None:
            return "No operator lessons available."
        engagement_id = engagement_provider() if engagement_provider is not None else None
        try:
            lessons = lesson_store.list_operator_lessons(engagement_id=engagement_id)
        except Exception:
            logger.exception("assistant: get_lessons failed")
            return "Operator-lesson retrieval failed."
        topic = str(args.get("topic", "")).strip().lower()
        if topic:
            lessons = [
                le for le in lessons if topic in (le.context + " " + le.lesson + " " + le.why).lower()
            ]
        if not lessons:
            return "No operator lessons apply here yet."
        lines = [
            f"[{le.scope} · {le.created_at.date()}] WHEN {le.context}: {le.lesson} (why: {le.why})"
            for le in lessons
        ]
        return "\n".join(lines)[:_MAX_LESSONS_CHARS]

    return [
        AgentTool(name="kb_retrieve", description=(...), input_schema=_KB_RETRIEVE_SCHEMA, handler=_kb_retrieve),  # UNCHANGED
        AgentTool(name="search_journal", description=(...), input_schema=_SEARCH_JOURNAL_SCHEMA, handler=_search_journal),  # UNCHANGED
        AgentTool(
            name="get_lessons",
            description=(
                "Load the operator's past corrections that apply to THIS engagement (their "
                "preferences + this op's context-scoped lessons). ALWAYS call this before "
                "recommending or answering, so you honour what the operator already taught you "
                "and never re-suggest something they rejected — then cite the lesson you applied."
            ),
            input_schema=_GET_LESSONS_SCHEMA,
            handler=_get_lessons,
        ),
    ]
```

Update `__all__` if it lists tool builders (leave `build_kb_service`/`build_journal_service`/`build_tool_registry`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_lessons.py tests/unit/agents/assistant/test_tools.py tests/unit/agents/assistant/test_tools_journal.py -v`
Expected: PASS (new lesson-tool tests + the updated registry-name assertions).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/lessons.py redrobin/application/agents/assistant/tools.py tests/unit/agents/assistant/test_lessons.py tests/unit/agents/assistant/test_tools.py tests/unit/agents/assistant/test_tools_journal.py
git commit -m "feat(lessons): get_lessons read-back tool over the LessonStore (closes the L1-read gap)"
```

---

### Task 5.3: Conversational answer cycle + parser

The Q&A cycle: an answer system prompt + `run_answer_cycle` → `ParsedAnswer` (answer + citations + optional lesson).

**Files:**
- Modify: `redrobin/application/agents/assistant/parser.py`, `redrobin/application/agents/assistant/agent.py`
- Test: `tests/unit/agents/assistant/test_conversational.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class ParsedAnswer: answer: str; citations: list[str]; lesson: dict[str, str] | None`
  - `parse_answer(raw: str) -> ParsedAnswer | None` (never-raises)
  - `run_answer_cycle(*, llm, tools, wm_snapshot, l5_summary, question, max_tokens=2048, wm_max_items=...) -> ParsedAnswer | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/agents/assistant/test_conversational.py
from __future__ import annotations

import pytest

from redrobin.application.agents.assistant.agent import run_answer_cycle
from redrobin.application.agents.assistant.parser import ParsedAnswer, parse_answer


def test_parse_answer_question_no_lesson():
    raw = '{"answer": "Kerberoast svc_sql then crack offline.", "citations": ["wm:svc_sql"], "lesson": null}'
    pa = parse_answer(raw)
    assert pa is not None
    assert pa.answer.startswith("Kerberoast")
    assert pa.citations == ["wm:svc_sql"]
    assert pa.lesson is None


def test_parse_answer_correction_captures_lesson():
    raw = (
        '{"answer": "Understood — I will prefer comsvcs.", "citations": [],'
        ' "lesson": {"scope": "operator", "context": "EDR observed",'
        ' "lesson": "prefer comsvcs minidump", "why": "operator says mimikatz too loud"}}'
    )
    pa = parse_answer(raw)
    assert pa is not None and pa.lesson is not None
    assert pa.lesson["scope"] == "operator"
    assert pa.lesson["lesson"] == "prefer comsvcs minidump"


def test_parse_answer_never_raises_on_garbage():
    assert parse_answer("not json") is None
    assert parse_answer('{"citations": 5}') is None  # no answer text
    assert parse_answer("42") is None  # valid JSON but not a dict
    assert parse_answer("") is None


def test_parse_answer_drops_malformed_lesson():
    # a lesson missing required fields is dropped (answer still returned)
    raw = '{"answer": "ok", "citations": [], "lesson": {"scope": "operator"}}'
    assert parse_answer(raw).lesson is None
    # a lesson with an out-of-vocab scope is also dropped
    bad_scope = '{"answer": "ok", "citations": [], "lesson": {"scope": "global", "context": "c", "lesson": "l", "why": "w"}}'
    assert parse_answer(bad_scope).lesson is None


@pytest.mark.asyncio
async def test_run_answer_cycle_sends_question_and_parses():
    seen = {}

    class _LLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            seen["user_message"] = user_message
            return '{"answer": "path: kerberoast -> DA", "citations": ["kb:kerberoast"], "lesson": null}'

    pa = await run_answer_cycle(
        llm=_LLM(), tools=[], wm_snapshot={"hosts": []}, l5_summary="recon done",
        question="what's my path to DA?",
    )
    assert pa is not None and pa.answer == "path: kerberoast -> DA"
    assert "what's my path to DA?" in seen["user_message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_conversational.py -v`
Expected: FAIL — `cannot import name 'parse_answer'` / `run_answer_cycle`.

- [ ] **Step 3: Write the implementation**

In `redrobin/application/agents/assistant/parser.py`, add (imports at top: `from dataclasses import dataclass`):

```python
@dataclass(frozen=True)
class ParsedAnswer:
    answer: str
    citations: list[str]
    lesson: dict[str, str] | None  # {scope, context, lesson, why} when the message was a correction


_LESSON_KEYS = ("scope", "context", "lesson", "why")


def _parse_lesson(raw_lesson: Any) -> dict[str, str] | None:
    """A correction's captured lesson, or None. Drops a malformed/partial lesson
    (never raises). scope must be a known value; all four fields must be non-empty."""
    if not isinstance(raw_lesson, dict):
        return None
    out: dict[str, str] = {}
    for k in _LESSON_KEYS:
        v = str(raw_lesson.get(k) or "").strip()
        if not v:
            return None
        out[k] = v
    if out["scope"] not in ("operator", "engagement"):
        return None
    return out


def parse_answer(raw: str) -> ParsedAnswer | None:
    """Parse the conversational cycle's output into an answer (+ optional captured
    lesson). NEVER raises — a malformed answer means the assistant simply does not
    reply (degraded-None). An answer with no text is dropped."""
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("assistant: unparseable answer JSON — staying silent")
        return None
    if not isinstance(data, dict):
        return None
    answer = str(data.get("answer") or "").strip()
    if not answer:
        return None
    _cites = data.get("citations")
    cites_raw = _cites if isinstance(_cites, list) else []
    citations = [str(c) for c in cites_raw if isinstance(c, (str, int, float))]
    return ParsedAnswer(answer=answer, citations=citations, lesson=_parse_lesson(data.get("lesson")))
```

Add `ParsedAnswer` and `parse_answer` to `__all__`.

In `redrobin/application/agents/assistant/agent.py`, add the answer prompt + cycle (imports: `parse_answer`, `ParsedAnswer` from parser):

```python
_ANSWER_SYSTEM_PROMPT = (
    "You are Red Robin, an operation-aware AI copilot behind a red-team operator's C2. "
    "The operator is TALKING TO YOU. Answer their question grounded in the engagement state "
    "and the retrieval tools (kb_retrieve, search_journal, and ALWAYS get_lessons — honour what "
    "this operator already taught you). Reason over what the tools and state return, never from "
    "memory, never invent a fact. You are ADVISORY ONLY.\n\n"
    "If the operator's message is a CORRECTION of your advice (e.g. 'that's too loud', "
    "'don't do X here'), you must ALSO capture a context-scoped lesson so you never repeat it: "
    "scope 'operator' if it is a general preference about how THIS operator works (persists across "
    "engagements), or 'engagement' if it is specific to THIS operation's situation (dies with the op). "
    "NEVER a blanket rule — always tie it to the context and the why.\n\n"
    "Return ONLY a JSON object, no prose, no fences:\n"
    '{"answer": "your grounded answer (or an acknowledgement of the correction)",\n'
    ' "citations": ["the grounding you used — kb/journal/lesson sources + wm facts"],\n'
    ' "lesson": null OR {"scope": "operator|engagement", "context": "when it applies", '
    '"lesson": "what to do differently", "why": "the reason"}}'
)


def build_answer_context(
    *, wm_snapshot: dict[str, Any], l5_summary: str, question: str,
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
) -> str:
    capped = cap_wm_snapshot(wm_snapshot, wm_max_items)
    return "\n".join([
        "# Engagement working memory (L2)",
        json.dumps(capped, ensure_ascii=False, sort_keys=True),
        "",
        "# Story so far (L5)",
        l5_summary or "(no summary yet)",
        "",
        "# The operator says",
        question or "(empty)",
        "",
        "Answer them. Call get_lessons first. If this is a correction, capture the lesson.",
    ])


async def run_answer_cycle(
    *, llm: AgentLLM, tools: list[AgentTool], wm_snapshot: dict[str, Any], l5_summary: str,
    question: str, max_tokens: int = 2048, wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
) -> ParsedAnswer | None:
    """Run ONE conversational cycle: Q&A prompt -> Tool Runner loop -> ParsedAnswer
    (answer + citations + optional captured lesson), or None to stay silent."""
    raw = await llm.run(
        system=_ANSWER_SYSTEM_PROMPT,
        user_message=build_answer_context(
            wm_snapshot=wm_snapshot, l5_summary=l5_summary, question=question, wm_max_items=wm_max_items,
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    return parse_answer(raw)
```

Add `build_answer_context`, `run_answer_cycle` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_conversational.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/parser.py redrobin/application/agents/assistant/agent.py tests/unit/agents/assistant/test_conversational.py
git commit -m "feat(conversational): answer cycle + never-raises parse_answer (ask + capture-on-correct)"
```

---

### Task 5.4: `capture_lesson` + the `operator.question` handler + wiring

Tie it together: the Assistant consumes `operator.question`, answers, and captures a correction's lesson.

**Files:**
- Modify: `redrobin/application/agents/assistant/lessons.py`, `redrobin/application/agents/assistant/assistant.py`, `redrobin/api/lifespan_helpers.py`
- Test: append to `tests/unit/agents/assistant/test_conversational.py`; `tests/unit/agents/assistant/test_lessons.py`

**Interfaces:**
- Consumes: `parse_answer`/`run_answer_cycle` (5.3), `LessonStore` (5.2), `LongtermRepo.add_operator_lesson` (5.1).
- Produces:
  - `capture_lesson(parsed_lesson: dict[str, str], *, engagement_id: str | None, now: datetime, store: LessonStore | None) -> Lesson | None`
  - `Assistant.__init__` gains `lesson_store: LessonStore | None = None`, `engagement_provider: Callable[[], str | None] | None = None`; a new `operator.question` branch in `run()` → `_on_question`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/agents/assistant/test_lessons.py
from redrobin.application.agents.assistant.lessons import capture_lesson


def test_capture_lesson_system_attaches_provenance(tmp_path):
    store = LongtermRepo(tmp_path / "longterm.db")
    parsed = {"scope": "operator", "context": "EDR", "lesson": "prefer comsvcs", "why": "too loud"}
    le = capture_lesson(parsed, engagement_id="eng-A", now=datetime(2026, 7, 20), store=store)
    assert le is not None and le.id is not None
    assert le.engagement_id == "eng-A"  # provenance system-attached, NOT from the model
    assert store.list_operator_lessons(engagement_id="eng-A")[0].lesson == "prefer comsvcs"


def test_capture_lesson_none_store_is_graceful():
    parsed = {"scope": "operator", "context": "x", "lesson": "y", "why": "z"}
    assert capture_lesson(parsed, engagement_id="e", now=datetime(2026, 7, 20), store=None) is None
```

```python
# append to tests/unit/agents/assistant/test_conversational.py
import asyncio

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


class _AnswerLLM:
    def __init__(self, payload: str):
        self._payload = payload

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        return self._payload


def _assistant(bus, llm, store, engagement="eng-A"):
    return Assistant(
        bus=bus, llm=llm, tools=[], trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
        summary_reader=lambda: "recon done", enabled=True,
        lesson_store=store, engagement_provider=lambda: engagement,
    )


@pytest.mark.asyncio
async def test_operator_question_publishes_answer(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    llm = _AnswerLLM('{"answer": "kerberoast then crack", "citations": ["kb:kb"], "lesson": null}')
    a = _assistant(bus, llm, LongtermRepo(tmp_path / "lt.db"))
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.question", "payload": {"question_id": "q1", "text": "path to DA?"}})
    ans = None
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "operator.answer":
            ans = evt
    assert ans is not None
    assert ans["payload"]["question_id"] == "q1"
    assert ans["payload"]["text"] == "kerberoast then crack"
    assert ans["payload"]["citations"] == ["kb:kb"]
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_operator_correction_captures_lesson_and_answers(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    store = LongtermRepo(tmp_path / "lt.db")
    llm = _AnswerLLM(
        '{"answer": "understood", "citations": [], "lesson": {"scope": "operator",'
        ' "context": "EDR", "lesson": "prefer comsvcs", "why": "too loud"}}'
    )
    a = _assistant(bus, llm, store)
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.question", "payload": {"question_id": "q2", "text": "that's too loud"}})
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "operator.answer":
            break
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)
    lessons = store.list_operator_lessons(engagement_id="eng-A")
    assert len(lessons) == 1 and lessons[0].lesson == "prefer comsvcs"
    assert lessons[0].engagement_id == "eng-A"  # provenance attached from the provider, not the model


@pytest.mark.asyncio
async def test_operator_question_degraded_no_llm_is_silent(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, None, LongtermRepo(tmp_path / "lt.db"))
    a._llm = None
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.question", "payload": {"question_id": "q", "text": "hi"}})
    saw = False
    for _ in range(6):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.05)
        except TimeoutError:
            break
        if evt.get("type") == "operator.answer":
            saw = True
    assert not saw
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_operator_question_survives_cycle_exception(tmp_path):
    # A raising LLM inside the Q/A cycle is logged + swallowed by the run-loop's
    # try/except around _on_question; the loop survives (never-crash, spec §6.E) —
    # the conversational counterpart of test_assistant_survives_cycle_exception.
    class _BoomLLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            raise RuntimeError("llm exploded")

    bus = EventBus()
    await bus.subscribe()
    a = _assistant(bus, _BoomLLM(), LongtermRepo(tmp_path / "lt.db"))
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.question", "payload": {"question_id": "q", "text": "boom?"}})
    await asyncio.sleep(0.05)
    assert not task.done()  # the raising cycle was caught; the loop survives
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_conversational.py tests/unit/agents/assistant/test_lessons.py -v`
Expected: FAIL — no `capture_lesson`; `Assistant.__init__` has no `lesson_store`.

- [ ] **Step 3: Write the implementation**

In `redrobin/application/agents/assistant/lessons.py`, add (imports: `datetime`):

```python
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def capture_lesson(
    parsed_lesson: dict[str, str], *, engagement_id: str | None, now: datetime,
    store: LessonStore | None,
) -> Lesson | None:
    """Turn a validated correction-lesson dict (from parse_answer) into a stored
    Lesson. Provenance (engagement_id, created_at) is SYSTEM-attached here, never
    model-supplied. Degraded/never-crash: no store or a store error -> None."""
    if store is None:
        return None
    try:
        return store.add_operator_lesson(
            Lesson(
                scope=parsed_lesson["scope"],
                context=parsed_lesson["context"],
                lesson=parsed_lesson["lesson"],
                why=parsed_lesson["why"],
                engagement_id=engagement_id,  # provenance, system-attached for BOTH scopes
                created_at=now,
            )
        )
    except Exception:
        logger.exception("assistant: capture_lesson failed")
        return None
```

Note: `engagement_id` is **always** system-attached as provenance (D1: "provenance + the quarantine key"). It only *quarantines* `engagement`-scoped lessons — an `operator`-scoped row is returned by `list_operator_lessons` via the `scope = 'operator'` disjunct regardless of its `engagement_id`, so storing the origin engagement on it is harmless provenance and never leaks it. Add `capture_lesson` to `__all__`.

In `redrobin/application/agents/assistant/assistant.py`:
- Imports: `from datetime import datetime`; `from redrobin.application.agents.assistant.agent import ..., run_answer_cycle`; `from redrobin.application.agents.assistant.lessons import LessonStore, capture_lesson`.
- `__init__`: add `lesson_store: LessonStore | None = None`, `engagement_provider: Callable[[], str | None] | None = None`, store them.
- In `run()`, add a branch **before the `if etype != "enrichment.completed": continue` guard** (place it alongside the other `if etype == ...` branches — if it lands after that guard the question is dropped). A question counts as operator activity, so reset the lull debounce (`_last_beat`) — otherwise a proactive "you've gone quiet" card could fire while the operator is mid-conversation (spec §6.A frames the lull as "operator went quiet"; asking questions is not quiet):

```python
                if etype == "operator.question":
                    self._last_beat = self._clock()  # a question is activity — defer the lull
                    try:
                        await self._on_question(evt.get("payload") or {})
                    except Exception:
                        logger.exception("assistant failed to process operator.question")
                    continue
```
- Add the handler:

```python
    async def _on_question(self, payload: dict[str, Any]) -> None:
        if self._llm is None or not self._enabled:
            return
        question = str(payload.get("text") or "").strip()
        question_id = str(payload.get("question_id") or payload.get("id") or "")
        if not question:
            return
        try:
            l5_summary = self._summary_reader()
        except Exception:
            logger.exception("assistant: summary_reader failed; proceeding with empty L5")
            l5_summary = ""
        parsed = await run_answer_cycle(
            llm=self._llm, tools=self._tools, wm_snapshot=self._wm_snapshot,
            l5_summary=l5_summary, question=question, max_tokens=self._max_tokens,
            wm_max_items=self._wm_max_items,
        )
        if parsed is None:
            return  # degraded / unparseable -> no reply
        engagement_id = self._engagement_provider() if self._engagement_provider is not None else None
        if parsed.lesson is not None:
            le = capture_lesson(
                parsed.lesson, engagement_id=engagement_id, now=datetime.now(), store=self._lesson_store,
            )
            if le is not None:
                logger.info("assistant: captured %s-scoped lesson: %s", le.scope, le.lesson)
        await self._bus.publish({
            "type": "operator.answer",
            "payload": {"question_id": question_id, "text": parsed.answer, "citations": parsed.citations},
        })
        logger.info("assistant: published operator.answer question_id=%s", question_id)
```

In `redrobin/api/lifespan_helpers.py` `start_assistant`, thread the store + engagement provider (reuse `app.state.longterm_repo` and the existing engagement resolution — the same source `_read_summary` uses):

Reuse the EXISTING dynamic engagement provider `_engagement_id_provider` that `start_assistant` already defines (the "M3" provider — `_read_summary` uses it; do NOT invent a new `_current_engagement`), and PRESERVE the existing `assistant_enabled` journal guard. Only the added lines are `lesson_store = ...` and the four new kwargs:

```python
    kb = build_kb_service()
    # PRESERVE this guard exactly (Task 4 whole-branch fix) — do NOT make it unconditional:
    journal = await build_journal_service() if settings.assistant_enabled else None
    lesson_store = getattr(app.state, "longterm_repo", None)  # set by start_working_memory, runs earlier
    assistant = Assistant(
        bus=app.state.event_bus,
        llm=build_agent_llm(model=settings.assistant_model),
        tools=build_tool_registry(
            kb, journal=journal, lesson_store=lesson_store, engagement_provider=_engagement_id_provider
        ),
        trigger=TriggerGate(cooldown_sec=settings.assistant_cooldown_sec),
        summary_reader=_read_summary,
        enabled=settings.assistant_enabled,
        max_tokens=settings.assistant_max_tokens,
        wm_max_items=settings.assistant_wm_max_items,
        lull_sec=settings.assistant_lull_sec,
        proactive_min_interval_sec=settings.assistant_proactive_min_interval_sec,
        lesson_store=lesson_store,
        engagement_provider=_engagement_id_provider,
    )
```

_Implementer: `_engagement_id_provider` returns `str` (satisfies the `Callable[[], str | None]` param); it reads `app.state.engagement_id` — the SAME source `_read_summary` uses, so capture and read-back share the quarantine key. Everything else in `start_assistant` (on_started, create_task, app.state, the close hook) is unchanged — the snippet shows only the edited lines._

- [ ] **Step 4: Run tests + the reactive/proactive suites to verify green**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/ tests/integration/test_assistant.py tests/integration/test_assistant_lifespan.py -v`
Expected: PASS (conversational + lessons + the existing reactive/proactive tests unregressed).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/lessons.py redrobin/application/agents/assistant/assistant.py redrobin/api/lifespan_helpers.py tests/unit/agents/assistant/test_conversational.py tests/unit/agents/assistant/test_lessons.py
git commit -m "feat(conversational): operator.question handler -> answer + capture correction lessons"
```

- [ ] **Step 6: Run the 5 CI gates (Task-5 acceptance)**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q --tb=short
```

Expected: all green; import-linter 4 KEPT; pytest = prior baseline + the new tests, pristine.

---

## Acceptance / Definition of Done (Task 5 / #26)

- [ ] **5 CI gates green.**
- [ ] **TDD red→green** for: Lesson scoping + storage (5.1); get_lessons read-back (5.2); the answer parser + cycle (5.3); capture + the operator.question handler (5.4).
- [ ] **A correction produces a context-scoped (never blanket) lesson with provenance**; **engagement-scoped dies with the op** (quarantined on read-back), **operator-scoped persists**; a new engagement loads only operator-scoped + its own; **get_lessons reads them back** (closing the L1-written-but-never-read gap).
- [ ] **Never-crash + degraded-None** — no key → conversational silent; parser never raises; no store → capture/read-back graceful; an exception in Q/A or capture is logged, daemon survives.
- [ ] **Observability** — `operator.answer` event + `assistant:` logs (answer published, lesson captured w/ scope).
- [ ] **Plugs into the seams** — reuses `operator.question`/`operator.answer`; `get_lessons` appended (list[AgentTool] contract unchanged); reuses `app.state.longterm_repo`; no re-architecture.
- [ ] **Acceptance form (D6)** — deterministic → CI gates + tests ARE the acceptance. **NO operator smoke in Task 5** (no live surface — the Chat pane is the deferred follow-up).
- [ ] **Atomic conventional commits, no AI co-author trailer.**
- [ ] **Pillars** — ① a new mode + one tool through existing channels/repo, no new architecture; ② the engagement/operator scope tag avoids both contamination and amnesia; ③ the assistant learns the operator's reasoning, read-back is auditable/editable.

## Documented limitations / deferred (in-scope, by design)

- **The TUI Chat pane + bidirectional WS is the follow-up** (operator input surface; live-surface/smoke-only). Until it lands, `operator.question` is produced by tests/CLI. **The conversational mode is not operator-usable in the TUI until the follow-up ships** — that follow-up carries the live-surface smoke.
- **Deep Sonnet sub-agent (two-speeds) deferred** (spec §6.F) — conversational uses the Haiku fast path.
- **Lessons are operator-adjustable "later"** — Task 5 stores/reads them; an edit/delete UI is not in scope (auditable via the DB now).
- **`question_id` linkage (D7)** — echoed from the question payload; the Chat-pane producer supplies real ids.
- **`get_lessons` filter is a simple substring** (`topic`) — semantic retrieval over lessons is unnecessary at this scale (the operator-scoped set is small by design, §6.D).
- **`get_lessons` read-back is unit-tested directly, not end-to-end through a cycle** — the handler tests pass `tools=[]` (you can't force a fake LLM to *call* a tool), so D5's "the assistant applies lessons everywhere" is proven by the tool test + the registry wiring, not by a full reactive/conversational cycle invoking it. The live application is covered by the Chat-pane follow-up's smoke.
- **Orphan engagement-scoped lesson (fails safe):** if the model tags a lesson `engagement` while no engagement is active, `engagement_id` is `None`; read-back's `engagement_id = ?` never matches `NULL` (SQL `NULL = NULL` is unknown), so that row is unreadable by any engagement — lost, NOT leaked (consistent with "dies with the op"). Conversational mode presupposes an active engagement, so this is an unlikely edge.
- **Spurious capture is bounded, not eliminated:** a well-formed but hallucinated lesson on a plain question would be stored (the parser only drops *malformed* lessons). On-spec (§6.D: scope "inferred at capture … operator-adjustable"); the edit/delete affordance is deferred, and lessons are auditable via the DB. Monitor in the follow-up smoke.

## Self-Review (author checklist — completed)

1. **Spec coverage:** §6.A conversational ask/correct → 5.3/5.4; correcting-is-learning unified in one cycle → D4; §6.D lesson shape (what/context/why/provenance) → D1/5.1; scope engagement-vs-operator + quarantine → 5.1; read-back closes L1 gap via get_lessons → 5.2/D5; §10 storage L1-extension → D2; §6.E never-crash/degraded → throughout; §8 acceptance → D6. Deferred per §6.F: Chat pane (split), Sonnet sub-agent. No gaps.
2. **Placeholder scan:** the `...  # UNCHANGED` markers in 5.2 point at existing kb_retrieve/search_journal code to preserve — not gaps. Every step has runnable code or an exact command.
3. **Type consistency:** `Lesson` fields ↔ the SQL columns ↔ `add/list_operator_lessons` ↔ `capture_lesson` ↔ `ParsedAnswer.lesson` dict keys (`scope/context/lesson/why`) all align; `build_tool_registry(kb, journal=None, lesson_store=None, engagement_provider=None)` is additive (existing 1-2 arg callers unchanged); `Assistant.__init__` new params defaulted (backward-compatible); `run_answer_cycle -> ParsedAnswer | None`.
