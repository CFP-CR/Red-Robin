# Data-Quality Group C — Engagement Seeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `rules_of_engagement` + `objectives` a source — the operator authors them in `engagement.yaml`, and they seed into working memory — while leaving the dynamic interpretation (objective progress, `current_phase`, kill-chain) clean for the future AI assistant (#103).

**Architecture:** Per the writer-split (spec §2): the operator owns the static *definition* (ROE text + objective id/goal/priority), the assistant owns the dynamic *interpretation*. Extend the `Engagement` loader to parse ROE + objectives from `engagement.yaml`, and extend `apply_engagement_scope` to project them onto working memory. Objectives are **merged by id** so a daemon reload refreshes the definition (goal/priority) from yaml while preserving any interpretation (status/progress) — the seam the assistant will write into. `current_phase`/`kill_chain_status` are never touched here.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, pytest, ruff/mypy/import-linter, `uv`.

---

## Pre-existing context (read once before starting)

- **Spec:** `docs/superpowers/specs/2026-05-28-data-quality-design.md` — §2 (writer-split: definition vs interpretation), §3 (constraints), §7 (Group C — this plan), §11 (acceptance).
- **Branch:** `wm/dq-c-seeding`, off `dev-juan` `264a340` (Groups D + A merged). Already checked out.
- **The loader** (`redrobin/application/engagement_loader.py`), verified current state:

```python
@dataclass(frozen=True)
class Engagement:
    slug: str
    name: str
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    summary: str = ""
    client: str = ""


class EngagementLoader:
    def __init__(self, engagements_root: Path) -> None:
        self._root = engagements_root

    def load(self, slug: str) -> Engagement:
        eng_file = self._root / slug / "engagement.yaml"
        if not eng_file.is_file():
            raise FileNotFoundError(f"engagement file not found: {eng_file}")
        data = yaml.safe_load(eng_file.read_text()) or {}
        return Engagement(
            slug=str(data.get("slug", slug)),
            name=str(data.get("name", slug)),
            in_scope=list(data.get("in_scope") or []),
            out_of_scope=list(data.get("out_of_scope") or []),
            summary=str(data.get("summary") or "").strip(),
            client=str(data.get("client") or ""),
        )
```

- **The seeder** (`redrobin/application/working_memory_seed.py`), verified current state:

