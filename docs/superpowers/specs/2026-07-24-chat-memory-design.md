# Assistant Conversational Memory + Chat Persistence — Design Spec

**Date:** 2026-07-24 · **Branch:** `feat-chat-memory` (off `dev`) · **Status:** approved (operator, 2026-07-24)
**Follows:** the AI-DLC — brainstorming-phase spec; next `writing-plans` → `auditing-plans` → `subagent-driven-development` → verify → finishing.

Gives Red Robin **short-term memory of the ongoing chat** and **persists the conversation** to disk (making it inspectable and replayable later). Grounded in how Claude Code handles session persistence — **minus auto-compaction**, which this scale does not need.

---

## §1 Context & Problem

Two gaps, one root cause: **the conversation is nowhere.**

- **No conversational memory.** `build_answer_context` (`agents/assistant/agent.py`) assembles **L2** (working-memory snapshot) + **L5** (story so far) + **the current question** — and nothing else. Every question is answered in isolation, so a follow-up like *"hacé el paso 1"* after *"what's my path to DA?"* has no referent. The assistant also cannot know what it already advised, so it can re-suggest something the operator already rejected.
- **No persistence.** The chat lives only in the TUI's `RichLog` scrollback (client-side, ephemeral). Restarting the TUI clears it (known #28 limitation); nothing on the daemon side records what was said. The conversation is therefore invisible to any other tooling and lost to the engagement record.

Distinct from what already exists: **L5** summarizes *beacon activity*, and **Lessons** capture *durable corrections*. Neither is the session thread. The chat is a **new, third layer** — mirroring Claude Code's separation of *transcript* (the conversation) from *CLAUDE.md* (persistent instructions) and *MEMORY.md* (auto-learned facts).

## §2 Goal

Persist the **full assistant stream** (operator questions, assistant answers, advisories, heads-ups, confirms) as an append-only per-engagement log, and feed a **bounded, recent slice** of it into the cycles that benefit — so the assistant follows the thread without the log growing unbounded in context.

## §3 Pillars

