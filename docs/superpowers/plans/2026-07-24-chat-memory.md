# Assistant Conversational Memory + Chat Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the full assistant stream to an append-only per-engagement JSONL log, and feed a bounded recent slice of it back into the assistant's cycles so it follows the conversation thread.

**Architecture:** Four small units in a one-way flow — `bus → ChatRecorder → chat_history.jsonl → history reader → model context`. The domain owns the *selection rule* (two quota-ed windows), infrastructure owns the *file*, `application/chat/` orchestrates, and the assistant consumes it through the same injected-reader seam already used by `summary_reader` and `snapshot_reader`. No compaction: the two windows bound the context deterministically.

**Tech Stack:** Python 3.13, asyncio, pydantic-settings, pytest + pytest-asyncio, `uv` for builds.

## Global Constraints

Every task's requirements implicitly include this section. Values copied from the spec (`docs/superpowers/specs/2026-07-24-chat-memory-design.md`), `CLAUDE.md`, and `CONTRIBUTING.md`.

- **5 CI gates must pass** (use `uv run --no-sync` when the daemon is running — it locks the venv):
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy redrobin/domain redrobin/application`
  - `uv run lint-imports --config importlinter.cfg` (run it plainly — do NOT pipe stdout to `/dev/null` on Windows; rich's cp1252 encode raises a spurious `UnicodeEncodeError`)
  - `uv run pytest tests/ -v --tb=short`
- **Boot-gated tests need `REDROBIN_ALLOW_DEGRADED=1`** in the environment (anything that builds `create_app()` / hits the boot gate). Set it when running the full suite.
- **TDD**: failing test → verify red → implement → verify green → commit.
- **Atomic, conventional commits**; one logical change per commit. **NO AI co-author trailer** (no `Co-Authored-By: Claude`).
- **Four import-linter contracts** (from `importlinter.cfg`): layers are `api | tui` → `application` → `domain`; `domain` imports NOTHING from `infrastructure`/`application`/`api`/`tui`; `application` never imports `api`/`tui`; `api`/`tui` never import `infrastructure` directly. `application → infrastructure` IS allowed (`infrastructure` is not in the layers contract).
- **mypy strict** covers `redrobin/domain` + `redrobin/application` only. Every new/changed function in those two layers needs complete type annotations.
- **Never-crash idiom**: a failed append must not stop the recorder loop; a failing reader must not stop a cycle; a malformed line must not break a read; a missing file yields `[]`/`""`.
- **NEVER log a turn's `text`** (it can contain credentials the operator pasted). Log the path and the exception only.
- **Single-writer**: only `ChatRecorder` writes `chat_history.jsonl`.
- **The interpretation cycle must NOT receive chat history** — it writes engagement state; chat-driven writes are explicitly out.
- **Settings defaults**: `assistant_chat_dialogue_turns=10` (`REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS`), `assistant_chat_advisory_turns=5` (`REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS`).
- **Kind vocabulary** (exact strings): `question`, `answer`, `advisory`, `heads_up`, `confirm`. **Role vocabulary**: `operator`, `assistant`.
- Reuse the existing `is_own_proposal_question(payload)` from `redrobin/application/agents/assistant/interpret.py` for the confirm discriminator — do NOT re-implement the `context_refs`/`milestone:` check.

---

### Task 1: Domain — `ChatTurn` + the two-window selector

**Files:**
- Create: `redrobin/domain/chat.py`
- Test: `tests/unit/test_domain_chat.py`

**Interfaces:**
- Consumes: nothing (pure domain; `domain` may not import other layers).
- Produces:
  - `ChatTurn(ts: str, role: str, kind: str, text: str, correlation_id: str | None = None)` — frozen dataclass, field order exactly as listed.
  - `DIALOGUE_KINDS: frozenset[str]` = `{"question", "answer", "confirm"}`
  - `VOLUNTEERED_KINDS: frozenset[str]` = `{"advisory", "heads_up"}`
  - `select_context_turns(turns: list[ChatTurn], *, dialogue_max: int, volunteered_max: int) -> list[ChatTurn]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_domain_chat.py`:

```python
from __future__ import annotations

from redrobin.domain.chat import (
    DIALOGUE_KINDS,
    VOLUNTEERED_KINDS,
    ChatTurn,
    select_context_turns,
)


def _turn(n: int, kind: str) -> ChatTurn:
    # ts sorts lexicographically the same as chronologically (zero-padded)
    role = "operator" if kind == "question" else "assistant"
    return ChatTurn(ts=f"2026-07-24T05:00:{n:02d}.000000+00:00", role=role, kind=kind, text=f"t{n}")


def test_kind_groups_are_disjoint_and_complete():
    assert DIALOGUE_KINDS == frozenset({"question", "answer", "confirm"})
    assert VOLUNTEERED_KINDS == frozenset({"advisory", "heads_up"})
    assert not (DIALOGUE_KINDS & VOLUNTEERED_KINDS)


def test_each_group_gets_its_own_quota():
    turns = [_turn(1, "question"), _turn(2, "answer"), _turn(3, "advisory"), _turn(4, "heads_up")]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=10)
    assert [t.text for t in out] == ["t1", "t2", "t3", "t4"]


def test_advisory_burst_cannot_evict_the_dialogue_thread():
    # THE dilution guarantee (spec §6): 20 advisories after a question must NOT
    # push that question out of the selected window.
    turns = [_turn(1, "question"), _turn(2, "answer")]
    turns += [_turn(i, "advisory") for i in range(3, 23)]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=5)
    texts = [t.text for t in out]
    assert "t1" in texts and "t2" in texts  # dialogue survived
    assert len([t for t in out if t.kind == "advisory"]) == 5  # advisory quota honoured
    assert texts[:2] == ["t1", "t2"]  # and the merge is chronological


def test_windows_keep_the_most_recent_of_each_group():
    turns = [_turn(i, "question") for i in range(1, 6)] + [_turn(i, "advisory") for i in range(6, 11)]
    out = select_context_turns(turns, dialogue_max=2, volunteered_max=2)
    assert [t.text for t in out] == ["t4", "t5", "t9", "t10"]


def test_merge_is_chronological_across_groups():
    turns = [_turn(1, "advisory"), _turn(2, "question"), _turn(3, "advisory"), _turn(4, "answer")]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=10)
    assert [t.text for t in out] == ["t1", "t2", "t3", "t4"]


def test_fewer_turns_than_the_caps_returns_all():
    turns = [_turn(1, "question"), _turn(2, "advisory")]
    assert len(select_context_turns(turns, dialogue_max=10, volunteered_max=10)) == 2


def test_empty_input_returns_empty():
    assert select_context_turns([], dialogue_max=10, volunteered_max=5) == []


def test_unknown_kind_is_ignored():
    turns = [_turn(1, "question"), ChatTurn(ts="2026-07-24T05:00:02.000000+00:00", role="assistant", kind="telemetry", text="leak")]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=10)
    assert [t.text for t in out] == ["t1"]


