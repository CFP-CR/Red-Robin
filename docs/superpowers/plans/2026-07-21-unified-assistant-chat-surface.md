# Unified Assistant Chat Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Assistant Chat pane the single surface for all assistant output — reactive + proactive recommendations render as messages in the same stream as answers and confirms — and remove the `#advisor-dock`.

**Architecture:** Presentation-layer only. The assistant already publishes `advisor.recommendation` on the bus; today `advisor.py` renders it into the reserved `#advisor-dock`. We move the (adapted) renderer into `chat.py`, register `advisor.recommendation` on the chat binder so it appends to `#chat-log`, relabel confirms to the one-voice style, cap the log, and delete the dock + `advisor.py`. No application/domain change — `correlation_id` (already in the payload) distinguishes reactive from proactive.

**Tech Stack:** Python 3.11+, Textual (widgets + `theme.tcss`), Rich (`Text`), pytest + pytest-asyncio (Textual `App().run_test()` Pilot harness), `uv`.

## Global Constraints

- **Presentation-layer only** — every change is under `redrobin/tui/` + tests + the smoke runbook. No change to the assistant's reasoning, cycles, events, prompts, or model. Do NOT touch `redrobin/application`, `redrobin/domain`, `redrobin/infrastructure`, or the `.cna`/adapter.
- **False-commit routing is untouched** — `next_operator_message`, the `/y /n` token match, `_pending`, and `resolve_confirm` are not modified. Only the rendered confirm *header* changes.
- **Layered imports** — `chat.py` imports only `redrobin.tui.textual_ui.frames` + rich/textual (never `application`/`infrastructure`/`app`). The frame-registry pattern is preserved (a handler per event type; no dispatch-loop edits).
- **One voice** — all assistant-authored lines are labeled `Red Robin`; a muted one-word tag names the act: reactive `advisory` / proactive `heads-up` / `confirm`. Answers stay plain `Red Robin`. Provenance tag driven by `payload["correlation_id"]` (present → reactive, `None` → proactive).
- **Styling: existing tokens only** — speaker in `_ACCENT` (`#58a6ff`), tags in `_MUTED` (`#8b949e`), the confirm tag in `_CONFIRM` (`#e3b341`), OPSEC risk in its `_RISK_COLORS` severity color. No emoji/badges.
- **5-part card kept in full** (WHAT `→` / WHY `why` / OPSEC `opsec` / `conf` / `refs`) — the WHY is the thesis, never hidden.
- **CI gates (all must pass):** `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy redrobin/domain redrobin/application` · `uv run lint-imports --config importlinter.cfg` · `uv run pytest tests/ -v`. Use `uv run --no-sync ...` when the daemon holds the venv lock; `REDROBIN_ALLOW_DEGRADED=1` for any boot-gated test.
- **Commits:** atomic, conventional (`feat(tui): …`, `refactor(tui): …`, `test(tui): …`, `docs(smoke): …`), **NO AI co-author trailer** (project override; verify `git log` shows none).
- **Live acceptance:** this is a live surface — the operator re-smoke (former dock checkpoints, now against the chat) is the only acceptance for rendering + the WS round-trip; CI cannot exercise it.

## File Structure

- `redrobin/tui/textual_ui/chat.py` — **gains** `format_recommendation` (adapted: `Red Robin · <tag>` header, reads `correlation_id`), `_RISK_COLORS`, `_on_recommendation` handler; **relabels** `format_confirm`; **caps** `#chat-log` with `max_lines`; **registers** `advisor.recommendation` in `register_chat`.
- `redrobin/tui/textual_ui/advisor.py` — **deleted** (its renderer moved to `chat.py`; its dock handler retired).
- `redrobin/tui/textual_ui/app.py` — **removes** the `register_advisor` import + call and the `#advisor-dock` Container from `compose`; docstring de-references the dock.
- `redrobin/tui/textual_ui/theme.tcss` — **removes** the `#advisor-dock` + `#advisor-card` rules.
- `tests/unit/tui_textual/test_chat.py` — gains the recommendation renderer/handler/tag tests + the migrated 5-part test + the confirm-relabel update + a `max_lines` test + a registry-routing Pilot test.
- `tests/unit/tui_textual/test_advisor.py` — **deleted** (advisor.py gone).
- `tests/unit/tui_textual/test_app_layout.py` — drops `#advisor-dock` from the region check; deletes `test_advisor_dock_is_hidden`.
- `tests/unit/tui_textual/test_frames.py` — **UNCHANGED** (it registers its own handler on a fresh `Registry`; it does not depend on `advisor.py`).
- `scripts/smoke/rr-assistant-smoke-runbook.md` — checkpoints A, B, D, F, G, H + the header reworded from `#advisor-dock`/"card" to the Chat pane.
- `scripts/smoke/rr-assistant-smoke-fixture.ps1` — the line-88 `Write-Host` hint reworded off `#advisor-dock` (audit MINOR-2).

