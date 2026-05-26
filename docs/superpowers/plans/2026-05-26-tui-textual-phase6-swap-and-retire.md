# TUI Textual Redesign — Phase 6 (FINAL): #156 WM-seed + entrypoint swap + retire legacy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the redesign — seed the working-memory scope from `engagement.yaml` (#156, fixes the live `in/out (none)`), swap the `redrobin tui` entrypoint to the Textual app, and **delete the now-dead legacy prompt_toolkit client** (modules + tests) — "legacy is legacy, no dead code."

**Architecture:** Three independent changes + cleanup. (1) #156: a pure `apply_engagement_scope(wm, engagement)` (application layer, mypy-gated) projects the engagement's `client`/`in_scope`/`out_of_scope` onto the `WorkingMemory`, wired in at engagement creation AND daemon engagement-load (refresh). (2) Entrypoint swap: `redrobin/cli_helpers/tui_cmd.py:run_tui` launches `RedRobinApp(ws_url=…, token=…).run()` instead of the legacy app. (3) Retire: with the entrypoint swapped, **nothing** outside the legacy files + their tests imports the legacy modules (verified by grep — the Textual client is fully self-contained, reuses NONE of `redrobin/tui/*.py`), so the entire legacy client + its tests are deleted; only `redrobin/tui/__init__.py` (package marker) + `redrobin/tui/textual_ui/` remain.

**Tech Stack:** Python 3.11, `dataclasses.replace`, Textual 8.2.7 (`RedRobinApp(ws_url=, token=).run()`), FastAPI lifespan, pytest + `uv run --no-sync`, `git rm`.

---

## Constraints (hard — every task)