def test_zero_cap_selects_nothing_from_that_group():
    # a 0 cap must select NOTHING from the group — a naive turns[-0:] would select ALL
    turns = [_turn(1, "question"), _turn(2, "advisory")]
    out = select_context_turns(turns, dialogue_max=0, volunteered_max=10)
    assert [t.text for t in out] == ["t2"]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=0)
    assert [t.text for t in out] == ["t1"]


def test_chat_turn_is_frozen_and_defaults_correlation_id():
    t = _turn(1, "question")
    assert t.correlation_id is None
    try:
        t.text = "mutated"  # type: ignore[misc]
    except Exception as exc:
        assert exc.__class__.__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("ChatTurn must be frozen")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_domain_chat.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'redrobin.domain.chat'`

- [ ] **Step 3: Write the implementation**

Create `redrobin/domain/chat.py`:

```python
"""redrobin/domain/chat.py — the session chat thread (pure domain).

A :class:`ChatTurn` is one entry in the operator<->assistant conversation. This is a
NEW memory layer, distinct from the canonical five: L2 holds engagement *state*, L5
summarises *beacon activity*, L1/L3 hold *durable lessons*, and the Operational
Journal is the verbatim *command* transcript. None of them records the dialogue.

The two kind groups exist so the context selector can give each its own quota —
beacon-driven advisories must never evict the dialogue thread (spec §6).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatTurn:
    """One recorded turn of the conversation.

    ``ts`` is an ISO-8601 UTC timestamp with microsecond precision (so ordering ties are
    practically impossible). Ordering is a lexicographic sort of that string, which is
    chronological ONLY while every ts carries the same UTC offset — guaranteed by the
    single-writer invariant (``ChatRecorder`` always stamps ``datetime.now(timezone.utc)``).
    ``role`` is ``"operator"`` or ``"assistant"``;
    ``kind`` is one of the five values in the two frozensets below.
    ``correlation_id`` carries the reactive trigger handle (``"<beacon>:<command>"``)
    for an advisory, and is None otherwise.
    """

    ts: str
    role: str
    kind: str
    text: str
    correlation_id: str | None = None


# The operator<->assistant exchange: the operator asks, the assistant answers, and a
# confirm asks for the operator's decision. This is the thread that must never be lost.
DIALOGUE_KINDS = frozenset({"question", "answer", "confirm"})

# What the assistant said unprompted: reactive advisories and proactive heads-ups.
VOLUNTEERED_KINDS = frozenset({"advisory", "heads_up"})


def select_context_turns(
    turns: list[ChatTurn], *, dialogue_max: int, volunteered_max: int
) -> list[ChatTurn]:
    """The two-window rule: the last ``dialogue_max`` dialogue turns AND the last
    ``volunteered_max`` volunteered turns, merged chronologically.

    A single "last N" window would be wrong here: advisories fire per beacon command,
    so the most recent N entries can be entirely advisories, evicting the operator's
    question — the very thing the history exists to carry. Each group therefore gets a
    guaranteed quota.

    Pure and non-mutating. ``turns`` is expected in file order (oldest first). A turn
    whose ``kind`` is in neither group is ignored, so a future kind cannot silently
    leak into the model's context. A cap of 0 selects nothing from that group. The
    sort is stable, so turns sharing a ``ts`` keep a deterministic order.
    """
    window: list[ChatTurn] = []
    if dialogue_max > 0:
        window += [t for t in turns if t.kind in DIALOGUE_KINDS][-dialogue_max:]
    if volunteered_max > 0:
        window += [t for t in turns if t.kind in VOLUNTEERED_KINDS][-volunteered_max:]
    return sorted(window, key=lambda t: t.ts)


__all__ = ["DIALOGUE_KINDS", "VOLUNTEERED_KINDS", "ChatTurn", "select_context_turns"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_domain_chat.py -q`
Expected: PASS — 10 passed.

- [ ] **Step 5: Run the type + lint gates**

Run: `uv run --no-sync mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .`
Expected: `All checks passed!` and `N files already formatted`

Run: `uv run --no-sync lint-imports --config importlinter.cfg`
Expected: `Contracts: 4 kept, 0 broken.`

- [ ] **Step 6: Commit**

```bash
git add redrobin/domain/chat.py tests/unit/test_domain_chat.py
git commit -m "feat(chat): ChatTurn domain type + two-window context selector"
```

---

### Task 2: `ChatHistoryRepo` — append-only JSONL store

**Files:**
- Create: `redrobin/infrastructure/persistence/chat_history_repo.py`
- Test: `tests/unit/test_chat_history_repo.py`

**Interfaces:**
- Consumes: `ChatTurn` from `redrobin.domain.chat` (Task 1).
- Produces:
  - `ChatHistoryRepo(path: Path | str)` with a **public** `self.path: Path` attribute (matching `WorkingMemoryRepo.path`).
  - `.exists() -> bool`
  - `.append(turn: ChatTurn) -> None`
  - `.tail(max_lines: int = _MAX_TAIL_LINES) -> list[ChatTurn]` — oldest first
  - module constant `_MAX_TAIL_LINES = 500`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_chat_history_repo.py`:

```python
from __future__ import annotations

import json

from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo


def _turn(n: int, kind: str = "question", **kw) -> ChatTurn:
    return ChatTurn(
        ts=f"2026-07-24T05:00:{n:02d}.000000+00:00",
        role="operator",
        kind=kind,
        text=f"t{n}",
        **kw,
    )


def test_append_then_tail_round_trips_in_order(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(_turn(1))
    repo.append(_turn(2, kind="advisory", correlation_id="B-7:whoami"))
    turns = repo.tail()
    assert [t.text for t in turns] == ["t1", "t2"]  # oldest first
    assert turns[1].kind == "advisory"
    assert turns[1].correlation_id == "B-7:whoami"
    assert turns[0].correlation_id is None


def test_append_creates_parent_dirs_and_is_one_json_per_line(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "nested" / "dir" / "chat_history.jsonl")
    repo.append(_turn(1))
    repo.append(_turn(2))
    lines = repo.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["text"] == "t1"


def test_missing_file_yields_empty_list_and_exists_false(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    assert repo.exists() is False
    assert repo.tail() == []


def test_malformed_lines_are_skipped_and_neighbours_still_parse(tmp_path):
    path = tmp_path / "chat_history.jsonl"
    good = json.dumps(
        {"ts": "2026-07-24T05:00:01.000000+00:00", "role": "operator", "kind": "question", "text": "keep me"}
    )
    missing_field = json.dumps({"ts": "x", "role": "operator", "kind": "question"})  # no text
    path.write_text(
        good + "\n" + '{"truncated": ' + "\n" + missing_field + "\n" + "\n" + good + "\n",
        encoding="utf-8",
    )
    repo = ChatHistoryRepo(path)
    turns = repo.tail()
    assert [t.text for t in turns] == ["keep me", "keep me"]  # 2 good, 3 skipped


def test_tail_honours_max_lines(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    for n in range(1, 11):
        repo.append(_turn(n))
    turns = repo.tail(max_lines=3)
    assert [t.text for t in turns] == ["t8", "t9", "t10"]  # the LAST 3, oldest first


def test_unicode_round_trips(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(ChatTurn(ts="2026-07-24T05:00:01.000000+00:00", role="operator", kind="question", text="¿cómo dumpeo creds? ✻"))
    assert repo.tail()[0].text == "¿cómo dumpeo creds? ✻"


def test_default_max_tail_lines_is_500():
    from redrobin.infrastructure.persistence.chat_history_repo import _MAX_TAIL_LINES

    assert _MAX_TAIL_LINES == 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_chat_history_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'redrobin.infrastructure.persistence.chat_history_repo'`

- [ ] **Step 3: Write the implementation**

Create `redrobin/infrastructure/persistence/chat_history_repo.py`:

```python
"""redrobin/infrastructure/persistence/chat_history_repo.py — the chat transcript store.