---

### Task 1: chat.py — render recommendations + relabel confirm + cap the log (additive)

Purely additive to `chat.py` and its tests. Does NOT register the handler on the app registry, does NOT touch `app.py`/`theme.tcss`/`advisor.py` — so `advisor.py` + `test_advisor.py` stay green (the dock still works). Deliverable: `chat.py` can render a recommendation (5 parts + provenance tag), confirms carry the one-voice label, and `#chat-log` is memory-bounded.

**Files:**
- Modify: `redrobin/tui/textual_ui/chat.py` (add `_RISK_COLORS`, `format_recommendation`, `_on_recommendation`; relabel `format_confirm`; `max_lines` on `#chat-log`; docstring)
- Test: `tests/unit/tui_textual/test_chat.py`

**Interfaces:**
- Consumes: the `advisor.recommendation` payload shape — `{suggested_action, rationale, opsec_risk, confidence, references, correlation_id}` (`correlation_id` is a `"<beacon>:<command>"` string for reactive, `None` for proactive). The existing `_chat(app)` helper (returns the `ChatPane` at `#events`, or `None` pre-mount).
- Produces: `format_recommendation(payload: dict) -> rich.text.Text` and `_on_recommendation(app, payload: dict) -> None` (used by Task 2's `register_chat`).

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/tui_textual/test_chat.py`; also add `format_recommendation` to the existing `from redrobin.tui.textual_ui.chat import (...)` block)

```python
def test_format_recommendation_reactive_has_five_parts_and_advisory_tag():
    payload = {
        "suggested_action": "Validate svc_reports vs SQL01",
        "rationale": "a quiet bind beats a loud dump",
        "opsec_risk": "WARN",
        "confidence": "high cred, unsure scope",
        "references": ["vault/mssql.md#chunk_0"],
        "correlation_id": "B-7:type db_backup.config",  # present -> reactive
    }
    s = format_recommendation(payload).plain
    assert "Red Robin" in s
    assert "advisory" in s and "heads-up" not in s        # reactive tag
    assert "Validate svc_reports" in s                     # WHAT
    assert "quiet bind" in s                               # WHY
    assert "WARN" in s                                     # OPSEC
    assert "unsure scope" in s                             # confidence
    assert "vault/mssql.md#chunk_0" in s                   # refs
    assert "nothing dispatched" in s


def test_format_recommendation_proactive_has_heads_up_tag():
    payload = {
        "suggested_action": "Baseline the host defences",
        "rationale": "no command ran; a lull is a good time to check EDR",
        "opsec_risk": "INFO",
        "confidence": "medium",
        "references": [],
        "correlation_id": None,  # None -> proactive
    }
    s = format_recommendation(payload).plain
    assert "Red Robin" in s
    assert "heads-up" in s and "advisory" not in s
    assert "(none)" in s  # empty refs render "(none)"


@pytest.mark.asyncio
async def test_on_recommendation_appends_to_chat_log():
    from redrobin.tui.textual_ui.chat import _on_recommendation

    async with _ChatHarness().run_test() as pilot:
        _on_recommendation(
            pilot.app,
            {
                "suggested_action": "x", "rationale": "y", "opsec_risk": "INFO",
                "confidence": "z", "references": [], "correlation_id": "B:whoami",
            },
        )
        assert len(pilot.app.query_one("#chat-log", RichLog).lines) >= 1


@pytest.mark.asyncio
async def test_chat_log_has_max_lines_cap():
    async with _ChatHarness().run_test() as pilot:
        log = pilot.app.query_one("#chat-log", RichLog)
        assert log.max_lines == 2000
```

Also **replace** the existing `test_format_confirm_shows_yn_affordance` body's first assertion (it currently asserts `"CONFIRM" in s`, which the relabel breaks):

```python
def test_format_confirm_shows_yn_affordance():
    t = format_confirm({"text": "Objective DA -> COMPLETE?", "context_refs": ["milestone:obj1"]})
    s = t.plain
    assert "Red Robin" in s and "confirm" in s   # one-voice label (was "CONFIRM")
    assert "Objective DA -> COMPLETE?" in s
    assert "/y" in s.lower() and "/n" in s.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v`
Expected: FAIL — `ImportError`/`cannot import name 'format_recommendation'`, and `test_format_confirm_shows_yn_affordance` fails on the `"Red Robin"` assertion.

- [ ] **Step 3: Implement in `chat.py`** — add `_RISK_COLORS` beside the existing tokens (after the `_CONFIRM` line):

```python
_RISK_COLORS = {"INFO": "#3fb950", "WARN": "#e3b341", "DANGER": "#f85149", "ESCALATE": "#58a6ff"}
```

Add `format_recommendation` (place it beside `format_answer`/`format_confirm`):

```python
def format_recommendation(payload: dict[str, Any]) -> Text:
    """The 5-part advisory as a chat message (one voice). The provenance tag is
    driven by correlation_id: reactive (a command triggered it) carries
    '<beacon>:<command>'; proactive (self-triggered) carries None -> 'heads-up'."""
    action = str(payload.get("suggested_action") or "")
    rationale = str(payload.get("rationale") or "")
    risk = str(payload.get("opsec_risk") or "INFO")
    confidence = str(payload.get("confidence") or "")
    references = payload.get("references") or []
    tag = "advisory" if payload.get("correlation_id") else "heads-up"
    t = Text()
    t.append("Red Robin", style=f"bold {_ACCENT}")
    t.append(f" · {tag}", style=_MUTED)
    t.append("  (nothing dispatched)\n", style=_MUTED)
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
```

Replace `format_confirm` with the one-voice label (behavior/affordance unchanged):

```python
def format_confirm(payload: dict[str, Any]) -> Text:
    t = Text()
    t.append("Red Robin", style=f"bold {_ACCENT}")
    t.append(" · confirm", style=f"bold {_CONFIRM}")
    t.append("  ")
    t.append(str(payload.get("text") or ""))
    t.append("   [reply /y or /n]", style=_MUTED)
    return t
```

Add the handler (place it beside `_on_answer`/`_on_question`):

```python
def _on_recommendation(app: Any, payload: dict[str, Any]) -> None:
    pane = _chat(app)
    if pane is not None:
        pane.append_line(format_recommendation(payload))
```

Cap the log — change the `RichLog(...)` line in `ChatPane.compose`:

```python
        log = RichLog(
            id="chat-log", highlight=False, markup=False, wrap=True, min_width=0, max_lines=2000
        )
```

Add `"format_recommendation"` to `__all__`. Update the module docstring line that reads `Renderers are pure Rich (unit-testable, like advisor.format_recommendation).` to `Renderers are pure Rich (unit-testable).` (advisor.py is deleted in Task 2). Also update the `_chat` helper's inline comment `# pre-mount — mirror advisor._on_recommendation's guard (never-crash)` to `# pre-mount — never-crash guard (mirrors _on_answer/_on_question)` so no comment references the deleted `advisor.py` (audit MINOR-1).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v`
Expected: PASS (all chat tests, including the updated confirm test).

- [ ] **Step 5: Run the affected gates**

Run: `uv run --no-sync ruff check redrobin/tui tests/unit/tui_textual && uv run --no-sync ruff format --check redrobin/tui tests/unit/tui_textual && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: `All checks passed!` · formatted · `Contracts: 4 kept, 0 broken.`

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/chat.py tests/unit/tui_textual/test_chat.py
git commit -m "feat(tui): render assistant recommendations + relabel confirm in the chat"
```

---

### Task 2: Route recommendations to the chat + remove the advisor dock

Flips the registration to the chat handler, removes the dock (compose + tcss), deletes `advisor.py` + `test_advisor.py`, fixes the layout test, and rewords the smoke runbook. Deliverable: a single unified assistant stream; no dock anywhere.

**Files:**
- Modify: `redrobin/tui/textual_ui/chat.py` (register `advisor.recommendation` in `register_chat`)
- Modify: `redrobin/tui/textual_ui/app.py` (drop the `register_advisor` import + call + the `#advisor-dock` Container; docstring)
- Modify: `redrobin/tui/textual_ui/theme.tcss` (drop `#advisor-dock` + `#advisor-card`)
- Delete: `redrobin/tui/textual_ui/advisor.py`, `tests/unit/tui_textual/test_advisor.py`
- Modify: `tests/unit/tui_textual/test_app_layout.py` (drop the dock region + the hidden test)
- Modify: `tests/unit/tui_textual/test_chat.py` (add the registry-routing Pilot test)
- Modify: `scripts/smoke/rr-assistant-smoke-runbook.md` (reword the dock checkpoints) + `scripts/smoke/rr-assistant-smoke-fixture.ps1` (line-88 hint)
- Modify: `redrobin/tui/textual_ui/chat.py` `_chat` comment (audit MINOR-1) — folded into Task 1 Step 3

**Interfaces:**
- Consumes: `_on_recommendation` + `format_recommendation` from Task 1; the module-level `REGISTRY` + `has_handler`/`dispatch` from `frames.Registry`.
- Produces: nothing downstream (final task).

- [ ] **Step 1: Write the failing test** (append to `tests/unit/tui_textual/test_chat.py`)

```python
@pytest.mark.asyncio
async def test_recommendation_routes_to_chat_and_dock_is_gone():
    # The app REGISTRY now routes advisor.recommendation to the chat log, and the
    # #advisor-dock no longer exists in the tree.
    from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp

    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        assert not app.query("#advisor-dock")                 # dock removed
        assert REGISTRY.has_handler("advisor.recommendation")  # chat now handles it
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "advisor.recommendation",
                "payload": {
                    "suggested_action": "quiet baseline check", "rationale": "why",
                    "opsec_risk": "INFO", "confidence": "high", "references": [],
                    "correlation_id": "B:whoami",
                },
            },
        )
        await pilot.pause()
        assert len(app.query_one("#chat-log", RichLog).lines) >= 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py::test_recommendation_routes_to_chat_and_dock_is_gone -v`
Expected: FAIL — `#advisor-dock` still exists (`app.query("#advisor-dock")` is truthy), so the first assertion fails; the recommendation still routes to the dock handler, so `#chat-log` is empty.

- [ ] **Step 3: Register the chat handler** — in `chat.py`, add the third line to `register_chat`:

```python
def register_chat(registry: Registry) -> None:
    registry.register("operator.answer")(_on_answer)
    registry.register("operator.question")(_on_question)
    registry.register("advisor.recommendation")(_on_recommendation)
```

- [ ] **Step 4: Remove the dock from `app.py`** — delete the import (line 24) `from redrobin.tui.textual_ui.advisor import register_advisor`; delete the binder block (the comment lines + `register_advisor(REGISTRY)`, currently around lines 44-46); delete the compose line `yield Container(id="advisor-dock")  # reserved seam (hidden via .tcss)`. In the module docstring, change `topbar + three panes + reserved advisor dock + footer` to `topbar + three panes + footer`. (Keep the `Container` import — `#sessions`/`#memory` still use it.)

- [ ] **Step 5: Remove the dock styling** — in `theme.tcss`, delete the whole `#advisor-dock { ... }` and `#advisor-card { ... }` blocks (the comment + both rules, currently lines 46-59).

- [ ] **Step 6: Delete `advisor.py` and its test**

```bash
git rm redrobin/tui/textual_ui/advisor.py tests/unit/tui_textual/test_advisor.py
```

- [ ] **Step 7: Fix `test_app_layout.py`** — reword the comment above the loop from `# The three panes, the topbar, and the reserved advisor dock all exist.` to `# The three panes and the topbar all exist.` (audit MINOR-3), and remove `"#advisor-dock"` from the region tuple so it reads:

```python
        # The three panes and the topbar all exist.
        for selector in ("#topbar", "#sessions", "#events", "#memory"):
            assert app.query(selector), f"missing region {selector}"
```

and delete the `test_advisor_dock_is_hidden` test entirely (the whole `async def test_advisor_dock_is_hidden(): ...` block).

- [ ] **Step 8: Run the full TUI + frames test suite to verify green**

Run: `uv run --no-sync pytest tests/unit/tui_textual/ -v`
Expected: PASS — the new routing test passes; `test_frames.py` (self-contained registry test) still passes; no import error from the deleted `advisor.py`.

- [ ] **Step 9: Reword the smoke runbook + fixture** — in `scripts/smoke/rr-assistant-smoke-fixture.ps1` (line ~88), change `Write-Host "Expect within a few seconds of each step: an advisor card in #advisor-dock" -ForegroundColor Cyan` to `Write-Host "Expect within a few seconds of each step: an advisory in the Chat pane (Red Robin · advisory)" -ForegroundColor Cyan` (audit MINOR-2). Then in `scripts/smoke/rr-assistant-smoke-runbook.md`, apply these exact edits (the dock is gone; advisories now render inline in the Chat pane):
  - **Header (line ~3):** `The live surface (the \`#advisor-dock\` card + the Chat pane over a real CS beacon +` → `The live surface (the assistant's advisories + confirms + answers in the Chat pane over a real CS beacon +`
  - **Checkpoint A:** `an advisor card appears** in \`#advisor-dock\` within a few seconds (the dock reveals itself; it was hidden before). It appears *reactively*` → `an advisory appears** in the Chat pane within a few seconds, labeled \`Red Robin · advisory\`. It appears *reactively*` (drop the "dock reveals itself; it was hidden before" clause).
  - **Checkpoint B:** `the card has all 5 parts:` → `the advisory has all 5 parts:`
  - **Checkpoint D:** `the card says advisory / nothing` → `the advisory line says nothing`
  - **Checkpoint F:** `does NOT produce a duplicate card` → `does NOT produce a duplicate advisory`
  - **Checkpoint G:** `NO advisor cards appear (silent),` → `NO advisories appear in the Chat pane (silent),`
  - **Checkpoint H:** `*proactive* \`advisor.recommendation\` card appears in \`#advisor-dock\` with NO` → `*proactive* advisory appears in the Chat pane, labeled \`Red Robin · heads-up\`, with NO`

- [ ] **Step 10: Run all 5 CI gates**

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy redrobin/domain redrobin/application && uv run --no-sync lint-imports --config importlinter.cfg && REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: ruff `All checks passed!` · format ok · mypy `Success` · import-linter `4 kept, 0 broken` · pytest all pass (net −3 tests from the deleted `test_advisor.py`, plus the new chat tests).

- [ ] **Step 11: Commit**

```bash
git add -A redrobin/tui/textual_ui/ tests/unit/tui_textual/ scripts/smoke/rr-assistant-smoke-runbook.md scripts/smoke/rr-assistant-smoke-fixture.ps1
git commit -m "refactor(tui): unify assistant advisories into the chat; remove the advisor dock"
```

---

## Self-Review

**1. Spec coverage:**
- §4.1 rendering (5-part + provenance tag via correlation_id) → Task 1 Step 3. ✓
- §4.2 layout (remove dock Container + tcss) → Task 2 Steps 4-5. ✓
- §4.3 registry (register on chat; delete advisor.py + `register_advisor`) → Task 2 Steps 3, 4, 6. ✓
- §4.4 confirm relabel → Task 1 Step 3 + test update. ✓
- §3/DoD `max_lines` cap → Task 1 Step 3 + `test_chat_log_has_max_lines_cap`. ✓
- §6 DoD "no dangling reference" → Task 2 (delete advisor.py + test_advisor.py; fix test_app_layout; test_frames verified unaffected). ✓
- §6 DoD runbook A/B/D/F/G/H + header → Task 2 Step 9. ✓
- §7 test strategy (tag reactive/proactive, confirm label, `_on_recommendation`, Pilot no-dock + arrival order) → Task 1 + Task 2 tests. ✓ (Note: the "rec does not disturb `#chat-input`" Pilot assertion from §7 is implicitly covered — `append_line` writes only to `#chat-log`; if a reviewer wants it explicit, add one assertion to `test_recommendation_routes_to_chat_and_dock_is_gone`.)

**2. Placeholder scan:** No TBD/TODO; every code + test step shows complete code; every command shows expected output. ✓

**3. Type consistency:** `format_recommendation(payload: dict) -> Text` and `_on_recommendation(app, payload: dict) -> None` used identically in Task 1 (definition) and Task 2 (registration). `register_chat(registry)` signature unchanged (one added line). `RichLog(..., max_lines=2000)` — `max_lines` is a real Textual RichLog constructor arg. ✓
