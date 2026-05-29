# Data-Quality Group B — Delete the Dead `Account` Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the dead `Account` dataclass + `WorkingMemory.accounts` field + the always-empty ACCOUNTS Inspector section, so working memory has one credential concept (`Credential`) and no dead model (#106).

**Architecture:** `Account` was operator-curated-by-intent but nothing ever populated it (the enricher writes `credentials`); the Inspector's ACCOUNTS section is always empty. Per the thesis (observe, don't make the operator curate) + YAGNI, delete it. This is one atomic deletion: removing the domain field forces the repo/inspector/test changes in the same commit (a half-deleted `Account` doesn't compile/pass). The one real risk — an existing `working_memory.json` with a legacy `accounts` key — is handled because `_from_dict` uses explicit kwargs (it simply stops reading the key; no `KeyError`), proven by a dedicated load test.

**Tech Stack:** Python 3.12, dataclasses, pytest, ruff/mypy/import-linter, `uv`.

---

## Pre-existing context (read once before starting)

- **Spec:** `docs/superpowers/specs/2026-05-28-data-quality-design.md` §8 (Group B) + §2 (writer-split) + §12 (the legacy-key risk).
- **Branch:** `wm/dq-b-account`, off `dev-juan` `f496bce` (Groups D + A + C merged). Already checked out.
- **Complete reference map (from a full grep of `Account`/`accounts` across `redrobin/` + `tests/`):**

| File:line | Reference | Action |
|---|---|---|
| `redrobin/domain/memory.py:5` | docstring "WorkingMemory (hosts, accounts, defenses…)" | remove "accounts," from the prose |
| `redrobin/domain/memory.py:36-41` | `@dataclass class Account:` (name/kind/source_host/captured_at) | DELETE the dataclass |
| `redrobin/domain/memory.py:56-59` | `Credential` docstring "Distinct from ``Account``: …" | rewrite to not reference `Account` |
| `redrobin/domain/memory.py:135` | `accounts: list[Account] = field(default_factory=list)` | DELETE the field |
| `redrobin/infrastructure/persistence/working_memory_repo.py:25` | `Account,` in the domain import | remove the import |
| `redrobin/infrastructure/persistence/working_memory_repo.py:203` | `accounts=[Account(**a) for a in data.get("accounts", [])],` | DELETE the kwarg line |
| `redrobin/tui/textual_ui/inspector.py:29` | `("accounts", "ACCOUNTS"),` in `SECTIONS` | DELETE |
| `redrobin/tui/textual_ui/inspector.py:47` | `"accounts": len(snapshot.get("accounts") or []),` in `section_counts` | DELETE |
| `redrobin/tui/textual_ui/inspector.py:119-120` | `_account_lines(s)` helper | DELETE the function |
| `redrobin/tui/textual_ui/inspector.py:171-172` | `elif section == "accounts": lines = _account_lines(snapshot)` | DELETE the branch |
| `redrobin/application/layer_b_engine.py:178` | comment "hosts/accounts/defenses/etc." | remove "accounts" (precision) |
| `tests/unit/test_working_memory_repo.py:6,30,39` | `Account` import + `accounts=[Account(...)]` + `assert loaded.accounts[0]...` | remove; + ADD legacy-load test |
| `tests/unit/tui_textual/test_inspector.py:36-38` | `"accounts": [...]` in the `SNAP` fixture | DELETE the key |
| `tests/integration/test_tui_ws_broadcast.py:65,80` | docstring lists "accounts" + `"accounts",` in the expected-keys tuple | remove both |

- **Why `_from_dict` tolerates the legacy key:** it builds `WorkingMemory(engagement_id=..., client=..., ...)` with EXPLICIT kwargs (NOT `WorkingMemory(**data)`). Removing the `accounts=` kwarg means `data.get("accounts")` is never called → a legacy `accounts` key in the JSON is silently ignored. `save()` uses `asdict(wm)` → a re-save drops the key. The manual-smoke `working_memory.json` currently has `accounts: []` — it must load without crashing.
- **Why this is atomic:** `working_memory_to_snapshot = asdict(wm)` — after the field is gone, the snapshot has no `accounts`; the TUI/tests that expected it must change in the same commit. There is no green intermediate where the field exists for some readers and not others.

## Standing constraints (apply to EVERY command/step)

- All `uv` invocations use `--no-sync`. Prepend `REDROBIN_ALLOW_DEGRADED=1` to the full-suite pytest.
- Atomic conventional commits, one per task. **NO AI/Claude co-author trailer.** Subagents = Opus 4.8.
- Production-grade, **NO DEAD CODE** — after this, `grep -rn "\bAccount\b\|accounts" redrobin/ --include="*.py"` returns ZERO (the `Account` class + the `accounts` WM field; unrelated prose like "account" is fine but we removed those too).
- mypy gate = `redrobin/domain` + `redrobin/application`; the inspector is `tui` (presentation) — NOT mypy-gated but ruff-checked.
- Baseline pytest = **598** (post-Group-C).

## File structure

This is ONE cohesive deletion across 3 source files + 3 test files, landing in a single commit (T1) to keep the suite green. T2 is verification + handoff. Splitting the deletion by file would leave a broken intermediate (e.g. the domain field gone but the repo still importing `Account`).

---

## Task 1: Delete the `Account` model (atomic)

**Files:**
- Modify: `redrobin/domain/memory.py`, `redrobin/infrastructure/persistence/working_memory_repo.py`, `redrobin/tui/textual_ui/inspector.py`, `redrobin/application/layer_b_engine.py`
- Test: `tests/unit/test_working_memory_repo.py`, `tests/unit/tui_textual/test_inspector.py`, `tests/integration/test_tui_ws_broadcast.py`

- [ ] **Step 1: Write the failing test (the deletion's TDD anchor)**

In `tests/unit/test_working_memory_repo.py`, append this test (it proves the post-deletion contract: a legacy JSON with an `accounts` key loads without crashing AND the loaded WM has no `accounts` attribute):

```python
def test_load_tolerates_legacy_accounts_key(tmp_path):
    # An existing working_memory.json (pre-Group-B) has an `accounts` key. After
    # deleting the Account model it must still load — the key is silently ignored,
    # no KeyError — and the loaded WorkingMemory has no `accounts` attribute.
    import json

    wm_path = tmp_path / "wm.json"
    wm_path.write_text(
        json.dumps(
            {
                "engagement_id": "legacy",
                "client": "Acme",
                "accounts": [{"name": "CORP\\\\alice", "kind": "ntlm"}],
                "hosts": [{"name": "DC01"}],
            }
        ),
        encoding="utf-8",
    )
    loaded = WorkingMemoryRepo(wm_path).load()
    assert loaded.engagement_id == "legacy"
    assert loaded.hosts[0].name == "DC01"
    assert not hasattr(loaded, "accounts")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/test_working_memory_repo.py::test_load_tolerates_legacy_accounts_key -v`
Expected: FAIL on `assert not hasattr(loaded, "accounts")` — `WorkingMemory` still has the `accounts` field (and `_from_dict` still builds it from the legacy key).

- [ ] **Step 3: Delete the `Account` dataclass + `accounts` field in `domain/memory.py`**

(a) DELETE the entire `Account` dataclass (the `@dataclass` decorator + `class Account:` + its 4 fields):

```python
@dataclass
class Account:
    name: str  # e.g., "acme\\jdoe"
    kind: str  # "ntlm" | "kerberos" | "plaintext"
    source_host: str | None = None
    captured_at: str | None = None  # ISO timestamp
```

(b) DELETE the `accounts` field from `WorkingMemory`:

```python
    accounts: list[Account] = field(default_factory=list)
```

(c) Update the module docstring line 5 — change `WorkingMemory (hosts, accounts, defenses, tools, kill_chain)` to `WorkingMemory (hosts, defenses, tools, kill_chain)` (drop `accounts, `).

(d) Rewrite the `Credential` docstring's opening so it no longer references the deleted `Account`. Replace:

```python
    """Credential observed on the wire (parsed from beacon output).

    Distinct from ``Account``: ``Account`` is operator-curated and may include
    plaintext/ticket material captured by hand; ``Credential`` is reducer-derived
    from event parsing and always carries a ``source_beacon_id`` so the operator
    can trace provenance.
```

with:

```python
    """Credential observed on the wire (parsed from beacon output).

    Reducer-derived from event parsing; always carries a ``source_beacon_id`` so
    the operator can trace provenance.
```

(e) If `domain/memory.py` has an `__all__` that lists `Account`, remove `"Account"` from it. (Grep `__all__` in the file; if none, skip.)

- [ ] **Step 4: Remove the `Account` references in the repo**

In `redrobin/infrastructure/persistence/working_memory_repo.py`:
- Remove `Account,` from the `from redrobin.domain.memory import (...)` block (line ~25).
- Remove the `accounts=` kwarg line from `_from_dict`:

```python
            accounts=[Account(**a) for a in data.get("accounts", [])],
```

(After this, `_from_dict` no longer reads `data.get("accounts")`, so a legacy `accounts` key is ignored — the Step-1 test's contract.)

- [ ] **Step 5: Remove the ACCOUNTS section in the inspector**

In `redrobin/tui/textual_ui/inspector.py`:
- Remove the `("accounts", "ACCOUNTS"),` line from the `SECTIONS` list.
- Remove the `"accounts": len(snapshot.get("accounts") or []),` line from `section_counts`.
- Remove the `_account_lines` function:

```python
def _account_lines(s: dict[str, Any]) -> list[str]:
    return _named_lines(s.get("accounts") or [])
```

- Remove the `render_section` branch:

```python
    elif section == "accounts":
        lines = _account_lines(snapshot)
```

- Fix the now-dangling `_named_lines` docstring (audit M1): it reads `"""Identity = ``name``, then every other populated field (host/account/tool)."""` (`inspector.py:106`). With `_account_lines` gone, drop the `account` mention — change `(host/account/tool)` to `(host/tool)`.

- [ ] **Step 6: Fix the stale prose references (audit D1)**

Two comments name `accounts`/`14 fields` and go stale once the field is deleted (the plan's NO-DEAD-CODE constraint):
- `redrobin/application/layer_b_engine.py:178` — change `# text representation of working memory (hosts/accounts/defenses/etc.)` to `# text representation of working memory (hosts/defenses/etc.)` (drop `accounts/`).
- `redrobin/api/tui_ws.py:53` — the docstring says the snapshot carries `14 WorkingMemory fields (#139)`; after deleting `accounts` there are **13**. Change `14 WorkingMemory fields` to `13 WorkingMemory fields`.

- [ ] **Step 7: Update the affected tests**

(a) `tests/unit/test_working_memory_repo.py`:
- Remove `Account,` from the `from redrobin.domain.memory import (...)` block.
- In `test_save_and_load_round_trip`, remove the `accounts=[Account(name="acme\\svc_sql", kind="plaintext", source_host="DC01")],` line and the `assert loaded.accounts[0].kind == "plaintext"` assertion.

(b) `tests/unit/tui_textual/test_inspector.py`: remove the `accounts` key from the `SNAP` fixture:

```python
    "accounts": [
        {"name": "CORP\\\\alice", "kind": "ntlm", "source_host": "EC2AMAZ", "captured_at": "t0"}
    ],
```

(c) `tests/integration/test_tui_ws_broadcast.py`:
- Remove `"accounts",` from the expected-keys tuple in `test_ws_initial_snapshot_carries_all_fields`.
- Update the test's docstring: change `(scope/objectives/kill_chain/accounts/etc.)` to `(scope/objectives/kill_chain/etc.)`.

- [ ] **Step 8: Run the TDD anchor + the touched tests to verify they pass**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_working_memory_repo.py tests/unit/tui_textual/test_inspector.py tests/integration/test_tui_ws_broadcast.py -v`
Expected: all pass — including `test_load_tolerates_legacy_accounts_key` (now `not hasattr(loaded, "accounts")` is True), the round-trip test (no accounts), `test_sections_match_spec_order` + `test_section_counts` (SECTIONS no longer has accounts; the order asserts engagement-first/findings-last, unaffected), and the WS broadcast test (no "accounts" expected).

- [ ] **Step 9: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: every gate green. mypy must be clean (the deleted `Account` is no longer referenced in domain/application). Pytest 598 → 599 (+1: the legacy-load test; no test was purely about accounts, so none is deleted).

- [ ] **Step 10: Commit**

```bash
git add redrobin/domain/memory.py redrobin/infrastructure/persistence/working_memory_repo.py redrobin/tui/textual_ui/inspector.py redrobin/application/layer_b_engine.py redrobin/api/tui_ws.py tests/unit/test_working_memory_repo.py tests/unit/tui_textual/test_inspector.py tests/integration/test_tui_ws_broadcast.py
git commit -m "refactor(memory): delete the dead Account model + empty ACCOUNTS section (#106)"
```

---

## Task 2: Grep-prove zero references + final CI + handoff

**Files:** none (verification + handoff).

- [ ] **Step 1: Grep-prove the `Account` class + `accounts` field are gone**

Run: `grep -rn "\bAccount\b" redrobin/ --include="*.py"`
Expected: ZERO hits.

Run: `grep -rn "accounts" redrobin/ --include="*.py"`
Expected: ZERO hits (the WM field + every reader removed; the layer_b comment fixed). If a hit remains, it's a leak — remove it (no dead code).

Run: `grep -rn "\bAccount\b\|\baccounts\b" tests/ --include="*.py"`
Expected: hits ONLY inside `test_load_tolerates_legacy_accounts_key` (audit M2): the test name itself, its `"accounts"` JSON dict KEY in `json.dumps`, and its comment — all INTENTIONAL (that test exercises the legacy data). So expect a small number of hits, ALL within that one test — NOT zero, and NOT anywhere else. Any `Account` (class) hit, or any `accounts` hit OUTSIDE that test, is a leak — remove it.

- [ ] **Step 2: Run every CI gate (confirm)**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: green, 599 passed.

- [ ] **Step 3: Operator handoff (smoke is OPTIONAL/light)**

This is a deletion with full test coverage (the legacy-load test proves an existing `working_memory.json` with an `accounts` key loads cleanly). A live smoke is OPTIONAL — the only visible change is the Inspector no longer has an ACCOUNTS section. If the operator wants a visual confirm: on the merged branch, open the TUI Inspector (`redrobin tui` → `i`) against `manual-smoke` (whose `working_memory.json` still has `accounts: []`) and confirm (a) it loads without error, (b) there is no ACCOUNTS section, (c) CREDENTIALS still shows the real credential. Otherwise, the test coverage is sufficient.

Do NOT auto-merge. After the final whole-branch review (subagent-driven-development handles it) and the operator's go, merge `wm/dq-b-account` → `dev-juan` (PR, CI fires on the `dev-*` push post-merge). **This completes the data-quality phase (D + A + C + B).**

---

## Self-Review

**1. Spec coverage (§8 Group B):**
- "Remove the `Account` dataclass + `accounts` field from `WorkingMemory`; fix `_from_dict`/serialization" → T1 Steps 3-4. ✓
- "Remove the `accounts` entry from `SECTIONS`, `_account_lines`, `section_counts` in inspector.py" → T1 Step 5. ✓
- "Remove any other `accounts` references (grep-prove zero)" → T1 Step 6 (layer_b comment) + T2 Step 1 (grep-prove). ✓
- "Update tests that referenced `accounts`" → T1 Step 7. ✓
- §12 legacy-key risk ("tolerate-and-ignore an unknown legacy `accounts` key; don't KeyError") → T1 Step 1 test + the explicit-kwargs `_from_dict` (Step 4). ✓
- §11 acceptance (no ACCOUNTS section; CREDENTIALS unaffected) → T2 Step 3 optional smoke. ✓

**2. Placeholder scan:** No TBD/TODO. Every deletion shows the exact code being removed; the one added test has full content; the legacy-load test is complete; commands have expected output.

**3. Type consistency:** `Account` is fully removed (class, field, import, all readers). No task references `Account` after T1. `WorkingMemory` loses `accounts` everywhere consistently (domain field, repo `_from_dict`, snapshot via `asdict`, inspector, tests). `working_memory_to_snapshot = asdict(wm)` automatically drops `accounts` once the field is gone — no separate change needed.

**4. Keep-green-between-tasks:** T1 is ONE atomic commit — the deletion + all readers + all test updates land together, so the suite is green before (all reference accounts consistently) and after (nothing references accounts). There is no valid partial state, which is why this is one task, not split by file. T2 is verification only (no code change). The legacy-load test is the red→green TDD anchor (fails before deletion via `hasattr`, passes after).

**Next step:** per the operator's instruction, this chains directly into `/auditing-plans` (no execution-handoff pause). The audit will scrutinize especially: the complete grep coverage (no missed `Account`/`accounts` reader), the `_from_dict` legacy-key tolerance (no KeyError on an old JSON), and that no test purely-about-accounts was silently dropped (only the round-trip test's accounts bits + the WS-broadcast key are removed, both additive-to-the-deletion).