Append-only JSONL, one :class:`~redrobin.domain.chat.ChatTurn` per line, one file per
engagement (a sibling of ``events.jsonl``). Deliberately shaped like a LOG, not like the
document repos (``WorkingMemoryRepo``): a turn is appended once and never rewritten, so
there is no atomic whole-file replace.

Reads are bounded: ``tail`` streams the file through a ``deque`` so memory is
O(max_lines), never O(file). A malformed line — a truncated append, a missing field — is
skipped so one bad entry cannot break the read. Neither method raises on a missing file.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import asdict
from pathlib import Path

from redrobin.domain.chat import ChatTurn

logger = logging.getLogger(__name__)

# Memory safety valve for a pathologically long log. The two context windows the
# selector applies are far smaller, so this only bounds the read, never the selection.
_MAX_TAIL_LINES = 500


def _parse_line(raw: str) -> ChatTurn | None:
    """One JSONL line -> ChatTurn, or None when unusable. Never raises.

    NOTE: the line's content is never logged — a turn's text can contain credentials.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("chat_history: skipped a malformed line")
        return None
    if not isinstance(data, dict):
        return None
    try:
        correlation_id = data.get("correlation_id")
        return ChatTurn(
            ts=str(data["ts"]),
            role=str(data["role"]),
            kind=str(data["kind"]),
            text=str(data["text"]),
            correlation_id=str(correlation_id) if correlation_id is not None else None,
        )
    except KeyError:
        logger.debug("chat_history: skipped a line missing a required field")
        return None


class ChatHistoryRepo:
    """One engagement's chat transcript (append-only JSONL).

    Single-writer by contract: only ``ChatRecorder`` calls :meth:`append` — the same
    invariant the working memory holds with the reducer as its sole writer.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def append(self, turn: ChatTurn) -> None:
        """Append one turn as a single JSON line. Creates the parent dir if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(turn), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def tail(self, max_lines: int = _MAX_TAIL_LINES) -> list[ChatTurn]:
        """The last ``max_lines`` parseable turns, oldest first. Missing file -> []."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            raw_lines = deque(fh, maxlen=max_lines)
        turns: list[ChatTurn] = []
        for raw in raw_lines:
            turn = _parse_line(raw)
            if turn is not None:
                turns.append(turn)
        return turns