- **Clarity (for the human).** One cohesive `chat/` module in the application layer; a plain-text-readable JSONL on disk next to `events.jsonl`; kind names that say what they are. A reader can answer "where does the chat live?" in one look.
- **Maintainability.** One responsibility per unit: the domain decides *what to select*, the repo *persists*, the recorder *listens*, the reader *renders*. Each is unit-testable alone. The assistant consumes it through the **same injected-reader seam** already used by `summary_reader` and `snapshot_reader` (#30) — no new integration pattern to learn.
- **Scalability.** Append is O(1) per turn. Reads are bounded in memory (`deque(maxlen=…)`), never O(file). Context injection is capped by two explicit windows, so a long engagement cannot inflate the prompt.
- **Architecture.** All four import-linter contracts hold: `domain/chat.py` stays pure; file I/O lives in `infrastructure/persistence/`; `application/chat/` orchestrates; no presentation import anywhere.

## §4 Prior art — what we take from Claude Code (and what we skip)

Researched via the `claude-code-guide` agent against current Claude Code docs.

| Claude Code mechanic | Adopted here |
|---|---|
| Append-only **JSONL** transcript, one object per entry | ✅ `chat_history.jsonl` |
| **Scoped** storage (`~/.claude/projects/<project>/…`) | ✅ scoped per **engagement** (`engagements/<slug>/`) |
| Entry carries **role + content + kind + timestamp** | ✅ `ChatTurn(ts, role, kind, text, correlation_id)` |
| **Re-hydrate from disk** on restart | ✅ the reader always reads disk (same principle as the #30 boot seed) |
| **Separate layers**: transcript vs persistent instructions vs auto-memory | ✅ chat (new) vs system prompts vs **Lessons** — kept distinct |
| **Don't parse the format ad-hoc** — go through an interface | ✅ only `ChatHistoryRepo` touches the file format |
| Auto-**compaction** (summarize older turns under context pressure) | ❌ **out of scope** — see §9 |
| Retention/cleanup window (`cleanupPeriodDays`) | ❌ out of scope — engagements are archived/deleted as a unit |

**Why no compaction:** Claude Code compacts because its sessions run to thousands of turns with large tool outputs. A Red Robin engagement chat is tens of turns. Two fixed windows (§6) bound the context deterministically, with no extra LLM call, no watermark state, and no summarizer failure mode. The **complete** transcript is persisted regardless, so compaction can be added later purely additively — reading the same file, changing nothing else.

**Deviation from Claude Code — one continuous log, not per-session.** Claude Code scopes a transcript per *session* because a CLI run is ephemeral. Red Robin's daemon is long-running and restarts mid-operation (observed repeatedly during the 2026-07-22/23 smoke). The conversation belongs to the **operation**, so a daemon restart must not make the assistant forget. One `chat_history.jsonl` per engagement; restarts are invisible to the thread.

## §5 Design — components

### §5.1 `redrobin/domain/chat.py` (pure — no I/O, no imports outside `domain`)

```python
@dataclass(frozen=True)
class ChatTurn:
    ts: str                        # ISO-8601 UTC, microsecond precision
    role: str                      # "operator" | "assistant"
    kind: str                      # question | answer | advisory | heads_up | confirm
    text: str
    correlation_id: str | None = None

DIALOGUE_KINDS    = frozenset({"question", "answer", "confirm"})
VOLUNTEERED_KINDS = frozenset({"advisory", "heads_up"})

def select_context_turns(
    turns: list[ChatTurn], *, dialogue_max: int, volunteered_max: int
) -> list[ChatTurn]: ...
```

`select_context_turns` is the **two-window rule** (§6), pure and independently testable. A turn whose `kind` is in neither set is ignored (forward-compatible: a future kind cannot silently leak into context).

The kind→window grouping is a **domain decision**, expressed as two named frozensets rather than an inline condition, so the criterion is explicit: `DIALOGUE_KINDS` is the operator↔assistant exchange (including a `confirm`, which asks for the operator's decision); `VOLUNTEERED_KINDS` is what the assistant said unprompted.

### §5.2 `redrobin/infrastructure/persistence/chat_history_repo.py`

```python
_MAX_TAIL_LINES = 500  # module constant, not a Setting — a memory safety valve

class ChatHistoryRepo:
    def __init__(self, path: Path) -> None
    def exists(self) -> bool
    def append(self, turn: ChatTurn) -> None
    def tail(self, max_lines: int = _MAX_TAIL_LINES) -> list[ChatTurn]
```

- **`append`** — opens in `"a"` mode, writes `json.dumps(asdict(turn), ensure_ascii=False) + "\n"`. Creates parent dirs if absent. **Single-writer:** only `ChatRecorder` calls it (the same invariant the project already holds for working memory, where the reducer is the sole writer).
- **`tail`** — streams the file with `deque(fh, maxlen=max_lines)` → **O(max_lines) memory**, independent of file size. Reads with `encoding="utf-8", errors="replace"`. Returns turns in file order (oldest → newest).
- **Malformed line** (truncated JSON from an interrupted append, or a missing field) → skipped with a debug log; the rest still parses. **Missing file** → `[]`. Neither raises.

Placement rationale: file I/O belongs in `infrastructure/` (matching the five existing repos). The one counter-precedent, `JournalService`, reads `events.jsonl` directly from the application layer — but that is a *foreign* log written by the events router, whereas the chat log is one we both own and write, which is exactly what a repo encapsulates. It also loads the whole file via `read_text()`, the unbounded pattern this design deliberately avoids.

### §5.3 `redrobin/application/chat/recorder.py`

A bus consumer, structurally identical to `WorkingMemoryReducer`/`Assistant`: `subscribe()` → loop → dispatch → `_close` sentinel exits; exposes `on_started` so the lifespan can await readiness before events flow.

Event → turn mapping (the complete table — nothing else is recorded):

| Bus event | Discriminator | `role` | `kind` |
|---|---|---|---|
| `operator.question` | `source == "operator"` | operator | `question` |
| `operator.question` | assistant-authored — **reuse the existing `is_own_proposal_question(payload)`** (`agents/assistant/interpret.py`) rather than re-implementing the `context_refs`/`milestone:` check | assistant | `confirm` |
| `operator.answer` | `source != "operator"` (the assistant's reply) | assistant | `answer` |
| `operator.answer` | `source == "operator"` (the operator's `/y` · `/n`) | operator | `answer` |
| `advisor.recommendation` | `correlation_id` truthy | assistant | `advisory` |
| `advisor.recommendation` | `correlation_id` falsy/absent | assistant | `heads_up` |

The advisory/heads-up discriminator is **the same rule the TUI already renders with** (`correlation_id` present ⇒ reactive) — one conceptual source of truth, so a stored turn and its chat line can never disagree.

`text` composition:
- `question` / `answer` → the payload's message text verbatim.
- `confirm` → the confirm question text.
- `advisory` / `heads_up` → the advisory's parts joined into a compact block (action, why, opsec, conf). The application layer **cannot** reuse the TUI renderer (contract 3), so a small pure module-level helper composes it — deliberately mirroring the operator-facing shape, minus refs (removed 2026-07-24).

Timestamps come from an **injected clock** (`now: Callable[[], datetime] = datetime.now`), matching `capture_lesson(now=…)`, so tests are deterministic.

Never-crash: an `append` failure is logged and swallowed — the loop survives (a full disk must not kill the chat, let alone the assistant). **The turn's `text` is never logged** on failure: it can contain credentials the operator pasted. Log the path and the exception only.

### §5.4 `redrobin/application/chat/history.py`

Builds the injected reader the assistant consumes:

```python
def build_history_reader(
    repo: ChatHistoryRepo, *, dialogue_max: int, volunteered_max: int
) -> Callable[[], str]
```

It reads (`repo.tail()`) → selects (`select_context_turns`) → renders one plain-text block, returning `""` when there is nothing (or on any failure). Returning a **string** makes it a drop-in twin of `summary_reader`, which keeps `agent.py` free of chat types and keeps the rendering unit-testable.

Rendered form — role-labelled, one entry per turn, chronological:

```
[operator] what's my path to DA?
[assistant] Kerberoast svc_sql, then crack offline…
[assistant · advisory] Validate svc_reports against SQL01
why    a quiet bind beats a loud dump
opsec  WARN
conf   high cred, unsure scope
```

### §5.5 Context injection (`agents/assistant/agent.py`)

`build_push_context` and `build_answer_context` each gain `chat_history: str = ""`, emitting a section beside L2/L5:

```
# Conversation so far
<the rendered block, or "(no conversation yet)">
```

The default `""` keeps every existing caller and test valid.

`Assistant` gains `history_reader: Callable[[], str] | None = None` (default None ⇒ current behavior), read inside the **existing** `try/except` that already guards `summary_reader`, so a failing reader degrades to `""` instead of breaking a cycle.

**Which cycles receive it:**

| Cycle | Receives chat history | Why |
|---|---|---|
| Conversational (`run_answer_cycle`) | ✅ | the point of the feature |
| Reactive (`run_cycle`, on a command) | ✅ | so an advisory doesn't repeat what was already said or rejected |
| Proactive (`run_cycle`, lull/material) | ✅ | same reasoning |
| **Interpretation** (`run_interpretation_cycle`) | ❌ | it **writes engagement state**. Feeding it chat would let a casual remark ("creo que obj-2 ya está") drive a state write from conversation instead of beacon evidence. Excluded deliberately. |

### §5.6 Wiring (`api/lifespan_helpers.py`)

In `start_assistant`, gated on `settings.assistant_enabled` (consistent with `build_kb_service` / `build_journal_service` — no cost when the feature is off):

1. Build `ChatHistoryRepo` at `<engagements_dir>/<slug>/chat_history.jsonl`, resolved from `app.state` the same way `start_working_memory` resolves its paths (falling back to the root dir when no engagement is active, so degraded boots still work).
2. Start `ChatRecorder` as a task, awaiting `on_started` before returning (the reducer's pattern) so no early event is missed.
3. Pass `history_reader=build_history_reader(...)` into `Assistant`.
4. Register a close hook that drains the recorder, ordered like the existing hooks.

### §5.7 Settings

Two knobs, following the existing `assistant_*` prefix (`assistant_cooldown_sec`, `assistant_wm_max_items`, …):

| Setting | Env var | Default |
|---|---|---|
| `assistant_chat_dialogue_turns` | `REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS` | `10` |
| `assistant_chat_advisory_turns` | `REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS` | `5` |

## §6 The two-window rule (§5.1, stated precisely)

A single "last N turns" window is **wrong** here: advisories fire per beacon command, so the most recent N entries can be entirely advisories, evicting the operator's question from context — exactly the case the feature exists to serve.

Instead, each group gets a **guaranteed quota**:

1. Take the last `dialogue_max` turns whose `kind ∈ DIALOGUE_KINDS`.
2. Take the last `volunteered_max` turns whose `kind ∈ VOLUNTEERED_KINDS`.
3. Concatenate and **stable-sort by `ts`** → one chronological list (so *"I advised X → you declined"* reads correctly).

Beacon chatter can no longer evict the conversation thread. Note this is **selection**, not per-kind formatting: rendering stays uniform (`turn.text` for every kind), which is what keeps it simple.

Microsecond-precision timestamps make ties effectively impossible; Python's stable sort makes any tie deterministic regardless.

## §7 Definition of Done

- With the assistant enabled (`REDROBIN_ASSISTANT_ENABLED=1`), `chat_history.jsonl` accumulates in the active engagement's directory, one JSON object per turn, across daemon restarts. With the assistant disabled, nothing is recorded and no repo/recorder is built (§5.6).
- All six event→turn mappings (§5.3) record with the correct `role`/`kind`; no other bus event is recorded.
- The assistant's conversational, reactive, and proactive cycles receive a `# Conversation so far` section; the interpretation cycle does **not**.
- The two-window rule holds: with 20 consecutive advisories after a question, that question is still in the injected context.
- Never-crash holds: a failed append does not stop the recorder; a raising/failing reader does not stop a cycle; a malformed line does not break a read; a missing file yields `""`.
- No turn text appears in any log line.
- Both Settings are honoured.
- **5 CI gates green.** Acceptance is **deterministic** (unit + integration) per `development-workflow.md` §5 — no live surface changes, so no operator smoke is required. A live check is nonetheless *useful* (ask a follow-up question and see it answered in context); it is not the gate.

## §8 Test strategy

- **Domain (`test_domain_chat.py`)** — `select_context_turns`: both quotas respected; **the dilution case** (20 advisories + 1 old question ⇒ question present); chronological merge; fewer turns than the cap; empty input; unknown `kind` ignored.
- **Infrastructure (`test_chat_history_repo.py`)** — append→tail round-trip preserving order and fields; missing file ⇒ `[]`; a truncated/garbage line skipped while neighbours parse; `tail` honours `max_lines`; unicode round-trips.
- **Application (`test_chat_recorder.py`)** — each of the six mappings; irrelevant events ignored; a raising `append` does not kill the loop; the injected clock stamps `ts`; no turn text in captured logs (assert on `caplog`).
- **Application (`test_chat_history_reader.py`)** — renders role labels + chronology; `""` when empty; `""` when the repo raises.
- **Assistant (`test_assistant_chat_context.py`)** — `history_reader` output reaches the conversational/reactive/proactive prompts and is **absent** from the interpretation prompt; a raising reader still runs the cycle.
- **Integration (`test_chat_persistence.py`)** — publish the events on a real `EventBus` through a real repo/temp dir and assert the file's contents (mirrors the reducer's integration tests).

## §9 Non-goals

- **No compaction/summarization** of older turns (Claude Code's auto-compact). The full transcript is persisted, so this is purely additive later.
- **No TUI replay** of the scrollback on reconnect — this feature makes it *possible*; rendering it is a separate follow-up (the #28 limitation).
- **No semantic chat search** (`search_chat` over embeddings, à la `JournalService`) — a later addition if retrieval over old conversation is ever needed.
- **No retention/cleanup policy** — engagements are archived/deleted as a unit.
- **No redaction** of the chat log (see §10).
- **No change** to the assistant's reasoning, the gate, the enricher, the TUI, or the wire protocol.

## §10 Risks

- **Thread dilution by beacon chatter** — resolved structurally by the two-window rule (§6), and covered by a dedicated test.
- **Context growth** — bounded by the two caps; the injected slice has a fixed maximum size regardless of engagement length.
- **Secrets on disk.** The chat can contain credentials the operator pasted or discussed. This is **not a new exposure class**: `events.jsonl` in the same directory already stores raw beacon output (credentials included), and the directory is archived/deleted with the engagement. `application/redactor.py` exists if redaction is ever wanted; building it now would be scope creep. Mitigated in the one place we control: turn text is never written to logs.
- **Interrupted append** could leave a partial last line; the malformed-line skip contains it to that single turn.
- **Distinct-layer confusion** — chat vs L5 vs Lessons could blur over time. Mitigated by naming and by this spec's §1: chat = the session thread, L5 = beacon story, Lessons = durable corrections.