```python
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

`replace()` only changes the named fields, so `current_phase`/`kill_chain_status`/`objectives` are preserved untouched today. Called at engagement creation (`engagement_service.new`) AND refreshed on every daemon load (`lifespan_helpers.start_working_memory`, when an active engagement is set, before the reducer).

- **The domain models** (`redrobin/domain/memory.py`):
  - `Objective(id: str, goal: str, status: str = "not_started", priority: str = "medium", progress_pct: int = 0, milestones: list[str] = field(default_factory=list), pending: list[str] = field(default_factory=list))` — `id` and `goal` are REQUIRED.
  - `WorkingMemory` has `objectives: list[Objective]`, `rules_of_engagement: list[str]`, `current_phase: str = "not_started"`, `kill_chain_status: dict[str, str]`.

- **The yaml writer** (`redrobin/application/engagement_service.py` `new`): writes a template dict `{name, client, in_scope, out_of_scope, summary}` via `yaml.safe_dump`. A comment there notes objectives/current_phase were removed from the old template because "nothing reads them" — Group C revives the read path for ROE + objectives (NOT current_phase, which is interpretation).

## Standing constraints (apply to EVERY command/step)

- All `uv` invocations use `--no-sync`. Prepend `REDROBIN_ALLOW_DEGRADED=1` to the full-suite pytest.
- Atomic conventional commits, one per task. **NO AI/Claude co-author trailer.**
- TDD: failing test first → run-fail → implement → run-pass → commit. Suite green after every task.
- Production-grade, no dead code. **No deterministic interpretation logic** (no phase-deriver, no progress tracker) — that is the assistant's, deferred.
- Layer (import-linter): `engagement_loader.py` + `working_memory_seed.py` + `engagement_service.py` are APPLICATION; `Objective`/`WorkingMemory` are DOMAIN; `application → domain` is allowed. mypy gate covers `redrobin/domain` + `redrobin/application` — **all three files are gated; keep types clean.**
- Baseline pytest = **585** (post-Group-A). Each task notes its delta.

## File structure

| File | Change | Layer |
|---|---|---|
| `redrobin/application/engagement_loader.py` | MODIFY — `Engagement` gains `rules_of_engagement: list[str]` + `objectives: list[Objective]`; `load()` parses them (builds clean `Objective`s, skips malformed); docstring schema updated. | application |
| `redrobin/application/working_memory_seed.py` | MODIFY — `apply_engagement_scope` seeds ROE (blanket) + objectives (merge-by-id, preserve interpretation); `current_phase`/`kill_chain` untouched. | application |
| `redrobin/application/engagement_service.py` | MODIFY — `new`'s yaml template emits `rules_of_engagement: []` + `objectives: []` placeholders (discoverability; the spec's "sample engagement.yaml"). | application |
| `tests/unit/application/test_engagement_loader.py` | NEW — `load()` parsing tests. | tests |
| `tests/unit/application/test_working_memory_seed.py` | EXTEND — seed + merge-preserve tests. | tests |
| `tests/integration/test_engagement_new_roundtrip.py` | EXTEND — `new` writes the new keys + they round-trip. | tests |

Order: **T1 (loader read) → T2 (seeder) → T3 (new-template + verification + smoke)**. T1 is read-only-additive (defaults empty → existing behavior unchanged). T2 builds on T1's `Engagement.objectives`. T3 wires discoverability + verifies + hands off.

---

## Task 1: Extend the `Engagement` loader to parse ROE + objectives

**Files:**
- Modify: `redrobin/application/engagement_loader.py`
- Test: `tests/unit/application/test_engagement_loader.py` (NEW — if a loader unit test already exists, append there instead; grep `EngagementLoader(` in `tests/unit/` first)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/application/test_engagement_loader.py` with EXACTLY this content:

```python
"""Unit tests for EngagementLoader parsing of rules_of_engagement + objectives (#103)."""

from __future__ import annotations

from pathlib import Path

from redrobin.application.engagement_loader import EngagementLoader


def _write(tmp_path: Path, slug: str, body: str) -> None:
    eng_dir = tmp_path / slug
    eng_dir.mkdir(parents=True, exist_ok=True)
    (eng_dir / "engagement.yaml").write_text(body, encoding="utf-8")


def test_load_parses_rules_of_engagement_and_objectives(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "eng-x",
        "slug: eng-x\nname: X\n"
        "rules_of_engagement:\n  - No DoS\n  - Business hours only\n"
        "objectives:\n"
        "  - {id: o1, goal: Domain admin on CORP, priority: high}\n"
        "  - {id: o2, goal: Exfil HR data}\n",
    )
    eng = EngagementLoader(tmp_path).load("eng-x")
    assert eng.rules_of_engagement == ["No DoS", "Business hours only"]
    assert len(eng.objectives) == 2
    assert eng.objectives[0].id == "o1"
    assert eng.objectives[0].goal == "Domain admin on CORP"
    assert eng.objectives[0].priority == "high"
    # interpretation fields default clean — they are the assistant's to write
    assert eng.objectives[0].status == "not_started"
    assert eng.objectives[0].progress_pct == 0
    # objective without a priority falls back to the model default
    assert eng.objectives[1].priority == "medium"


def test_load_missing_roe_objectives_defaults_empty(tmp_path: Path) -> None:
    _write(tmp_path, "eng-y", "slug: eng-y\nname: Y\n")
    eng = EngagementLoader(tmp_path).load("eng-y")
    assert eng.rules_of_engagement == []
    assert eng.objectives == []


def test_load_skips_objective_without_id(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "eng-z",
        "slug: eng-z\nname: Z\n"
        "objectives:\n  - {goal: no id here}\n  - {id: o2, goal: valid one}\n",
    )
    eng = EngagementLoader(tmp_path).load("eng-z")
    assert len(eng.objectives) == 1
    assert eng.objectives[0].id == "o2"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/application/test_engagement_loader.py -v`
Expected: FAIL — `Engagement` has no `rules_of_engagement` / `objectives` attribute (AttributeError on the assertions).

- [ ] **Step 3: Extend the `Engagement` model + `load()`**

In `redrobin/application/engagement_loader.py`:

(a) Add the import for `Objective` (application → domain is allowed) at the top:

```python
from redrobin.domain.memory import Objective
```

(b) Add the two fields to the `Engagement` dataclass (after `client`):

```python
    rules_of_engagement: list[str] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)
```

(c) In `load()`, build the objectives (clean — only definition fields from yaml; interpretation defaults) and pass both new fields into `Engagement(...)`. Replace the `return Engagement(...)` block with:

```python
        objectives: list[Objective] = []
        for o in data.get("objectives") or []:
            if not isinstance(o, dict) or not o.get("id"):
                continue  # an objective needs an id; skip malformed entries
            objectives.append(
                Objective(
                    id=str(o["id"]),
                    goal=str(o.get("goal") or ""),
                    priority=str(o.get("priority") or "medium"),
                )
            )
        return Engagement(
            slug=str(data.get("slug", slug)),
            name=str(data.get("name", slug)),
            in_scope=list(data.get("in_scope") or []),
            out_of_scope=list(data.get("out_of_scope") or []),
            summary=str(data.get("summary") or "").strip(),
            client=str(data.get("client") or ""),
            rules_of_engagement=[str(r) for r in (data.get("rules_of_engagement") or [])],
            objectives=objectives,
        )
```

(d) Update the module docstring's "Schema (v0)" block to document the two new fields. Find the schema block (it lists `slug/name/in_scope/out_of_scope/summary`) and add:

```
  rules_of_engagement: list[str]   # ROE bullet lines (static; operator-authored)
  objectives:                       # mission goals (static definition; the
    - id: <str>                     #   assistant owns dynamic status/progress)
      goal: <str>
      priority: low|medium|high
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/application/test_engagement_loader.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: green (mypy must accept `Objective` in `Engagement` — application→domain). Pytest 585 → 588 (+3).

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/engagement_loader.py tests/unit/application/test_engagement_loader.py
git commit -m "feat(engagement): parse rules_of_engagement + objectives from engagement.yaml (#103)"
```

---

## Task 2: Seed ROE + objectives in `apply_engagement_scope` (merge-preserve)

**Files:**
- Modify: `redrobin/application/working_memory_seed.py`
- Test: `tests/unit/application/test_working_memory_seed.py`

**Design decision (explicit — the audit will scrutinize):** `apply_engagement_scope` runs on EVERY daemon load. A blanket `objectives=engagement.objectives` would wipe any interpretation (status/progress) the future assistant writes, on every restart — a latent footgun. So objectives are **merged by id**: an objective already in WM keeps its interpretation (`status`/`progress_pct`/`milestones`/`pending`) while its definition (`goal`/`priority`) is refreshed from yaml; new objectives are added clean; objectives removed from yaml are dropped (the yaml is the definition source). ROE is a blanket replace (pure definition, no interpretation). `current_phase`/`kill_chain_status` are never touched. This honors the writer-split: the definition-writer (yaml) and the interpretation-writer (assistant) do not clobber each other.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/application/test_working_memory_seed.py` (it already imports `Engagement`, `apply_engagement_scope`, `WorkingMemory`; add `from redrobin.domain.memory import Objective`):

```python
def test_apply_engagement_scope_seeds_roe_and_objectives_clean() -> None:
    wm = WorkingMemory(engagement_id="e")
    eng = Engagement(
        slug="e",
        name="E",
        rules_of_engagement=["No DoS", "Business hours only"],
        objectives=[Objective(id="o1", goal="Domain admin", priority="high")],
    )
    out = apply_engagement_scope(wm, eng)
    assert out.rules_of_engagement == ["No DoS", "Business hours only"]
    assert len(out.objectives) == 1
    assert out.objectives[0].id == "o1"
    assert out.objectives[0].goal == "Domain admin"
    assert out.objectives[0].priority == "high"
    assert out.objectives[0].status == "not_started"  # clean interpretation
    assert out.objectives[0].progress_pct == 0


def test_apply_engagement_scope_preserves_objective_interpretation_on_reseed() -> None:
    # WM already has o1 with interpretation written (as the future assistant would).
    wm = WorkingMemory(
        engagement_id="e",
        objectives=[
            Objective(id="o1", goal="old goal", priority="low", status="in_progress", progress_pct=40)
        ],
    )
    # The yaml re-seeds o1 with a refreshed definition.
    eng = Engagement(
        slug="e", name="E", objectives=[Objective(id="o1", goal="new goal", priority="high")]
    )
    out = apply_engagement_scope(wm, eng)
    o = out.objectives[0]
    assert o.goal == "new goal"       # definition refreshed from yaml
    assert o.priority == "high"       # definition refreshed
    assert o.status == "in_progress"  # interpretation PRESERVED
    assert o.progress_pct == 40       # interpretation PRESERVED


def test_apply_engagement_scope_adds_new_objective_clean_keeps_existing() -> None:
    wm = WorkingMemory(
        engagement_id="e",
        objectives=[Objective(id="o1", goal="g1", status="done", progress_pct=100)],
    )
    eng = Engagement(
        slug="e",
        name="E",
        objectives=[
            Objective(id="o1", goal="g1", priority="medium"),
            Objective(id="o2", goal="g2", priority="low"),
        ],
    )
    out = apply_engagement_scope(wm, eng)
    by_id = {o.id: o for o in out.objectives}
    assert by_id["o1"].status == "done"         # preserved
    assert by_id["o2"].status == "not_started"  # new, clean


def test_apply_engagement_scope_drops_objective_removed_from_yaml() -> None:
    wm = WorkingMemory(engagement_id="e", objectives=[Objective(id="o1", goal="g1")])
    eng = Engagement(slug="e", name="E", objectives=[])  # o1 removed from yaml
    out = apply_engagement_scope(wm, eng)
    assert out.objectives == []


def test_apply_engagement_scope_leaves_phase_and_kill_chain_untouched() -> None:
    wm = WorkingMemory(
        engagement_id="e",
        current_phase="lateral-movement",
        kill_chain_status={"recon": "done"},
    )
    eng = Engagement(slug="e", name="E", objectives=[Objective(id="o1", goal="g")])
    out = apply_engagement_scope(wm, eng)
    assert out.current_phase == "lateral-movement"  # interpretation untouched
    assert out.kill_chain_status == {"recon": "done"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/application/test_working_memory_seed.py -v`
Expected: the 5 new tests FAIL (apply_engagement_scope doesn't seed ROE/objectives yet); the 3 pre-existing tests still pass.

- [ ] **Step 3: Implement the seeding**

Replace the entire body of `redrobin/application/working_memory_seed.py` with:

```python
"""Project an engagement's definition onto its working memory (#103/#156).

``engagement.yaml`` is the source of truth for the static *definition* —
``client`` + scope + rules-of-engagement + objective goals. The working-memory
JSON mirrors them so the TUI (Working Memory panel + Inspector) shows the real
engagement instead of ``(none)``. Applied at engagement creation and re-applied
at daemon engagement-load.

Per the writer-split: the operator authors the definition (here); the future AI
assistant owns the dynamic *interpretation* (objective ``status``/``progress_pct``,
``current_phase``, ``kill_chain_status``). So objectives are MERGED BY ID — a
daemon reload refreshes an objective's definition (``goal``/``priority``) from
yaml while PRESERVING any interpretation already in working memory, and never
touches ``current_phase``/``kill_chain_status``. Definition-writer and
interpretation-writer do not clobber each other.
"""

from __future__ import annotations

from dataclasses import replace

from redrobin.application.engagement_loader import Engagement
from redrobin.domain.memory import Objective, WorkingMemory


def apply_engagement_scope(wm: WorkingMemory, engagement: Engagement) -> WorkingMemory:
    """Return a copy of ``wm`` with client/scope/ROE set from ``engagement`` and
    objectives merged by id (definition refreshed, interpretation preserved)."""
    existing = {o.id: o for o in wm.objectives}
    merged_objectives: list[Objective] = []
    for spec in engagement.objectives:
        prior = existing.get(spec.id)
        if prior is not None:
            # definition (goal/priority) from yaml; interpretation preserved from WM
            merged_objectives.append(
                replace(
                    spec,
                    status=prior.status,
                    progress_pct=prior.progress_pct,
                    milestones=list(prior.milestones),
                    pending=list(prior.pending),
                )
            )
        else:
            merged_objectives.append(spec)  # new objective — clean interpretation
    return replace(
        wm,
        client=engagement.client,
        scope_in_scope=list(engagement.in_scope),
        scope_out_of_scope=list(engagement.out_of_scope),
        rules_of_engagement=list(engagement.rules_of_engagement),
        objectives=merged_objectives,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/application/test_working_memory_seed.py -v`
Expected: 8 passed (5 new + 3 pre-existing — the pre-existing `..._preserves_other_fields` still passes because `current_phase` is still untouched, and the two scope tests still pass because their engagements have no objectives → `merged_objectives == []`).

- [ ] **Step 5: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: green. Pytest 588 → 593 (+5).

- [ ] **Step 6: Commit**

```bash
git add redrobin/application/working_memory_seed.py tests/unit/application/test_working_memory_seed.py
git commit -m "feat(engagement): seed ROE + objectives into working memory, merge-preserve interpretation (#103)"
```

---

## Task 3: `new`-template placeholders + full CI gate + smoke runbook

**Files:**
- Modify: `redrobin/application/engagement_service.py` (`new`'s yaml template)
- Test: `tests/integration/test_engagement_new_roundtrip.py`
- Create: `docs/superpowers/runbooks/2026-05-28-dq-c-smoke.md`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_engagement_new_roundtrip.py` (match the file's existing style — it creates an `EngagementService` against a tmp root, calls `new(...)`, then re-loads; read the file head for the exact fixture/import pattern and reuse it):

```python
def test_new_writes_roe_and_objectives_placeholders(tmp_path) -> None:
    """`new` seeds empty rules_of_engagement + objectives keys in the template so
    operators discover where to author them; they round-trip through the loader
    as empty lists (#103)."""
    from redrobin.application.engagement_loader import EngagementLoader
    from redrobin.application.engagement_service import EngagementService

    svc = EngagementService(root=tmp_path)
    svc.new("eng-c", name="C", client="acme", in_scope=["h1"])
    eng = EngagementLoader(tmp_path / "engagements").load("eng-c")
    assert eng.rules_of_engagement == []
    assert eng.objectives == []
    # the keys are present in the written yaml (discoverability)
    import yaml  # type: ignore[import-untyped]

    data = yaml.safe_load((tmp_path / "engagements" / "eng-c" / "engagement.yaml").read_text())
    assert "rules_of_engagement" in data
    assert "objectives" in data
```

Note: verify `EngagementService(root=...)` and the `engagements` subdir path against the real constructor (the audit confirmed `EngagementService(root: Path | None = None)` and `engagements/<slug>/`); if the existing roundtrip test uses a different fixture shape, mirror it.

- [ ] **Step 2: Run the test to verify it fails**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_engagement_new_roundtrip.py::test_new_writes_roe_and_objectives_placeholders -v`
Expected: FAIL — `rules_of_engagement`/`objectives` keys are not in the written template, so `"rules_of_engagement" in data` is False (the loader still returns `[]` via defaults, but the in-yaml-key assertions fail).

- [ ] **Step 3: Add the placeholders to the `new` template**

In `redrobin/application/engagement_service.py` `new`, find the template dict written via `yaml.safe_dump` (currently `{name, client, in_scope, out_of_scope, summary}`) and add two keys so a fresh engagement.yaml shows where ROE + objectives go:

```python
            "rules_of_engagement": [],
            "objectives": [],
```

Place them after `"summary"` (order is cosmetic; `sort_keys=False` preserves insertion order). Do NOT add `current_phase` — that is interpretation (the assistant's), never authored in yaml.

- [ ] **Step 4: Run the test to verify it passes**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_engagement_new_roundtrip.py -v`
Expected: all pass (the new test + every pre-existing roundtrip test — if a pre-existing test asserted the EXACT template dict/keys, update it to include the two new keys; the new keys are additive).

- [ ] **Step 5: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: green. Pytest 593 → 594 (+1, or +N if a pre-existing roundtrip test was split/updated — report the actual).

- [ ] **Step 6: Write the smoke runbook**

Create `docs/superpowers/runbooks/2026-05-28-dq-c-smoke.md` with EXACTLY this content:

```markdown
# Data-Quality Group C Live Smoke Runbook

**Acceptance gate** for Group C (#103) = ROE + objectives authored in engagement.yaml
show up in the TUI Inspector, with interpretation honestly clean.

## Pre-conditions
- Branch: `wm/dq-c-seeding` (or after merge, `dev-juan`).
- `manual-smoke` engagement exists.

## Steps

```powershell
# 1. Stop the daemon.
redrobin daemon stop -ErrorAction SilentlyContinue
Get-Process redrobin -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. Author ROE + objectives in the engagement.yaml (definition — the operator's to write).
#    Add these keys to $env:USERPROFILE\.redrobin\engagements\manual-smoke\engagement.yaml :
#
#      rules_of_engagement:
#        - No DoS
#        - Business hours only (08:00-18:00 CST)
#      objectives:
#        - {id: obj-1, goal: Domain admin on the lab DC, priority: high}
#        - {id: obj-2, goal: Locate and exfil the creds.txt loot, priority: medium}

# 3. Restart the daemon (start_working_memory re-seeds from the yaml on load).
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
.\.venv\Scripts\Activate.ps1
redrobin daemon start --host 127.0.0.1 --port 7777
```

## Checks

| # | Action | Expected |
|---|---|---|
| 1 | Open the TUI Inspector (`redrobin tui` → `i`), ROE section | shows the 2 rules from the yaml |
| 2 | Inspector OBJECTIVES section | shows obj-1 / obj-2 with their goal + priority |
| 3 | Objective status / progress | reads "not_started" / 0% (interpretation honestly clean — the assistant's to advance later) |
| 4 | current_phase | still "not_started" (NOT seeded from yaml — interpretation) |

Verify with `python $env:USERPROFILE\.redrobin\inspect_layers.py`.
```

- [ ] **Step 7: Commit**

```bash
git add redrobin/application/engagement_service.py tests/integration/test_engagement_new_roundtrip.py docs/superpowers/runbooks/2026-05-28-dq-c-smoke.md
git commit -m "feat(engagement): new-template ROE/objectives placeholders + Group C smoke runbook (#103)"
```

- [ ] **Step 8: Dispatch the final whole-branch review** (subagent-driven-development handles this) and address every finding.

- [ ] **Step 9: Hand the runbook to the operator.** Do NOT auto-merge. After the operator's live smoke is green, merge `wm/dq-c-seeding` → `dev-juan` (PR, same pattern; CI fires on the `dev-*` push post-merge). Then Group B (delete dead Account) branches off the updated `dev-juan` — the last data-quality plan.

---

## Self-Review

**1. Spec coverage (§7 Group C):**
- "Extend the engagement.yaml schema with `rules_of_engagement` + `objectives`" → T1 (loader parse) + T3 (template placeholders) + T1 docstring. ✓
- "Extend the `Engagement` dataclass + `load()`" → T1. ✓
- "Extend `apply_engagement_scope` to project ROE + objective definitions" → T2. ✓
- "Leave interpretation fields clean and untouched (status/progress/current_phase/kill_chain); NO deterministic deriver" → T2 (merge-preserve keeps interpretation; current_phase/kill_chain never in the replace() call; objectives seeded with model-default clean interpretation; no deriver anywhere). ✓
- "Modify a sample/manual-smoke engagement.yaml" → T3 (the `new` template is the canonical sample) + the smoke runbook (operator edits the live manual-smoke yaml). ✓
- §11 acceptance (Inspector shows ROE+objectives; progress/phase clean) → T3 runbook. ✓

**2. Placeholder scan:** No TBD/TODO. Every code step has complete before/after code; every test step has full bodies; every command has expected output. T3 Step 1's "mirror the file's fixture shape" is a directed verification (the test code is complete; only the fixture-construction may need to match the existing file's pattern), not a placeholder.

**3. Type consistency:**
- `Engagement` gains `rules_of_engagement: list[str]` + `objectives: list[Objective]` (T1), used identically in T2's `apply_engagement_scope` and tests. `Objective` imported from `redrobin.domain.memory` in both `engagement_loader.py` (T1) and `working_memory_seed.py` (T2). Consistent.
- `apply_engagement_scope(wm: WorkingMemory, engagement: Engagement) -> WorkingMemory` — signature unchanged (T2 only changes the body). All callers (engagement_service.new, lifespan start_working_memory) unaffected.
- `Objective(id, goal, status, priority, progress_pct, milestones, pending)` — built with `id`/`goal`/`priority` from yaml in T1; merged via `replace(spec, status=, progress_pct=, milestones=, pending=)` in T2. Field names match the domain model.

**4. Keep-green-between-tasks:** T1 is additive (new Engagement fields default empty → `apply_engagement_scope` still ignores objectives until T2, so no behavior change; existing tests green). T2 changes the seeder body but the 3 pre-existing seed tests stay green (current_phase untouched; no-objective engagements → empty merge). T3 adds template keys (additive) + the roundtrip test. Suite green after each commit.

**Next step:** per the operator's instruction, this chains directly into `/auditing-plans` (no execution-handoff pause). The audit will scrutinize especially the merge-preserve design decision (T2) and the `EngagementService(root=...)`/`new`-template assumptions (T3).