__all__ = ["ChatHistoryRepo"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_chat_history_repo.py -q`
Expected: PASS — 7 passed.

- [ ] **Step 5: Run the lint gates**

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .`
Expected: clean.

Run: `uv run --no-sync lint-imports --config importlinter.cfg`
Expected: `Contracts: 4 kept, 0 broken.`

- [ ] **Step 6: Commit**

```bash
git add redrobin/infrastructure/persistence/chat_history_repo.py tests/unit/test_chat_history_repo.py
git commit -m "feat(chat): append-only ChatHistoryRepo with bounded tail reads"
```

---

### Task 3: `ChatRecorder` — the bus consumer that writes the transcript

**Files:**
- Create: `redrobin/application/chat/__init__.py` (empty)
- Create: `redrobin/application/chat/recorder.py`
- Test: `tests/unit/test_chat_recorder.py`

**Interfaces:**
- Consumes: `ChatTurn` (Task 1); `ChatHistoryRepo` (Task 2); `EventBus` from `redrobin.application.event_bus` (`await bus.subscribe() -> asyncio.Queue`, `await queue.get()`, `await bus.unsubscribe(queue)`, `await bus.publish(evt)`); `is_own_proposal_question` from `redrobin.application.agents.assistant.interpret`.
- Produces:
  - `advisory_text(payload: dict[str, Any]) -> str`
  - `turn_for(event_type: str, payload: dict[str, Any], *, ts: str) -> ChatTurn | None`
  - `ChatRecorder(*, bus: EventBus, repo: ChatHistoryRepo, now: Callable[[], datetime] = _utc_now)` with `.on_started: Callable[[], None] | None` and `async .run() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_chat_recorder.py`:

```python
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pytest

from redrobin.application.chat.recorder import ChatRecorder, advisory_text, turn_for
from redrobin.application.event_bus import EventBus
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo

_TS = "2026-07-24T05:00:01.000000+00:00"


def test_operator_question_maps_to_a_question_turn():
    turn = turn_for("operator.question", {"text": "path to DA?", "source": "operator"}, ts=_TS)
    assert turn is not None
    assert (turn.role, turn.kind, turn.text) == ("operator", "question", "path to DA?")


def test_assistant_confirm_proposal_maps_to_a_confirm_turn():
    # the assistant's own proposal is discriminated by context_refs (is_own_proposal_question)
    turn = turn_for(
        "operator.question",
        {"text": "Mark obj-2 COMPLETE?", "context_refs": ["milestone:obj-2"]},
        ts=_TS,
    )
    assert turn is not None
    assert (turn.role, turn.kind) == ("assistant", "confirm")


def test_assistant_answer_maps_to_an_assistant_answer_turn():
    turn = turn_for("operator.answer", {"text": "Kerberoast svc_sql", "question_id": "opq-0"}, ts=_TS)
    assert turn is not None
    assert (turn.role, turn.kind) == ("assistant", "answer")


def test_operator_confirm_reply_maps_to_an_operator_answer_turn():
    turn = turn_for("operator.answer", {"text": "yes", "source": "operator"}, ts=_TS)
    assert turn is not None
    assert (turn.role, turn.kind, turn.text) == ("operator", "answer", "yes")


def test_reactive_recommendation_is_an_advisory_with_correlation_id():
    turn = turn_for(
        "advisor.recommendation",
        {
            "suggested_action": "Validate svc_reports",
            "rationale": "a quiet bind beats a loud dump",
            "opsec_risk": "WARN",
            "confidence": "high",
            "correlation_id": "B-7:type db_backup.config",
        },
        ts=_TS,
    )
    assert turn is not None
    assert turn.kind == "advisory"
    assert turn.correlation_id == "B-7:type db_backup.config"
    assert "Validate svc_reports" in turn.text and "WARN" in turn.text


def test_proactive_recommendation_is_a_heads_up_without_correlation_id():
    turn = turn_for(
        "advisor.recommendation",
        {"suggested_action": "Baseline the host defences", "rationale": "a lull", "correlation_id": None},
        ts=_TS,
    )
    assert turn is not None
    assert turn.kind == "heads_up"
    assert turn.correlation_id is None


def test_irrelevant_and_empty_events_are_not_recorded():
    assert turn_for("enrichment.completed", {"command": "whoami"}, ts=_TS) is None
    assert turn_for("opsec.verdict", {"verdict": "BLOCK"}, ts=_TS) is None
    assert turn_for("working_memory.deltas", {"hosts": ["h1"]}, ts=_TS) is None
    assert turn_for("operator.question", {"text": "", "source": "operator"}, ts=_TS) is None
    assert turn_for("operator.answer", {"text": ""}, ts=_TS) is None
    assert turn_for("advisor.recommendation", {"suggested_action": ""}, ts=_TS) is None


def test_advisory_text_omits_absent_fields():
    text = advisory_text({"suggested_action": "do X"})
    assert text == "do X"


@pytest.mark.asyncio
async def test_run_records_a_published_event_with_the_injected_clock(tmp_path):
    bus = EventBus()
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    fixed = datetime(2026, 7, 24, 5, 0, 1, tzinfo=timezone.utc)
    rec = ChatRecorder(bus=bus, repo=repo, now=lambda: fixed)
    started = asyncio.Event()
    rec.on_started = started.set
    task = asyncio.create_task(rec.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.question", "payload": {"text": "hola", "source": "operator"}})
    for _ in range(50):
        await asyncio.sleep(0.01)
        if repo.tail():
            break
    turns = repo.tail()
    assert [t.text for t in turns] == ["hola"]
    assert turns[0].ts == fixed.isoformat()  # the injected clock stamped it
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_run_survives_an_append_failure_and_never_logs_the_text(tmp_path, caplog):
    class _BoomRepo:
        path = tmp_path / "chat_history.jsonl"

        def append(self, turn):
            raise OSError("disk full")

    bus = EventBus()
    rec = ChatRecorder(bus=bus, repo=_BoomRepo())  # type: ignore[arg-type]
    started = asyncio.Event()
    rec.on_started = started.set
    task = asyncio.create_task(rec.run())
    await asyncio.wait_for(started.wait(), 1.0)
    with caplog.at_level(logging.DEBUG):
        await bus.publish(
            {"type": "operator.question", "payload": {"text": "SECRET-PASSWORD", "source": "operator"}}
        )
        await asyncio.sleep(0.05)
        assert not task.done()  # the failed append did NOT kill the loop
        assert "SECRET-PASSWORD" not in caplog.text  # a turn's text is never logged
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_chat_recorder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'redrobin.application.chat'`

- [ ] **Step 3: Create the package marker**

Create `redrobin/application/chat/__init__.py` as an empty file (matching `redrobin/application/agents/__init__.py`).

- [ ] **Step 4: Write the implementation**

Create `redrobin/application/chat/recorder.py`:

```python
"""redrobin/application/chat/recorder.py — the chat transcript's single writer.

A bus consumer with the same shape as ``WorkingMemoryReducer``/``Assistant``: subscribe,
loop, dispatch, exit on the ``_close`` sentinel. It maps the assistant-stream events to
:class:`~redrobin.domain.chat.ChatTurn` rows and appends them, so the conversation
survives a daemon restart and can be fed back into later cycles.

Never-crash: a failed append is logged and swallowed — the loop survives (a full disk
must not kill the chat, let alone the assistant). A turn's text is NEVER logged: it can
contain credentials the operator pasted or discussed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from redrobin.application.agents.assistant.interpret import is_own_proposal_question
from redrobin.application.event_bus import EventBus
from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def advisory_text(payload: dict[str, Any]) -> str:
    """Compose an advisory's stored text from its wire payload.

    The application layer cannot reuse the TUI renderer (import-linter: application
    never imports presentation), so this mirrors the operator-facing shape — the action
    plus why/opsec/conf — minus refs, which were dropped from the output on 2026-07-24.
    Absent fields are omitted rather than rendered empty.
    """
    lines = [str(payload.get("suggested_action") or "")]
    for label, key in (("why", "rationale"), ("opsec", "opsec_risk"), ("conf", "confidence")):
        value = str(payload.get(key) or "")
        if value:
            lines.append(f"{label}    {value}")
    return "\n".join(line for line in lines if line)


def turn_for(event_type: str, payload: dict[str, Any], *, ts: str) -> ChatTurn | None:
    """Map one bus event to a ChatTurn, or None when it is not part of the chat.

    The advisory/heads-up split uses the SAME discriminator the TUI renders with (a
    ``correlation_id`` means a command triggered it), so a stored turn and its chat line
    can never disagree. Pure; never raises.
    """
    from_operator = payload.get("source") == "operator"
    text = str(payload.get("text") or "")

    if event_type == "operator.question":
        if from_operator:
            return ChatTurn(ts=ts, role="operator", kind="question", text=text) if text else None
        if is_own_proposal_question(payload):
            return ChatTurn(ts=ts, role="assistant", kind="confirm", text=text) if text else None
        return None

    if event_type == "operator.answer":
        if not text:
            return None
        role = "operator" if from_operator else "assistant"
        return ChatTurn(ts=ts, role=role, kind="answer", text=text)

    if event_type == "advisor.recommendation":
        body = advisory_text(payload)
        if not body.strip():
            return None
        correlation_id = payload.get("correlation_id")
        return ChatTurn(
            ts=ts,
            role="assistant",
            kind="advisory" if correlation_id else "heads_up",
            text=body,
            correlation_id=str(correlation_id) if correlation_id else None,
        )

    return None


class ChatRecorder:
    """Appends the assistant stream to the chat transcript (the store's single writer)."""

    def __init__(
        self,
        *,
        bus: EventBus,
        repo: ChatHistoryRepo,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._bus = bus
        self._repo = repo
        self._now = now
        # Optional readiness callback, mirroring the reducer/assistant: the lifespan and
        # tests await it so no early event is missed.
        self.on_started: Callable[[], None] | None = None

    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("chat recorder on_started callback raised")
        try:
            while True:
                evt = await sub.get()
                if evt.get("_type") == "_close":
                    return
                self._record(evt)
        finally:
            await self._bus.unsubscribe(sub)

    def _record(self, evt: dict[str, Any]) -> None:
        """Map + append one event. Never raises — a write failure must not kill the loop."""
        try:
            payload = evt.get("payload")
            turn = turn_for(
                str(evt.get("type") or ""),
                payload if isinstance(payload, dict) else {},
                ts=self._now().isoformat(),
            )
            if turn is None:
                return
            self._repo.append(turn)
        except Exception:
            # NEVER log the turn's text — it can contain credentials.
            logger.exception("chat recorder: failed to append a turn to %s", self._repo.path)


__all__ = ["ChatRecorder", "advisory_text", "turn_for"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_chat_recorder.py -q`
Expected: PASS — 10 passed.

- [ ] **Step 6: Run the type + lint gates**

Run: `uv run --no-sync mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 7: Commit**

```bash
git add redrobin/application/chat/__init__.py redrobin/application/chat/recorder.py tests/unit/test_chat_recorder.py
git commit -m "feat(chat): ChatRecorder bus consumer writes the conversation transcript"
```

---

### Task 4: The history reader — selected slice, rendered for the prompt

**Files:**
- Create: `redrobin/application/chat/history.py`
- Test: `tests/unit/test_chat_history_reader.py`

**Interfaces:**
- Consumes: `select_context_turns`, `VOLUNTEERED_KINDS`, `ChatTurn` (Task 1); `ChatHistoryRepo.tail()` (Task 2).
- Produces:
  - `render_turns(turns: list[ChatTurn]) -> str`
  - `build_history_reader(repo: ChatHistoryRepo, *, dialogue_max: int, volunteered_max: int) -> Callable[[], str]` — the zero-arg reader the `Assistant` receives (a drop-in twin of `summary_reader`).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_chat_history_reader.py`:

```python
from __future__ import annotations

from redrobin.application.chat.history import build_history_reader, render_turns
from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo


def _turn(n: int, kind: str, role: str, text: str) -> ChatTurn:
    return ChatTurn(ts=f"2026-07-24T05:00:{n:02d}.000000+00:00", role=role, kind=kind, text=text)


def test_render_labels_roles_and_marks_volunteered_kinds():
    out = render_turns(
        [
            _turn(1, "question", "operator", "path to DA?"),
            _turn(2, "answer", "assistant", "Kerberoast svc_sql"),
            _turn(3, "advisory", "assistant", "Validate svc_reports"),
            _turn(4, "heads_up", "assistant", "Baseline defences"),
        ]
    )
    assert "[operator] path to DA?" in out
    assert "[assistant] Kerberoast svc_sql" in out
    assert "[assistant · advisory] Validate svc_reports" in out
    assert "[assistant · heads-up] Baseline defences" in out


def test_render_empty_is_empty_string():
    assert render_turns([]) == ""


def test_reader_returns_rendered_selection(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(_turn(1, "question", "operator", "path to DA?"))
    repo.append(_turn(2, "advisory", "assistant", "Validate svc_reports"))
    read = build_history_reader(repo, dialogue_max=10, volunteered_max=5)
    out = read()
    assert "[operator] path to DA?" in out
    assert "[assistant · advisory] Validate svc_reports" in out


def test_reader_applies_the_two_windows(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(_turn(1, "question", "operator", "old question"))
    for n in range(2, 12):
        repo.append(_turn(n, "advisory", "assistant", f"adv{n}"))
    read = build_history_reader(repo, dialogue_max=10, volunteered_max=2)
    out = read()
    assert "old question" in out  # dialogue quota protected it
    assert out.count("adv") == 2  # only the last 2 advisories


def test_reader_returns_empty_string_when_there_is_no_history(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    assert build_history_reader(repo, dialogue_max=10, volunteered_max=5)() == ""


def test_reader_returns_empty_string_when_the_repo_raises(tmp_path):
    class _BoomRepo:
        path = tmp_path / "chat_history.jsonl"

        def tail(self, max_lines: int = 500):
            raise OSError("unreadable")

    read = build_history_reader(_BoomRepo(), dialogue_max=10, volunteered_max=5)  # type: ignore[arg-type]
    assert read() == ""  # degrades, never raises
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_chat_history_reader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'redrobin.application.chat.history'`

- [ ] **Step 3: Write the implementation**

Create `redrobin/application/chat/history.py`:

```python
"""redrobin/application/chat/history.py — the chat slice the assistant reasons over.

Builds the injected reader the ``Assistant`` consumes: read the tail -> apply the
domain's two-window selection -> render one plain-text block. Returning a STRING makes
it a drop-in twin of ``summary_reader`` (the L5 seam), which keeps the agent's context
builders free of chat types and keeps the rendering unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from redrobin.domain.chat import VOLUNTEERED_KINDS, ChatTurn, select_context_turns
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo

logger = logging.getLogger(__name__)


def render_turns(turns: list[ChatTurn]) -> str:
    """Render selected turns as the ``# Conversation so far`` body.

    Dialogue turns are labelled by role alone; a volunteered turn also carries its kind
    (``advisory`` / ``heads-up``) so the model can tell an answer it was asked for from
    something it offered unprompted — the same distinction the TUI shows the operator.
    """
    lines: list[str] = []
    for turn in turns:
        label = turn.role
        if turn.kind in VOLUNTEERED_KINDS:
            label = f"{turn.role} · {turn.kind.replace('_', '-')}"
        lines.append(f"[{label}] {turn.text}")
    return "\n".join(lines)


def build_history_reader(
    repo: ChatHistoryRepo, *, dialogue_max: int, volunteered_max: int
) -> Callable[[], str]:
    """A zero-arg reader returning the rendered chat slice, or "" when there is none.

    Never raises: a read failure degrades to "" so the cycle still runs (the assistant
    then simply has no conversation context, exactly as before this feature).
    """

    def _read() -> str:
        try:
            turns = select_context_turns(
                repo.tail(), dialogue_max=dialogue_max, volunteered_max=volunteered_max
            )
        except Exception:
            logger.exception("chat history: read failed; proceeding without conversation context")
            return ""
        return render_turns(turns)

    return _read


__all__ = ["build_history_reader", "render_turns"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_chat_history_reader.py -q`
Expected: PASS — 6 passed.

- [ ] **Step 5: Run the type + lint gates**

Run: `uv run --no-sync mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/chat/history.py tests/unit/test_chat_history_reader.py
git commit -m "feat(chat): history reader renders the selected conversation slice"
```

---

### Task 5: Inject the chat into the cycles (NOT interpretation)

**Files:**
- Modify: `redrobin/application/agents/assistant/agent.py` (`build_push_context`, `build_answer_context`, `run_cycle`, `run_answer_cycle`)
- Modify: `redrobin/application/agents/assistant/assistant.py` (`__init__`, new `_read_history`, the three cycle call sites)
- Test: `tests/unit/agents/assistant/test_assistant_chat_context.py`

**Interfaces:**
- Consumes: the `Callable[[], str]` produced by `build_history_reader` (Task 4).
- Produces:
  - `build_push_context(..., chat_history: str = "")` and `build_answer_context(..., chat_history: str = "")` — both emit a `# Conversation so far` section.
  - `run_cycle(..., chat_history: str = "")` and `run_answer_cycle(..., chat_history: str = "")` — pass-through.
  - `Assistant(..., history_reader: Callable[[], str] | None = None, ...)` and `Assistant._read_history() -> str`.
  - `run_interpretation_cycle` is deliberately **unchanged**.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/assistant/test_assistant_chat_context.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from redrobin.application.agents.assistant.agent import build_answer_context, build_push_context
from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus

_REC_JSON = (
    '{"suggested_action":"rotate to comsvcs","rationale":"loud EDR",'
    '"opsec_risk":"WARN","confidence":"medium"}'
)
_HISTORY = "[operator] path to DA?\n[assistant] Kerberoast svc_sql"


class _CapturingLLM:
    """Records every user_message it is asked to run, and answers plausibly."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        self.messages.append(user_message)
        if "The operator says" in user_message:
            return '{"answer": "kerberoast then crack", "lesson": null}'
        if "SHARED PICTURE" in system or "milestone_id" in system:
            return '{"phase": "recon", "milestone_id": ""}'
        return _REC_JSON


def _assistant(bus, llm, *, history_reader):
    return Assistant(
        bus=bus,
        llm=llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 1000.0),
        summary_reader=lambda: "recon done",
        history_reader=history_reader,
        enabled=True,
        clock=lambda: 1000.0,
    )


def test_push_context_emits_the_conversation_section():
    ctx = build_push_context(
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon done",
        trigger_command="whoami",
        chat_history=_HISTORY,
    )
    assert "# Conversation so far" in ctx
    assert "Kerberoast svc_sql" in ctx


def test_push_context_without_history_says_so():
    ctx = build_push_context(wm_snapshot={}, l5_summary="", trigger_command="whoami")
    assert "# Conversation so far" in ctx
    assert "(no conversation yet)" in ctx


def test_answer_context_emits_the_conversation_section():
    ctx = build_answer_context(
        wm_snapshot={}, l5_summary="", question="hacé el paso 1", chat_history=_HISTORY
    )
    assert "# Conversation so far" in ctx
    assert "Kerberoast svc_sql" in ctx


@pytest.mark.asyncio
async def test_conversational_cycle_receives_the_history():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._on_question({"text": "hacé el paso 1", "question_id": "opq-0"})
    assert any("Kerberoast svc_sql" in m for m in llm.messages)


@pytest.mark.asyncio
async def test_reactive_cycle_receives_the_history():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._on_enrichment({"command": "whoami", "beacon_id": "b1"})
    assert any("Kerberoast svc_sql" in m for m in llm.messages)


@pytest.mark.asyncio
async def test_proactive_cycle_receives_the_history():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    assert any("Kerberoast svc_sql" in m for m in llm.messages)


@pytest.mark.asyncio
async def test_interpretation_cycle_does_NOT_receive_the_history():
    # the interpretation cycle WRITES engagement state; chat must never drive a state
    # write, so its prompt must not carry the conversation (spec §5.5)
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._safe_interpret()
    assert llm.messages, "the interpretation cycle should have run"
    assert all("Kerberoast svc_sql" not in m for m in llm.messages)


@pytest.mark.asyncio
async def test_cycle_survives_a_raising_history_reader():
    def _boom() -> str:
        raise RuntimeError("reader exploded")

    bus = EventBus()
    sub = await bus.subscribe()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=_boom)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")  # must not raise
    evt = await asyncio.wait_for(sub.get(), 1.0)
    while evt.get("type") != "advisor.recommendation":
        evt = await asyncio.wait_for(sub.get(), 1.0)
    assert evt["payload"]["suggested_action"] == "rotate to comsvcs"


@pytest.mark.asyncio
async def test_no_history_reader_keeps_prior_behaviour():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=None)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    assert any("(no conversation yet)" in m for m in llm.messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_assistant_chat_context.py -q`
Expected: FAIL — `TypeError: Assistant.__init__() got an unexpected keyword argument 'history_reader'` (and the context tests fail on the missing `chat_history` kwarg).

- [ ] **Step 3: Add the section to `build_push_context`**

In `redrobin/application/agents/assistant/agent.py`, change the `build_push_context` signature to add the parameter after `trigger_command`:

```python
def build_push_context(
    *,
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    trigger_command: str,
    chat_history: str = "",
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
    proactive: bool = False,
) -> str:
```

Then replace its `lines = [...]` block (the one ending with `"",` before the `if proactive:`) with:

```python
    lines = [
        "# Engagement working memory (L2)",
        wm_json,
        "",
        "# Story so far (L5)",
        l5_summary or "(no summary yet)",
        "",
        "# Conversation so far",
        chat_history or "(no conversation yet)",
        "",
    ]
```

- [ ] **Step 4: Add the section to `build_answer_context`**

In the same file, change `build_answer_context` to accept the parameter after `question` and emit the section before `# The operator says`:

```python
def build_answer_context(
    *,
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    question: str,
    chat_history: str = "",
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
) -> str:
    """Assemble the conversational PUSH context: L2 (current working memory) + L5
    (story so far) + the chat thread + the operator's question. The L2 snapshot is
    capped to the most-recent ``wm_max_items`` per category (context-rot guard).
    Non-mutating."""
    capped = cap_wm_snapshot(wm_snapshot, wm_max_items)
    return "\n".join(
        [
            "# Engagement working memory (L2)",
            json.dumps(capped, ensure_ascii=False, sort_keys=True),
            "",
            "# Story so far (L5)",
            l5_summary or "(no summary yet)",
            "",
            "# Conversation so far",
            chat_history or "(no conversation yet)",
            "",
            "# The operator says",
            question or "(empty)",
            "",
            "Answer them. Call get_lessons first. If this is a correction, capture the lesson.",
        ]
    )
```

- [ ] **Step 5: Thread the parameter through both cycle runners**

In `run_cycle`, add `chat_history: str = ""` to the signature (after `correlation_id`) and pass it into `build_push_context`:

```python
        user_message=build_push_context(
            wm_snapshot=wm_snapshot,
            l5_summary=l5_summary,
            trigger_command=trigger_command,
            chat_history=chat_history,
            wm_max_items=wm_max_items,
            proactive=proactive,
        ),
```

In `run_answer_cycle`, add `chat_history: str = ""` to the signature (after `question`) and pass it into `build_answer_context`:

```python
        user_message=build_answer_context(
            wm_snapshot=wm_snapshot,
            l5_summary=l5_summary,
            question=question,
            chat_history=chat_history,
            wm_max_items=wm_max_items,
        ),
```

Leave `run_interpretation_cycle` and `build_interpret_context` **untouched**. (Verified: `run_interpretation_cycle` builds its prompt with its own `build_interpret_context`, NOT with `build_push_context` — so the interpretation cycle cannot inherit the chat section structurally, not merely by discipline.)

- [ ] **Step 6: Add `history_reader` to the `Assistant`**

In `redrobin/application/agents/assistant/assistant.py`, add the constructor parameter immediately after `snapshot_reader`:

```python
        snapshot_reader: Callable[[], dict[str, Any]] | None = None,
        history_reader: Callable[[], str] | None = None,
```

and store it next to the other readers:

```python
        self._snapshot_reader = snapshot_reader
        self._history_reader = history_reader
```

Then add this method immediately after `_seed_snapshot`:

```python
    def _read_history(self) -> str:
        """The chat slice for this cycle (empty when the feature is unwired).

        Never raises: a failing reader degrades to no conversation context rather than
        breaking the cycle — the same contract as summary_reader."""
        if self._history_reader is None:
            return ""
        try:
            return self._history_reader()
        except Exception:
            logger.exception("assistant: history_reader failed; proceeding without chat history")
            return ""
```

- [ ] **Step 7: Pass the history into the three cycles**

In `_on_enrichment`, add the argument to the wrapped `run_cycle` call:

```python
        async with self._thinking():
            rec = await run_cycle(
                llm=self._llm,
                tools=self._tools,
                wm_snapshot=self._wm_snapshot,
                l5_summary=l5_summary,
                trigger_command=trigger_command,
                correlation_id=trigger_key,
                chat_history=self._read_history(),
                max_tokens=self._max_tokens,
                wm_max_items=self._wm_max_items,
            )
```

In `_on_question`:

```python
        async with self._thinking():
            parsed = await run_answer_cycle(
                llm=self._llm,
                tools=self._tools,
                wm_snapshot=self._wm_snapshot,
                l5_summary=l5_summary,
                question=question,
                chat_history=self._read_history(),
                max_tokens=self._max_tokens,
                wm_max_items=self._wm_max_items,
            )
```

In `_maybe_proactive`:

```python
        async with self._thinking():
            rec = await run_cycle(
                llm=self._llm,
                tools=self._tools,
                wm_snapshot=self._wm_snapshot,
                l5_summary=l5_summary,
                trigger_command="",
                correlation_id=None,
                chat_history=self._read_history(),
                max_tokens=self._max_tokens,
                wm_max_items=self._wm_max_items,
                proactive=True,
            )
```

Do **NOT** touch `_safe_interpret`'s `run_interpretation_cycle` call.

- [ ] **Step 8: Run the new tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_assistant_chat_context.py -q`
Expected: PASS — 9 passed.

- [ ] **Step 9: Run the assistant regression tests + gates**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/agents/assistant/ tests/integration/test_assistant.py -q`
Expected: PASS — the existing cycle/prompt tests are unaffected (the new parameter defaults to `""`, so every prior caller behaves as before).

Run: `uv run --no-sync mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add redrobin/application/agents/assistant/agent.py redrobin/application/agents/assistant/assistant.py tests/unit/agents/assistant/test_assistant_chat_context.py
git commit -m "feat(assistant): feed the chat thread into the reactive, proactive, and answer cycles"
```

---

### Task 6: Settings + lifespan wiring + end-to-end integration test

**Files:**
- Modify: `redrobin/infrastructure/config.py` (add two `assistant_chat_*` fields next to the other `assistant_*` fields)
- Modify: `redrobin/api/lifespan_helpers.py` (`start_assistant`: build the repo, start the recorder, inject the reader, register the close hook)
- Test: `tests/integration/test_chat_persistence.py`

**Interfaces:**
- Consumes: `ChatHistoryRepo` (Task 2), `ChatRecorder` (Task 3), `build_history_reader` (Task 4), `Assistant(history_reader=...)` (Task 5).
- Produces: `Settings.assistant_chat_dialogue_turns: int` and `Settings.assistant_chat_advisory_turns: int`; a running `ChatRecorder` on `app.state.chat_recorder` and the repo on `app.state.chat_history_repo`.

**Note on the file path:** derive it from the already-resolved working-memory path — `app.state.working_memory_repo.path.parent / "chat_history.jsonl"` (`WorkingMemoryRepo.path` is a public attribute). `start_working_memory` runs earlier in the lifespan and already resolves the engagement directory (with a root-dir fallback in degraded mode), so this reuses that resolution instead of duplicating it.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_chat_persistence.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from redrobin.application.chat.history import build_history_reader
from redrobin.application.chat.recorder import ChatRecorder
from redrobin.application.event_bus import EventBus
from redrobin.infrastructure.config import Settings
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo


def test_settings_expose_the_two_chat_window_knobs():
    s = Settings()
    assert s.assistant_chat_dialogue_turns == 10
    assert s.assistant_chat_advisory_turns == 5


def test_settings_chat_knobs_read_from_env(monkeypatch):
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS", "3")
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS", "1")
    s = Settings()
    assert s.assistant_chat_dialogue_turns == 3
    assert s.assistant_chat_advisory_turns == 1


@pytest.mark.asyncio
async def test_bus_events_become_a_readable_conversation(tmp_path):
    # End-to-end over a real EventBus + a real repo: the recorder persists the stream and
    # the reader renders it back for the model.
    bus = EventBus()
    repo = ChatHistoryRepo(tmp_path / "engagements" / "vantage" / "chat_history.jsonl")
    recorder = ChatRecorder(bus=bus, repo=repo)
    started = asyncio.Event()
    recorder.on_started = started.set
    task = asyncio.create_task(recorder.run())
    await asyncio.wait_for(started.wait(), 1.0)

    await bus.publish(
        {"type": "operator.question", "payload": {"text": "path to DA?", "source": "operator"}}
    )
    await bus.publish(
        {"type": "operator.answer", "payload": {"text": "Kerberoast svc_sql", "question_id": "opq-0"}}
    )
    await bus.publish(
        {
            "type": "advisor.recommendation",
            "payload": {
                "suggested_action": "Validate svc_reports",
                "rationale": "quiet bind",
                "opsec_risk": "WARN",
                "confidence": "high",
                "correlation_id": "B-7:whoami",
            },
        }
    )
    await bus.publish({"type": "enrichment.completed", "payload": {"command": "whoami"}})  # ignored

    for _ in range(100):
        await asyncio.sleep(0.01)
        if len(repo.tail()) >= 3:
            break

    turns = repo.tail()
    assert [(t.role, t.kind) for t in turns] == [
        ("operator", "question"),
        ("assistant", "answer"),
        ("assistant", "advisory"),
    ]

    rendered = build_history_reader(repo, dialogue_max=10, volunteered_max=5)()
    assert "[operator] path to DA?" in rendered
    assert "[assistant] Kerberoast svc_sql" in rendered
    assert "[assistant · advisory] Validate svc_reports" in rendered

    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_transcript_survives_a_recorder_restart(tmp_path):
    # A daemon restart must not lose the thread: a fresh recorder appends to the same file.
    path = tmp_path / "chat_history.jsonl"
    for text in ("first run", "second run"):
        bus = EventBus()
        repo = ChatHistoryRepo(path)
        recorder = ChatRecorder(bus=bus, repo=repo)
        started = asyncio.Event()
        recorder.on_started = started.set
        task = asyncio.create_task(recorder.run())
        await asyncio.wait_for(started.wait(), 1.0)
        await bus.publish(
            {"type": "operator.question", "payload": {"text": text, "source": "operator"}}
        )
        for _ in range(100):
            await asyncio.sleep(0.01)
            if any(t.text == text for t in repo.tail()):
                break
        await bus.publish({"_type": "_close"})
        await asyncio.wait_for(task, 1.0)

    assert [t.text for t in ChatHistoryRepo(path).tail()] == ["first run", "second run"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_chat_persistence.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'assistant_chat_dialogue_turns'` on the two Settings tests.

- [ ] **Step 3: Add the Settings fields**

In `redrobin/infrastructure/config.py`, immediately after the `assistant_wm_max_items` field, add:

```python
    # Chat-history context windows (spec 2026-07-24): each kind group gets its own quota
    # so a burst of beacon-driven advisories can never evict the dialogue thread.
    assistant_chat_dialogue_turns: int = Field(
        default=10, alias="REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS"
    )
    assistant_chat_advisory_turns: int = Field(
        default=5, alias="REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS"
    )
```

- [ ] **Step 4: Run the Settings tests to verify they pass**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_chat_persistence.py -q`
Expected: PASS — 4 passed (the recorder/reader tests already pass; they use Tasks 2-4 directly).

- [ ] **Step 5: Wire the recorder + reader into the lifespan**

In `redrobin/api/lifespan_helpers.py`, add these imports next to the existing application imports (keep alphabetical order within the `redrobin.application` group):

```python
from redrobin.application.chat.history import build_history_reader
from redrobin.application.chat.recorder import ChatRecorder
```

and next to the existing infrastructure import group:

```python
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo
```

In `start_assistant`, immediately after the `kb` / `journal` / `lesson_store` block and BEFORE `assistant = Assistant(...)`, insert:

```python
    # Chat transcript (spec 2026-07-24). Gated on assistant_enabled like kb/journal: with
    # the assistant off there is no stream to record. The path reuses the engagement
    # directory start_working_memory already resolved (it also handles the degraded
    # no-engagement fallback), so the resolution logic is not duplicated here.
    history_reader: Callable[[], str] | None = None
    chat_task: asyncio.Task[None] | None = None
    if settings.assistant_enabled:
        wm_repo = getattr(app.state, "working_memory_repo", None)
        if wm_repo is not None:
            chat_repo = ChatHistoryRepo(wm_repo.path.parent / "chat_history.jsonl")
            app.state.chat_history_repo = chat_repo
            history_reader = build_history_reader(
                chat_repo,
                dialogue_max=settings.assistant_chat_dialogue_turns,
                volunteered_max=settings.assistant_chat_advisory_turns,
            )
            chat_recorder = ChatRecorder(bus=app.state.event_bus, repo=chat_repo)
            chat_started = asyncio.Event()
            chat_recorder.on_started = chat_started.set
            chat_task = asyncio.create_task(chat_recorder.run(), name="chat-recorder")
            await chat_started.wait()
            app.state.chat_recorder = chat_recorder
            app.state.chat_recorder_task = chat_task
```

Add the reader to the `Assistant(...)` construction, immediately after `snapshot_reader=_read_wm_snapshot,`:

```python
        history_reader=history_reader,
```

Finally, extend the existing `close()` hook in `start_assistant` so the recorder is drained too. **Drain both tasks CONCURRENTLY under one timeout** — the close hooks run in reverse start order, so the EventBus (started first) is closed LAST, meaning neither task has received the `_close` sentinel yet when this hook runs and both will hit their timeout. Two sequential `wait_for(…, 2.0)` calls would therefore stall shutdown for 4 s instead of 2 s. Replace the current body:

```python
    async def close() -> None:
        # Both tasks are still blocked on the bus here (the bus closes after us, in
        # reverse start order), so they are awaited CONCURRENTLY under a single
        # timeout — sequential waits would double the shutdown stall.
        pending_tasks = [t for t in (assistant_task, chat_task) if t is not None]
        _, pending = await asyncio.wait(pending_tasks, timeout=2.0)
        for task in pending:
            task.cancel()
```

`chat_task` is the local captured from the wiring block above (None when the assistant is disabled) — no `app.state` lookup needed.

- [ ] **Step 6: Run the full suite + all 5 gates**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS — all previous tests plus the new ones; no regressions (notably `tests/integration/test_assistant_lifespan.py`, which boots the lifespan).

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
```
Expected: clean; mypy `Success`; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 7: Commit**

```bash
git add redrobin/infrastructure/config.py redrobin/api/lifespan_helpers.py tests/integration/test_chat_persistence.py
git commit -m "feat(chat): wire the chat recorder + history reader into the daemon lifespan"
```

---

### Final: whole-change review

- [ ] **Step 1: Confirm the acceptance criteria from the spec's DoD**

Verify by inspection + the suite (no operator smoke required — acceptance is deterministic per `development-workflow.md` §5, since no live surface changes):
- `chat_history.jsonl` accumulates per engagement across restarts (Task 6 integration test).
- All six event→turn mappings correct; nothing else recorded (Task 3 tests).
- Conversational/reactive/proactive receive the history; interpretation does not (Task 5 tests).
- The two-window rule holds under a 20-advisory burst (Task 1 + Task 4 tests).
- Never-crash on: failed append, raising reader, malformed line, missing file (Tasks 2-5 tests).
- No turn text in logs (Task 3 test asserts on `caplog`).
- Both Settings honoured (Task 6 tests).

- [ ] **Step 2: Whole-change review** (subagent-driven-development dispatches the final reviewer here), then a manual sanity check if desired: start the daemon with `REDROBIN_ASSISTANT_ENABLED=1`, ask two chained questions in the TUI, and confirm `~/.redrobin/engagements/<slug>/chat_history.jsonl` contains the turns.

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:

| Spec section | Task |
|---|---|
| §5.1 `ChatTurn`, kind frozensets, `select_context_turns` | Task 1 |
| §5.2 `ChatHistoryRepo` (append / bounded `tail` / malformed-skip / missing file) | Task 2 |
| §5.2 `_MAX_TAIL_LINES = 500` module constant | Task 2 (test asserts the value) |
| §5.3 `ChatRecorder`, the 6-row event→turn table, injected clock, never-crash, no-text-logging | Task 3 |
| §5.3 reuse `is_own_proposal_question` | Task 3 (imported, not re-implemented) |
| §5.3 advisory text composition (no TUI import) | Task 3 (`advisory_text`) |
| §5.4 `build_history_reader` → `str`, render format, `""` on failure | Task 4 |
| §5.5 `chat_history` param on both context builders + both cycle runners | Task 5 |
| §5.5 `Assistant(history_reader=...)`, guarded read | Task 5 (`_read_history`) |
| §5.5 the cycle table — interpretation excluded | Task 5 (a dedicated negative test) |
| §5.6 lifespan wiring, `assistant_enabled` gate, path resolution, close hook | Task 6 |
| §5.7 the two Settings + env aliases | Task 6 |
| §6 the two-window rule + the dilution guarantee | Task 1 (`test_advisory_burst_cannot_evict_the_dialogue_thread`) |
| §7 DoD | Final step 1 (mapped item by item) |
| §8 test strategy (domain / infra / application / assistant / integration) | Tasks 1-6 — every listed test file exists |
| §9 non-goals | Nothing in the plan implements compaction, TUI replay, `search_chat`, retention, or redaction |
| §10 risks | Dilution → Task 1 test; context growth → the two caps (Tasks 1+6); secrets → Task 3's no-log test; interrupted append → Task 2's malformed-line test |

**2. Placeholder scan** — no `TBD`/`TODO`/"handle edge cases"/"similar to Task N". Every code step shows complete code; every command states its expected output.

**3. Type consistency** — `ChatTurn` field names/order identical in Tasks 1-4. `select_context_turns(turns, *, dialogue_max, volunteered_max)` called with exactly those keywords in Task 4. `ChatHistoryRepo.tail()`/`.append()`/`.path` used consistently in Tasks 3, 4, 6 (`path` is a public attribute, matching `WorkingMemoryRepo.path`). `build_history_reader(repo, *, dialogue_max, volunteered_max) -> Callable[[], str]` matches its Task 6 call site (`volunteered_max=settings.assistant_chat_advisory_turns`). The kind strings (`heads_up` with an underscore in data; rendered as `heads-up`) are consistent between Tasks 1, 3, and 4. `Callable` is already imported in `lifespan_helpers.py` (line 23), so Task 6's annotation needs no new import.

**4. Task independence** — Tasks 1→2→3/4→5→6 in dependency order. Task 5 is the only one touching existing assistant files; Task 6 is the only one touching the lifespan. Every task ends with a green suite and a commit.