- **Branch:** `tui/phase-6-swap-and-retire` off `dev-juan` (`633d6d1`). This phase **touches the daemon** (#156 in `api`/`application`) — its smoke needs a live engagement with scope in `engagement.yaml`.
- **Tests:** `uv run --no-sync pytest ...`. Strict asyncio. **Deleting the legacy tests REDUCES the suite count — that is intended;** the gate requires GREEN, not a specific count.
- **Lint/format/type gates (CI on `.`):** `ruff check .`, `ruff format --check .`, **mypy `redrobin/domain redrobin/application`** (so `apply_engagement_scope` + the `engagement_service` wiring ARE mypy-checked — keep them clean), import-linter (4 contracts), pytest. No `# noqa: BLE001`. Imports top-of-file.
- **Commits:** atomic, conventional subject, **no AI/Claude co-author trailer**.
- **Verified against the real code (while writing this plan):**
  - `Engagement` (`redrobin/application/engagement_loader.py`) has fields `slug, name, in_scope, out_of_scope, summary, client` — **NO `rules_of_engagement`, NO `objectives`**. So #156 seeds only `client` + `scope_in_scope`(←`in_scope`) + `scope_out_of_scope`(←`out_of_scope`). (ROE/objectives aren't in `engagement.yaml`'s schema — out of #156 scope.)
  - `WorkingMemory` (`redrobin/domain/memory.py`) is a **mutable** dataclass with `client`, `scope_in_scope`, `scope_out_of_scope` (+ findings/hosts/sessions/…). `dataclasses.replace` gives a copy with only those changed.
  - `start_working_memory` (`redrobin/api/lifespan_helpers.py:105`) computes `active_engagement = getattr(app.state, "engagement", None)` (line 136) BEFORE the seed at line 159-160 — so the loaded `Engagement` is available to seed from.
  - `cmd_tui` (`redrobin/cli.py:104`) resolves the token (`resolve_token(args.token)`) then calls `run_tui(connect_ws=, connect_http=, token=)`. `run_tui` (`tui_cmd.py:28`) currently launches the legacy app.
  - `RedRobinApp.__init__` accepts `*, start_worker=True, ws_url=None, token=None` (Phase 1). The Textual client is a **read-only WS observer** — it does NOT POST commands, so `connect_http` is unused by it.
  - **grep proof (deletion safety):** the ONLY importers of legacy `redrobin/tui/{app,dispatch,i18n,inspector,key_bindings,slash_commands,snapshot_adapter,state,ws_client,panels}` are: other legacy `redrobin/tui/*.py` files, `cli_helpers/tui_cmd.py` (swapped in Task 2), legacy tests, and doc snippets. `redrobin/tui/textual_ui/` imports NONE of them. No daemon/api/application/domain/other-cli importer exists. `redrobin/tui/__init__.py` is empty (keep it).
  - ruff uses defaults (no `select` in pyproject) → unused-argument (`ARG`) is NOT enabled → `run_tui` keeping an unused `connect_http` param is lint-clean.

---

## File Structure

- **Create** `redrobin/application/working_memory_seed.py` — pure `apply_engagement_scope(wm, engagement) -> WorkingMemory` (#156).
- **Modify** `redrobin/application/engagement_service.py` — seed scope at engagement creation (use the helper).
- **Modify** `redrobin/api/lifespan_helpers.py` — seed/refresh scope at daemon engagement-load (use the helper).
- **Modify** `redrobin/cli_helpers/tui_cmd.py` — `run_tui` launches the Textual app.
- **Delete** the legacy client (11 module files) + the legacy tests (10 files / the `tests/unit/tui/` dir).
- **Create** `tests/unit/application/test_working_memory_seed.py`, `tests/unit/test_tui_entrypoint.py`.

---

## Task 1: #156 — seed WorkingMemory scope from the engagement

**Files:**
- Create: `redrobin/application/working_memory_seed.py`
- Modify: `redrobin/application/engagement_service.py` (the `WorkingMemory(...)` save at ~line 91)
- Modify: `redrobin/api/lifespan_helpers.py` (the seed at ~line 159)
- Test: `tests/unit/application/test_working_memory_seed.py`

- [ ] **Step 1: Write the failing test** — create `tests/unit/application/test_working_memory_seed.py`:

```python
from redrobin.application.engagement_loader import Engagement
from redrobin.application.working_memory_seed import apply_engagement_scope
from redrobin.domain.memory import WorkingMemory


def test_apply_engagement_scope_sets_client_and_scope():
    wm = WorkingMemory(engagement_id="e2e", current_phase="recon")
    eng = Engagement(
        slug="e2e",
        name="E2E",
        in_scope=["EC2AMAZ-IJVFMFN", "10.100.0.0/24"],
        out_of_scope=["10.99.0.0/16"],
        client="Acme",
    )
    out = apply_engagement_scope(wm, eng)
    assert out.client == "Acme"
    assert out.scope_in_scope == ["EC2AMAZ-IJVFMFN", "10.100.0.0/24"]
    assert out.scope_out_of_scope == ["10.99.0.0/16"]


def test_apply_engagement_scope_preserves_other_fields():
    wm = WorkingMemory(engagement_id="e2e", current_phase="recon")
    eng = Engagement(slug="e2e", name="E2E", in_scope=["a"], out_of_scope=[], client="X")
    out = apply_engagement_scope(wm, eng)
    assert out.engagement_id == "e2e"
    assert out.current_phase == "recon"  # untouched fields survive


def test_apply_engagement_scope_empty_engagement_is_safe():
    out = apply_engagement_scope(WorkingMemory(engagement_id="e"), Engagement(slug="e", name="E"))
    assert out.client == ""
    assert out.scope_in_scope == []
    assert out.scope_out_of_scope == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/application/test_working_memory_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: ...working_memory_seed`.

- [ ] **Step 3: Implement the helper** — create `redrobin/application/working_memory_seed.py`:

```python
"""Project an engagement's scope onto its working memory (#156).

``engagement.yaml`` is the source of truth for ``client`` + scope; the
working-memory JSON mirrors them so the TUI (Working Memory panel + Inspector)
shows the real scope instead of ``(none)``. Applied at engagement creation and
re-applied at daemon engagement-load. Only ``client``/``scope_in_scope``/
``scope_out_of_scope`` are touched — all other working-memory state is preserved.
"""

from __future__ import annotations

from dataclasses import replace

from redrobin.application.engagement_loader import Engagement
from redrobin.domain.memory import WorkingMemory


def apply_engagement_scope(wm: WorkingMemory, engagement: Engagement) -> WorkingMemory:
    """Return a copy of ``wm`` with ``client`` + scope set from ``engagement``."""
    return replace(
        wm,
        client=engagement.client,
        scope_in_scope=list(engagement.in_scope),
        scope_out_of_scope=list(engagement.out_of_scope),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/application/test_working_memory_seed.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire it into engagement creation (`engagement_service.py`)**

Add the import near the other `from redrobin.application...`/`from redrobin.domain...` imports:

```python
from redrobin.application.working_memory_seed import apply_engagement_scope
```

Replace the working-memory init (currently around line 91):

```python
        WorkingMemoryRepo(path / "working_memory.json").save(
            WorkingMemory(engagement_id=slug, client=client)
        )
```

with (seed scope from the just-built `eng`):

```python
        WorkingMemoryRepo(path / "working_memory.json").save(
            apply_engagement_scope(WorkingMemory(engagement_id=slug), eng)
        )
```

- [ ] **Step 6: Wire it into daemon engagement-load (`lifespan_helpers.py`)**

Add the import at the top of `redrobin/api/lifespan_helpers.py`:

```python
from redrobin.application.working_memory_seed import apply_engagement_scope
```

Replace the seed block (currently around line 156-160):

```python
    # Seed an empty working memory file if none exists so the reducer's
    # synchronous load → mutate → save cycle has something to read on the
    # first event.
    if not working_memory_repo.exists():
        working_memory_repo.save(WorkingMemory(engagement_id=engagement_id))
```

with (when an engagement is active, seed/refresh client+scope from it — #156; fixes the live `(none)`; degraded mode keeps the old behavior):

```python
    # Seed working memory. With an active engagement, project its client + scope
    # onto the WM (#156) so the TUI shows real scope, not (none) — refreshed on
    # every load since engagement.yaml is the source of truth. Degraded mode (no
    # engagement) just seeds an empty WM if missing, preserving prior behavior.
    if active_engagement is not None:
        wm = (
            working_memory_repo.load()
            if working_memory_repo.exists()
            else WorkingMemory(engagement_id=engagement_id)
        )
        working_memory_repo.save(apply_engagement_scope(wm, active_engagement))
    elif not working_memory_repo.exists():
        working_memory_repo.save(WorkingMemory(engagement_id=engagement_id))
```

- [ ] **Step 7: Run the affected suites + mypy**

Run: `uv run --no-sync pytest tests/unit/application/test_working_memory_seed.py -v && uv run --no-sync mypy redrobin/domain redrobin/application && uv run --no-sync pytest tests/ -q -k "engagement or lifespan or working_memory"`
Expected: the seed tests pass; mypy `Success`; the engagement/lifespan/WM tests still pass. *(If the daemon-boot integration tests need a scope in their fixture engagement, that is exercised by the operator smoke; the pure-function tests are the unit guarantee.)*

- [ ] **Step 8: Lint + format + commit**

```bash
uv run --no-sync ruff check redrobin/application/working_memory_seed.py redrobin/application/engagement_service.py redrobin/api/lifespan_helpers.py tests/unit/application/test_working_memory_seed.py
uv run --no-sync ruff format redrobin/application/working_memory_seed.py redrobin/application/engagement_service.py redrobin/api/lifespan_helpers.py tests/unit/application/test_working_memory_seed.py
git add redrobin/application/working_memory_seed.py redrobin/application/engagement_service.py redrobin/api/lifespan_helpers.py tests/unit/application/test_working_memory_seed.py
git commit -m "feat(#156): seed working-memory scope from engagement.yaml"
```

---

## Task 2: Swap the `redrobin tui` entrypoint to the Textual app

**Files:**
- Modify: `redrobin/cli_helpers/tui_cmd.py`
- Test: `tests/unit/test_tui_entrypoint.py` (create)

- [ ] **Step 1: Write the failing test** — create `tests/unit/test_tui_entrypoint.py`:

```python
def test_run_tui_launches_the_textual_app(monkeypatch):
    captured = {}

    class _FakeApp:
        def __init__(self, *, ws_url=None, token=None, **kwargs):
            captured["ws_url"] = ws_url
            captured["token"] = token

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr("redrobin.tui.textual_ui.app.RedRobinApp", _FakeApp)

    from redrobin.cli_helpers.tui_cmd import run_tui

    rc = run_tui(connect_ws="ws://127.0.0.1:7777/v1/tui", connect_http="http://x", token="TOK")

    assert rc == 0
    assert captured["ws_url"] == "ws://127.0.0.1:7777/v1/tui"
    assert captured["token"] == "TOK"
    assert captured["ran"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/test_tui_entrypoint.py -v`
Expected: FAIL — `run_tui` still imports/launches the legacy app (`redrobin.tui.app`), so the `_FakeApp` (patched on `textual_ui.app.RedRobinApp`) is never constructed (`captured` empty → KeyError/assert fail).

- [ ] **Step 3: Swap `run_tui`** — replace the body of `run_tui` in `redrobin/cli_helpers/tui_cmd.py` (keep `resolve_token` as-is — `cli.py` still calls it). The `run_tui` function becomes:

```python
def run_tui(
    *,
    connect_ws: str,
    connect_http: str,
    token: str | None,
) -> int:
    # The Textual client is a read-only WS observer — it consumes the daemon's
    # /v1/tui stream and never POSTs commands, so connect_http is unused here
    # (kept in the signature for CLI compatibility).
    from redrobin.tui.textual_ui.app import RedRobinApp

    RedRobinApp(ws_url=connect_ws, token=token).run()
    return 0
```

Also update the module docstring's "just launches the textual app" line if it references the legacy client (it already says "textual app" — leave it).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/test_tui_entrypoint.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + format + commit**

```bash
uv run --no-sync ruff check redrobin/cli_helpers/tui_cmd.py tests/unit/test_tui_entrypoint.py
uv run --no-sync ruff format redrobin/cli_helpers/tui_cmd.py tests/unit/test_tui_entrypoint.py
git add redrobin/cli_helpers/tui_cmd.py tests/unit/test_tui_entrypoint.py
git commit -m "feat(tui): swap 'redrobin tui' entrypoint to the Textual app"
```

---

## Task 3: Retire the legacy prompt_toolkit client (delete dead code + tests)

**Files (DELETE):**
- Legacy modules: `redrobin/tui/app.py`, `dispatch.py`, `i18n.py`, `inspector.py`, `key_bindings.py`, `slash_commands.py`, `snapshot_adapter.py`, `state.py`, `ws_client.py`, `panels/` (`working_memory.py` + `__init__.py`), `labels/` (`en.toml` + `es.toml` — i18n data, read ONLY by the deleted `i18n.py` via `LABELS_DIR`; verified no other reader)
- One stale doc reference to fix: `redrobin/domain/memory.py` `working_memory_to_snapshot` docstring names the deleted `snapshot_adapter` (Step 2 fixes it).
- Legacy tests: `tests/unit/test_i18n.py`, `tests/unit/test_inspector.py`, `tests/unit/test_slash_commands.py`, `tests/unit/test_tui_panels.py`, `tests/integration/test_tui_full_journey.py`, and the whole `tests/unit/tui/` dir (`test_dispatch.py`, `test_key_bindings.py`, `test_snapshot_adapter.py`, `test_state.py`, `test_ws_client.py`, `__init__.py`)
- **KEEP:** `redrobin/tui/__init__.py` (empty package marker for `textual_ui/`) and the entire `redrobin/tui/textual_ui/` + `tests/unit/tui_textual/`.

- [ ] **Step 1: Confirm `tests/unit/tui/` contains only legacy tests**

Run: `ls redrobin/tui/ redrobin/tui/panels/ redrobin/tui/labels/ tests/unit/tui/`
Expected: `redrobin/tui/` = `__init__.py`, `textual_ui/`, `panels/`, `labels/`, + the 9 legacy `.py` files; `panels/` = `__init__.py` + `working_memory.py`; `labels/` = `en.toml` + `es.toml`; `tests/unit/tui/` = `__init__.py` + the 5 legacy `test_*.py` (test_dispatch/test_key_bindings/test_snapshot_adapter/test_state/test_ws_client). If anything unexpected appears (e.g. a non-legacy file in `tests/unit/tui/`), STOP and report.

- [ ] **Step 2: Delete the legacy modules + the `panels/` package + the `labels/` i18n data, and fix the stale docstring**

```bash
git rm redrobin/tui/app.py redrobin/tui/dispatch.py redrobin/tui/i18n.py \
       redrobin/tui/inspector.py redrobin/tui/key_bindings.py \
       redrobin/tui/slash_commands.py redrobin/tui/snapshot_adapter.py \
       redrobin/tui/state.py redrobin/tui/ws_client.py
git rm -r redrobin/tui/panels redrobin/tui/labels
```

Then fix the now-stale docstring in `redrobin/domain/memory.py` (the `working_memory_to_snapshot` docstring names the just-deleted adapter). Change:

```python
    """Serialize a :class:`WorkingMemory` to the WS ``working_memory_snapshot``
    payload dict — the inverse of
    ``redrobin.tui.snapshot_adapter.working_memory_from_snapshot``.
```

to:

```python
    """Serialize a :class:`WorkingMemory` to the WS ``working_memory_snapshot``
    payload dict (consumed directly by the Textual TUI client).
```

(domain/memory.py is mypy-gated, but this is a docstring-only change — no type impact. Keep the rest of the docstring as-is.)

- [ ] **Step 3: Delete the legacy tests**

```bash
git rm tests/unit/test_i18n.py tests/unit/test_inspector.py \
       tests/unit/test_slash_commands.py tests/unit/test_tui_panels.py \
       tests/integration/test_tui_full_journey.py
git rm -r tests/unit/tui
```

- [ ] **Step 4: Verify nothing references the deleted modules + the full suite is green**

```bash
uv run --no-sync ruff check .          # F401/F821 would catch any orphaned import/reference
uv run --no-sync lint-imports --config importlinter.cfg
uv run --no-sync pytest tests/ -q
```
Expected: ruff clean; import-linter `4 kept, 0 broken`; pytest GREEN. **The suite count drops** (legacy tests removed) — intended. If any module still imports a deleted file (it should not — verified by grep), ruff/pytest will surface it; fix by removing the stale import (do NOT resurrect the deleted file).

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor(tui): retire the legacy prompt_toolkit client (dead after the Textual swap)"
```

---

## Task 4: Full-suite gate + docs (redesign COMPLETE)

**Files:**
- Modify: `docs/superpowers/TODO.md`

- [ ] **Step 1: Run the exact CI gates locally**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
uv run --no-sync pytest tests/ -q
```
Expected: all green. mypy covers the #156 helper + engagement_service wiring.

- [ ] **Step 2: Mark Phase 6 done + the redesign complete in `docs/superpowers/TODO.md`**

Change the Phase 6 line to `- [x]` and update the "Now / Next" to declare the TUI Textual redesign **COMPLETE** (all 6 phases merged; legacy retired). One-line outcome on the Phase 6 entry (fill `M` = final suite count):

```markdown
- [x] **Phase 6 — #156 + entrypoint swap + retire legacy** ✅ — `apply_engagement_scope` seeds WM client+scope from `engagement.yaml` at creation + daemon-load (#156, fixes live `(none)`); `redrobin tui` launches the Textual app; the legacy prompt_toolkit client (11 modules + 10 test files) DELETED — no dead code. Plan: [plans/2026-05-26-tui-textual-phase6-swap-and-retire.md](./plans/2026-05-26-tui-textual-phase6-swap-and-retire.md). *(branch `tui/phase-6-swap-and-retire`; M suite green; legacy gone.)*
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/TODO.md
git commit -m "docs: TUI Textual redesign complete (Phase 6 + legacy retired)"
```

---

## Self-Review (completed while writing)

**1. Spec coverage (§2 #156, §3.5 migration):**
- #156 WM-seed → Task 1 (`apply_engagement_scope` + creation + daemon-load wiring). Seeds `client`/`scope_in_scope`/`scope_out_of_scope` (the fields `engagement.yaml` actually has; ROE/objectives are NOT in the `Engagement` model — out of scope, noted). ✓
- Entrypoint swap → Task 2. ✓
- Retire legacy → Task 3 (delete; grep-proven dead). ✓
- "Reused modules" (§3.5 named i18n/dispatcher/snapshot_adapter as reused) — the Textual client turned out **fully self-contained** (reuses none), so there are NO shared modules to preserve; all legacy deleted. This diverges from the spec's plan but matches the implemented reality (verified). ✓

**2. Placeholder scan:** none — concrete code/commands + the exact delete lists + expected results.

**3. Type/name consistency:** `apply_engagement_scope(wm, engagement)` signature is identical at all three call sites (helper def, engagement_service, lifespan_helpers); `Engagement.in_scope`/`out_of_scope`/`client` + `WorkingMemory.scope_in_scope`/`scope_out_of_scope`/`client` match the real dataclasses; `RedRobinApp(ws_url=, token=)` matches Phase-1 `__init__`. Order is correct: Task 2 (swap) precedes Task 3 (delete) so `tui_cmd` no longer imports the legacy app before it's removed.

---

## Execution Notes

- **Operator smoke (after Task 4, before PR) — needs a live daemon on an engagement WITH scope in `engagement.yaml` (e.g. e2e-verify):**
  1. **#156:** start the daemon on the engagement, open the TUI, press `i` → the Inspector **SCOPE** section + the Working Memory panel now show the real `in`/`out` scope (e.g. `EC2AMAZ-IJVFMFN`, `10.100.0.0/24` / `10.99.0.0/16`) instead of `(none)`. (This is the visible #156 win.)
  2. **Entrypoint swap:** run `redrobin tui` (the real CLI, no `python -m`) → it launches the **Textual** dashboard (not the old prompt_toolkit client).
  3. Sanity: the dashboard still streams verdicts + the Inspector/palette/`?`/Tab all work (Phases 2-5 intact).
- **PR:** `tui/phase-6-swap-and-retire` → `dev-juan` (CI runs on the merge push). **This completes the TUI Textual redesign** (all 6 phases). After merge, consider a `dev-juan` → `main` integration when ready (separate decision).
