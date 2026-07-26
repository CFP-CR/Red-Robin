# Remove the Synchronous OPSEC Pre-Flight Gate — Design Spec

**Date:** 2026-07-25 · **Branch:** `feat-remove-preflight-gate` (off `dev`) · **Status:** approved (operator, 2026-07-25)
**Follows:** the AI-DLC — brainstorming-phase spec; next `writing-plans` → `auditing-plans` → `subagent-driven-development` → verify → finishing.

Deletes the synchronous pre-flight gate — the feature Red Robin was born as — and keeps only the `.cna` **capture** that feeds the AI assistant. Red Robin becomes purely advisory: it never holds or blocks a command.

---

## §1 Why

**The product moved.** The daemon judges every beacon command (Layer A Sigma → Layer B LLM judge → a verdict of PASS/WARN/BLOCK/ESCALATE). The value today is the **copilot that advises**, not a checker that rules on the operator's commands.

**The gate is fully live and it intercepts the operator's command path.**

> **CORRECTION (2026-07-26).** This section previously claimed the gate was "already toothless — no `alias` override anywhere, `redrobin_gate` is dead code". **That was wrong.** The check behind it grepped only `redrobin.cna` itself and never followed the `include()` statements at `redrobin.cna:453-457`, which load four alias template files:
>
> | File | `alias` overrides | calls to `redrobin_gate` |
> |---|---|---|
> | `aliases/tier1a_gate.sleep` | 16 | 18 |
> | `aliases/tier1b_wrapper.sleep` | 7 | 7 |
> | `aliases/tier2_warn.sleep` | 5 | 5 |
> | **total** | **28** | **30** |
>
> The overrides cover `mimikatz`, `dcsync`, `hashdump`, `psexec`, `jump`, `spawn`, `getsystem`, `inject`, `shell`, `powershell` and more. Tier-1A **hard-blocks** on a DANGER verdict (`berror` + `return false`) — the command never reaches the beacon. So the 2026-07-22 smoke's `mimikatz sekurlsa` **was** technically blocked, not merely discouraged by a console message.
>
> **Consequence for the removal order.** Deleting `POST /v1/verdict/quick` before the aliases leaves them querying a route that no longer exists; the gate then returns `UNAVAILABLE`, which Tier-1A treats as dangerous — hard-blocking 16 commands while telling the operator the daemon is unreachable. **The aliases must therefore be deleted as part of this change, not assumed absent.**

The case for deletion is unchanged and if anything stronger: the gate really does stand between the operator and their C2, on a judgment the operator has already made, with an LLM call in the critical path.

**What it costs to keep.** ~1400 lines across eight application modules, two API surfaces, a Sigma rule pack with hot-reload, and ~90 lines of dead `.cna` code — plus the conceptual weight of a two-tier verdict protocol that every reader of this codebase has to learn before understanding what Red Robin actually does.

## §2 The one non-obvious dependency

`opsec.verdict` is **not only the gate's output — it is the plumbing of the memory system**, by accident.

Because the gate fired on *every* command, the verdict stream was the only event stream carrying per-command operator activity. The reducer piggybacked on it: on every verdict it records an **Episode** (L3), and Episodes are the **only** input to

1. the **L5 summarizer** — *the "story so far" the assistant reads on every cycle*, and
2. the **`LessonPromoter`** (L3→L1 promotion).

The reducer's own comment admits it: *"every verdict (**not only BLOCK**) becomes an Episode so the promoter sees **full operator activity**."* They wanted the activity; the verdict was the vehicle.

The coupling is **accidental, not architectural** — three proofs:
- `docs/architecture-design.md` (§Componente 3) defines L3 as *"operator-tagged + auto-detected **lessons** del engagement actual"*. The gate is not mentioned.
- `Episode`'s docstring says *"one row per `opsec.verdict` event"* — a description of the implementation, not of a memory layer.
- `Episode`'s shape is `(command, verdict, reason)`: the **command** is the substance; `verdict`/`reason` are the gate's opinion bolted on.

So deleting the gate cuts the **vehicle**, not the **need** — and the need is served better by the command stream we are keeping.

## §3 Goal

Delete the gate. Re-source L3/L5 from `beacon.command_input`. Delete the `LessonPromoter`, whose premise dies with the verdict. Leave the capture → enricher → working memory → assistant path untouched. **The tree stays green at every commit** (see §7 ordering).

## §4 Pillars — what this makes *simpler*

This is a deletion, so the pillars are served by subtraction, not by new abstractions.

- **Clarity.** One boundary, stated in one line: **the adapter captures, the core advises.** No verdict tiers, no PASS/WARN/BLOCK/ESCALATE vocabulary, no two-tier protocol to explain. `Episode` stops meaning "a verdict" and starts meaning what its name says — something the operator did.
- **Maintainability.** −8 application modules, −2 API surfaces, −18 test files, −15 rule YAMLs, −1 infrastructure loader, and the `.cna` loses its `alias` overrides (the code most likely to break an operator's command). `application/`'s loose-file count drops from 25 to 17, which also serves the operator's separate refactor.
- **Scalability.** One less LLM call on the operator's critical path (Layer B judged inline, per command). The daemon stops doing synchronous work between the operator pressing enter and the beacon receiving the task.
- **Clean code / not overcomplicated.** The re-source is **one new dispatch branch** in a reducer that already has four, writing a dataclass with **two fewer fields** than today. No new module, no new service, no new event type, no compatibility shim.

## §5 Design

### §5.1 Deleted

**Application (8 modules, ~1350 lines):** `layer_a_engine.py` · `layer_b_engine.py` · `verdict_pipeline.py` · `verdict_pipeline_factory.py` · `scope_enforcer.py` (used only by the pipeline) · `redactor.py` (used only by Layer B) · `rules_lifecycle.py` · `lesson_promoter.py`

**API (2 surfaces):** `verdicts_router.py` (`POST /v1/verdict/quick` — the gate's only endpoint) · `adapter_events_ws.py` (`/v1/adapters/events` exists solely to push `opsec.verdict` back to the adapter)

**Infrastructure:** `rules_loader.py` (Sigma loading + hot-reload)

**Product data:** `rules/` — 15 Sigma/OPSEC YAMLs, plus the "Adding a rule" flow in `CONTRIBUTING.md`

**Domain:** `opsec.py` entirely (`Verdict` + `VerdictAction`) · `rules.py` entirely (`Rule`, `RuleSeverity`, `EventContext`) · the `OpsecVerdictEvent` type in `events.py` and its `Envelope` severity-serialization branch · `PromotedLesson` · `Episode.verdict` and `Episode.reason`. **`events.Severity` is KEPT** — see the trap in §5.5.

**The C2 adapter** (corrected 2026-07-26 — this is substantially larger than first stated): the **four alias template files** `aliases/tier1a_gate.sleep`, `tier1b_wrapper.sleep`, `tier2_warn.sleep`, `popup_beacon.sleep` and their four `include()` lines — **28 `alias` overrides in total, the gate's actual enforcement** · the `redrobin_gate` and `redrobin_query_quick` subs they call · the `heartbeat_5s` branch that prints `[Red Robin] Layer B …` verdicts (and the `/v1/adapters/events` polling feeding it) · the gate capability declarations in **both** `adapters/cobaltstrike/manifest.toml` **and** `adapters/sliver-mock/manifest.toml` (`preflight_block_console`, `preflight_block_tui`, `gate_scope`, `events_consumed`) and their mirror in the `.cna` handshake JSON · the `opsec.verdict` event in `adapters/sliver-mock/scenarios/acme_corp.json` · and the helper-jar sources `RedRobinEventStream.java`, `VerdictEvent.java`, `RedRobinEventStreamTest.java`.

**NOT touched — the capture path:** `on beacon_input`, `on beacon_initial`, `set BEACON_OUTPUT`, `on script_unload`, the `redrobin_emit_*` helpers, the HTTP/escape/token helpers, and `RedRobinHttp.java`.

**Deliberately out of scope:** rebuilding the committed binary `jars/redrobin-aggressor-http-1.0.2.jar`. Its stale verdict classes are inert once nothing calls them; rebuilding a committed binary is separate work and gets its own ticket. The source/binary divergence is recorded rather than hidden.

**Tests:** the 18 gate test files listed in §8.

### §5.2 Survives, untouched

The capture path and everything downstream of it: the `.cna`'s `redrobin_emit_command_input` / `_command_output` / `_beacon_initial` → `POST /v1/events` → `events_router` → the bus → the **enricher** (`beacon.command_output` → `working_memory.deltas`) → the **reducer** → **working memory** → the **assistant** (all four cycles, the chat, the KB, the journal, lessons) and the **TUI** (minus the verdict counts).

**Findings survive.** The gate appended a `Finding` only on BLOCK; the enricher is the real source — `apply_deltas` merges `deltas.findings` with dedup, and the live `vantage` engagement holds **61 findings, all enricher-sourced** (`kind: privilege_analysis` and similar, none of them gate verdicts). Deleting the gate's finding path removes a small extra stream, not the stream.

### §5.3 Changed — the L3/L5 re-source

`Episode` drops the gate's two fields:

```python
@dataclass(frozen=True)
class Episode:
    """One command the operator ran — the raw activity log L5 narrates from."""
    id: int | None
    engagement_id: str
    correlation_id: str
    command: str
    created_at: datetime
```

The reducer gains **one dispatch branch** beside its existing four, replacing `_on_verdict`:

```python
elif etype == "beacon.command_input":
    self._on_command_input(payload)   # records an Episode; no WM mutation
    return True
```

**Why `beacon.command_input` and not `enrichment.completed`:** `command_input` is published by the `.cna` on **every** command, carries `command` + `correlation_id` directly (wire: `bid`, `operator`, `command`, `raw`, `task_id?`, `correlation_id?`), and needs **no LLM**. `enrichment.completed` also carries the command, but only fires when the enricher runs — so in a degraded boot (no `ANTHROPIC_API_KEY`) L5 would go silent, which the gate's deterministic Layer A never did. Choosing `command_input` preserves that robustness.

**The L5 summarizer** keeps its high-water-mark loop unchanged; only `_format` adapts to the narrower Episode (drop the verdict/reason columns). A command sequence is a sufficient — and more honest — basis for "the story so far" than a verdict log: it says what the operator *did*, not what the gate *thought*.

**`episodic.db`'s schema** gains no migration: the two dropped columns are simply no longer written or read. Existing rows keep their values; nothing queries them. (Engagements are per-directory and short-lived, so a migration would be ceremony.)

### §5.4 Changed — the rest

- **`dedup_keys.py`** — drop `opsec.verdict` from `_CORRELATED_TYPES` (leaving `beacon.command_input`/`beacon.command_output`).
- **`lifespan_helpers.py` + `app.py`** — remove `start_rules`, `start_pipeline`, `start_lesson_promoter` and their close hooks. Shutdown loses two steps.
- **`boot_validation.py`** — `ANTHROPIC_API_KEY` is still required, but for the **assistant / enricher / summarizer**, not "for Layer B". Message and rationale updated; the requirement itself is unchanged.
- **TUI** — see §5.5; it is the one operator-visible change.
- **Docs** — `CLAUDE.md` (the gate is no longer "the first feature and the capture hook"), `CONTRIBUTING.md` ("Adding a rule" section), `.claude/rules/wire-protocol.md` (the `opsec.verdict` row), **`.claude/rules/testing.md`** (it names `VerdictAction` as a canonical enum for assertions — that line must drop `VerdictAction` and keep `Severity`), `.claude/rules/architecture.md` if it references the gate, `docs/architecture-design.md` (a status note: the gate was removed, L3 is now command-sourced), and the smoke runbook (the checkpoints that exercise the gate).

### §5.5 Changed — the TUI (the only operator-visible change)

The gate's TUI surface is larger than it looks, because #28 already made most of it vestigial:

| Deleted | Where |
|---|---|
| The four topbar counters (`BLOCK` / `WARN` / `PASS` / `ESCALATE`) | `panels.py: verdict_counts`, `TopBar.update_status` |
| `format_verdict` (dead since #28 — retained only as a test harness), `apply_verdict`, `VERDICT_COLORS` | `panels.py` |
| **The entire `events` reactive** and its `watch_events` — since #28 replaced the Verdicts pane with the Chat pane, `app.events` exists *only* to feed those counters | `app.py` |
| The `opsec.verdict` frame handler | `app.py:213` |
| The `$verdict-block` / `$verdict-warn` / `$verdict-pass` / `$verdict-escalate` tokens | `theme.tcss` |

**Operator decision (2026-07-25): the freed topbar space stays empty.** The header becomes `Red Robin · <engagement> · ● connected`. Putting a new widget there (phase, objective progress, an assistant-state indicator) would be UX design smuggled inside a deletion — it gets its own small effort if it's ever wanted. The counters were actionable *because* something had been blocked; with nothing blocking, counting is decoration.

Untouched: the Chat pane, Sessions, Working Memory, the Inspector, the help overlay, and the command palette.

**⚠️ Implementation trap — there are THREE overlapping severity vocabularies, and only two go:**

| Enum | Values | Home | Consumers | Fate |
|---|---|---|---|---|
| `VerdictAction` | PASS / WARN / BLOCK / ESCALATE | `domain/opsec.py` | the gate only (`verdicts_router`, `layer_a/b_engine`, `verdict_pipeline`, the factory) | **deleted** — the whole `domain/opsec.py` (`Verdict` + `VerdictAction`) goes |
| `RuleSeverity` | INFO / WARN / DANGER | `domain/rules.py` | the Sigma rules (`layer_a_engine`, `rules_loader`) | **deleted** with `domain/rules.py` + `rules_loader.py` |
| `Severity` | INFO / WARN / DANGER / ESCALATE | `domain/events.py` | **the assistant** — `Recommendation.opsec_risk`, `parser.py` | **KEPT.** It only loses one consumer: the deleted `OpsecVerdictEvent.severity` field and its `Envelope` serialization branch |

`chat.py`'s `_RISK_COLORS` is keyed on the **`Severity`** values, so a grep-and-delete pass over `"WARN"` or `"ESCALATE"` would break advisory rendering. **Delete by symbol and by module, never by string match.**

## §6 Explicit non-goals

- **No replacement warning mechanism.** Nothing gets a "soft gate", a confirmation prompt, or a pre-flight advisory. The assistant already speaks reactively on command output; that is the replacement, and it already exists.
- **No `Redactor` rescue.** It only ever protected the Layer B path; the enricher and the assistant never used it, so deleting it with Layer B is not a security regression. The *underlying* question — should command output be redacted before it reaches Anthropic at all? — is **pre-existing and real**, and gets its own ticket rather than riding along here.
- **No episodic.db migration**, no dual-write period, no feature flag to re-enable the gate. If it needs to come back, it comes back from git.
- **No changes to the assistant's reasoning, prompts, cycles, or the chat.**
- **Not the C2-boundary debt (#29).** Deleting the gate removes *some* CS-specific leakage incidentally, but the remaining debt is a separate effort.

## §7 Ordering constraint (the tree stays green)

The re-source must land **before** the deletion, so no commit exists in which L5 has no input:

1. **Add** the `beacon.command_input` → Episode branch (additive; verdicts still also record episodes — a brief double-write that is harmless and untested-for).
2. **Delete** the gate core and its satellites, which removes the verdict path and the double-write with it.
3. **Narrow** `Episode` (drop `verdict`/`reason`) and adapt `_format`.
4. `.cna`, then docs.

Step 3 after step 2 keeps each commit's tests coherent: the fields disappear only once nothing writes them.

## §8 Definition of Done

- No code path can hold, delay, or block a beacon command. `POST /v1/verdict/quick` and `/v1/adapters/events` return 404; the `.cna` has no `alias` override and no gate call.
- `Episode` rows are written from `beacon.command_input`, one per command, with no LLM involved — verifiable in a degraded boot (no `ANTHROPIC_API_KEY`).
- The L5 summary still populates from those episodes, and the assistant still receives it as `l5_summary`.
- The enricher → working-memory → assistant path is byte-for-byte unchanged; findings, credentials, hosts, defenses, objectives, and the chat all still work.
- Every deleted module, route, rule, and test is gone — no orphaned imports, no dead code, no skipped tests left behind.
- **5 CI gates green**, with the suite smaller by the gate's tests and no test weakened to accommodate the deletion.
- **Acceptance is a live operator smoke** (skill `smoke-test`), because the `.cna` is not exercised by CI: run a command that the gate used to BLOCK (`mimikatz sekurlsa`) and confirm it **dispatches immediately** with no interception, while the assistant still advises in the chat and the Working-Memory panel still fills.

## §9 Test strategy

- **Deletion is verified by absence:** the gate's 18 test files are removed with their subjects; a grep-level check that no `opsec.verdict`, `VerdictAction`, `Layer A`, `Layer B`, or `verdict_pipeline` reference survives outside historical docs.
- **New unit test** — the reducer records one Episode per `beacon.command_input`, with the command and correlation_id carried through, and mutates no working memory.
- **New unit test** — the L5 `_format` renders a command-only episode list.
- **Regression (must stay green untouched):** the enricher tests, the reducer's deltas/beacon.initial/milestone branches, every assistant test, the chat-memory tests, and the TUI tests other than the verdict counts. **In particular the advisory-rendering tests** (`format_recommendation` over each `Severity`) — they are the guard against the §5.5 `WARN`/`ESCALATE` vocabulary trap.
- **New Pilot test** — the topbar renders `Red Robin · <engagement> · ● connected` with no counter section, and the app exposes no `events` reactive.
- **Degraded-boot test** — with no `ANTHROPIC_API_KEY`, publishing a `beacon.command_input` still records an Episode (proves the L5 input no longer depends on an LLM).
- **Live smoke** — the only acceptance for the `.cna` change (§8).

## §10 Risks

- **The `.cna` is not CI-testable, and the adapter edit is the riskiest part of this change** (corrected 2026-07-26 — an earlier draft of this spec wrongly called it small). Removing 28 `alias` overrides *is* surgery on the operator's command path: a mistake there breaks the ability to task a beacon, which is the exact failure the deletion exists to prevent. Mitigations: the adapter is its own task, landed last before docs; the capture hooks are explicitly out of bounds; and the operator smoke is the only acceptance.
- **The removal order is load-bearing, not cosmetic.** Deleting `POST /v1/verdict/quick` before the aliases produces a *worse* state than either endpoint — the aliases query a dead route, read `UNAVAILABLE` as dangerous, and hard-block 16 commands while blaming the daemon. Any future re-sequencing of this plan must keep the adapter change in the same branch as the route deletion.
- **Two adapter test suites are NOT in CI.** `pytest tests/` does not include `adapters/*/tests/`, so the five gates cannot see an adapter regression. Both suites must be run explicitly whenever the adapter changes — this was discovered when a `sliver-mock` replay test went red on the branch without any gate noticing.
- **Losing the console message.** Today a WARN/BLOCK verdict prints in the beacon console — the operator's only in-CS signal from Red Robin. After this change the beacon console is silent and everything comes through the TUI chat. That is the intent (one voice, one surface), but it *is* a behavioral change for an operator who works mostly in the CS window, and worth confirming during the smoke.
- **L5 quality shifts.** Summaries stop mentioning verdict reasons and start narrating commands. Expected and intended, but the operator should eyeball the first few summaries after the change.
- **Losing the deterministic layer.** Layer A caught known-bad patterns without an LLM; post-change, if the assistant is disabled or degraded, nothing comments on a dangerous command. This is the accepted trade — the operator asked for advisory-only — and it is worth stating plainly rather than discovering later.
- **`docs/architecture-design.md` becomes substantially historical.** It describes the gate as the product's core. The spec updates it with a status note rather than rewriting it; a full rewrite is its own effort.
