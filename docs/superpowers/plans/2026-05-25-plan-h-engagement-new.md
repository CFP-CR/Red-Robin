# Plan H: `engagement new` Usability (#135 + #142) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `redrobin engagement new` produce an engagement the daemon can actually use — today it writes a YAML the loader can't consume, so the daemon loads empty scope and the gate BLOCKs every targeted command silently.

**Architecture:** Two `Engagement` classes with disjoint YAML schemas are the root cause: the WRITER (`EngagementService.new`) emits `id` + nested `scope.in_scope` + dead keys; the daemon LOADER (`EngagementLoader.load`) reads top-level `slug`/`in_scope`/`out_of_scope`/`summary`. Mismatch → `in_scope=[]` → `ScopeEnforcer` deny-all. Fix: **unify on one `Engagement` model** (the loader's, the de-facto runtime model), make the writer emit that schema, add CLI flags to set scope at creation, and **fail-closed + warn loudly** when scope is empty (never silent). Verified live 2026-05-25: a fresh `engagement new` writes nested `scope:` and the real `ScopeEnforcer` BLOCKs `jump psexec 10.10.0.5` with empty scope.

**Tech Stack:** Python 3.13, argparse CLI, PyYAML, pytest / pytest-asyncio, FastAPI lifespan (daemon). No `.cna` / Java / beacon changes — this is CLI + daemon Python, testable with unit tests + a local daemon (`TestClient`).

**Posture (decided):** fail-closed. Empty `in_scope` keeps deny-all semantics (correct for an OPSEC gate); the fix makes it **loud** (CLI notice at creation + daemon WARN at load + banner indicator) and **settable** (`--in-scope` flags), never a silent allow-all wildcard.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `redrobin/domain/engagement.py` | slug validation only | REMOVE the dead `Engagement` + `EngagementScope` dataclasses; keep `validate_slug` + `_SLUG_RE` |
| `redrobin/application/engagement_loader.py` | the single `Engagement` model + YAML reader | ADD `client` field + defaults to `Engagement`; read `client` in `load()` |
| `redrobin/application/engagement_service.py` | engagement lifecycle (writer) | import the loader `Engagement` + `validate_slug`; rewrite `new()` to accept scope/name/summary and write the **loader schema** |
| `redrobin/cli_helpers/engagement_cmd.py` | CLI business logic | `run_new` gains name/in_scope/out_of_scope/summary; add `_csv_list` flatten helper; empty-scope notice; `eng.id`→`eng.slug` |
| `redrobin/cli.py` | argparse + dispatch | `engagement new` gains `--name/--in-scope/--out-of-scope/--summary`; `cmd_engagement_new` forwards them |
| `redrobin/api/lifespan_helpers.py` | daemon engagement load | WARN + set `app.state.scope_empty` when a loaded engagement has empty `in_scope` (#142) |
| `redrobin/application/startup_banner.py` | startup banner | `format_banner` gains `scope_empty` → renders an EMPTY-SCOPE warning line |
| `redrobin/api/app.py` | lifespan wiring | pass `scope_empty=app.state.scope_empty` to `format_banner` |
| `tests/integration/test_engagement_new_roundtrip.py` | #135 regression | CREATE — writer output must round-trip through the loader |
| `tests/unit/test_engagement_cmd.py` | CLI helper | CREATE — `_csv_list` flatten + empty-scope notice |
| `tests/integration/test_engagement_new_flags.py` | CLI flags + acceptance | CREATE — `engagement new --in-scope` → loader → usable scope (the #135 acceptance) |
| `tests/integration/test_engagement_empty_scope_warn.py` | #142 | CREATE — empty-scope engagement boots with `scope_empty` flag + WARN |
| `tests/unit/test_startup_banner.py` | banner | CREATE (or append) — `scope_empty=True` renders the warning line |

**Note (verified):** the only importers of the dead domain `Engagement`/`EngagementScope` are `engagement_service.py:25` (return type) and the `EngagementScope` definition itself; `tests/unit/test_engagement_service.py` imports only `validate_slug`. The loader `Engagement` is imported by `lifespan_helpers.py:29` (type hint, reads `.in_scope/.out_of_scope/.slug/.name`) — all fields preserved, so lifespan needs no change in Task 1. `inspector.py`'s `rules_of_engagement` is `WorkingMemory.rules_of_engagement`, NOT the engagement model — untouched.

---

### Task 1: Unify the `Engagement` model + make the writer emit the loader schema (#135 core)

**Files:**
- Modify: `redrobin/domain/engagement.py` (remove dead dataclasses)
- Modify: `redrobin/application/engagement_loader.py:28-34,46-52` (add `client` + defaults)
- Modify: `redrobin/application/engagement_service.py:25,49-75` (import + rewrite `new()`)
- Modify: `redrobin/cli_helpers/engagement_cmd.py:28-29` (`eng.id`→`eng.slug`)
- Modify: `tests/unit/test_engagement_service.py:33` (`eng.id`→`eng.slug`)
- Modify: `tests/integration/test_engagement_active_file.py:42-45` (stale comment)
- Test: `tests/integration/test_engagement_new_roundtrip.py` (create)

- [ ] **Step 1: Write the failing round-trip test**

Create `tests/integration/test_engagement_new_roundtrip.py`:

```python
"""#135: `engagement new` must write a YAML the daemon's EngagementLoader
consumes WITHOUT losing scope.

Root cause (verified live 2026-05-25): the writer emitted `id` + nested
`scope.in_scope`; the loader reads top-level `slug`/`in_scope`, so scope was
silently dropped -> empty -> ScopeEnforcer deny-all on every targeted command.
This test pins the writer and loader to ONE schema.
"""
from __future__ import annotations

from redrobin.application.engagement_loader import EngagementLoader
from redrobin.application.engagement_service import EngagementService


def test_new_output_round_trips_through_loader(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new(
        "acme-q2",
        name="Acme Q2 2026",
        client="Acme Corp",
        in_scope=["corp.acme.local", "10.10.0.0/16"],
        out_of_scope=["prod.acme.com"],
        summary="Internal pentest.",
    )

    eng = EngagementLoader(engagements_root=tmp_path / "engagements").load("acme-q2")
    assert eng.slug == "acme-q2"
    assert eng.name == "Acme Q2 2026"
    assert eng.in_scope == ["corp.acme.local", "10.10.0.0/16"]
    assert eng.out_of_scope == ["prod.acme.com"]
    assert eng.summary == "Internal pentest."


def test_new_writes_top_level_keys_not_nested_scope(tmp_path):
    import yaml

    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2", in_scope=["corp.acme.local"])
    data = yaml.safe_load(
        (tmp_path / "engagements" / "acme-q2" / "engagement.yaml").read_text()
    )
    # The loader reads these top-level keys; the old writer used `id` + `scope:`.
    assert data["slug"] == "acme-q2"
    assert data["in_scope"] == ["corp.acme.local"]
    assert "scope" not in data  # no nested scope block
    assert "id" not in data     # loader never read `id`
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_engagement_new_roundtrip.py -v`
Expected: FAIL — `new()` raises `TypeError: new() got an unexpected keyword argument 'name'` (the new kwargs don't exist yet).

- [ ] **Step 3: Strip the dead model from `domain/engagement.py`**

Replace the ENTIRE contents of `redrobin/domain/engagement.py` with:

```python
"""redrobin/domain/engagement.py — engagement slug validation.

The ``Engagement`` data model lives in
:mod:`redrobin.application.engagement_loader` — a single source of truth shared
by the writer (:meth:`EngagementService.new`) and the daemon loader
(:meth:`EngagementLoader.load`). Before Plan H there were TWO ``Engagement``
classes with disjoint YAML schemas (#135); this module now keeps only the
slug-validation rule both layers depend on.
"""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_slug(slug: str) -> None:
    """Engagement slugs are filesystem-safe + URL-safe."""
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid engagement slug {slug!r}. "
            "Use lowercase alphanumeric + hyphens/underscores (max 64 chars)."
        )
```

- [ ] **Step 4: Add `client` + defaults to the loader `Engagement` and read it**

In `redrobin/application/engagement_loader.py`, change the import line `from dataclasses import dataclass` to:

```python
from dataclasses import dataclass, field
```

Replace the `Engagement` dataclass (lines 28-34) with:

```python
@dataclass(frozen=True)
class Engagement:
    slug: str
    name: str
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    summary: str = ""
    client: str = ""
```

Replace the `return Engagement(...)` block in `load()` (lines 46-52) with:

```python
        return Engagement(
            slug=str(data.get("slug", slug)),
            name=str(data.get("name", slug)),
            in_scope=list(data.get("in_scope") or []),
            out_of_scope=list(data.get("out_of_scope") or []),
            summary=str(data.get("summary") or "").strip(),
            client=str(data.get("client") or ""),
        )
```

- [ ] **Step 5: Rewrite the writer to emit the loader schema**

In `redrobin/application/engagement_service.py`, replace the import on line 25:

```python
from redrobin.domain.engagement import Engagement, validate_slug
```

with:

```python
from redrobin.application.engagement_loader import Engagement
from redrobin.domain.engagement import validate_slug
```

Replace the `new()` method (lines 49-75) with:

```python
    def new(
        self,
        slug: str,
        *,
        name: str = "",
        client: str = "",
        in_scope: list[str] | None = None,
        out_of_scope: list[str] | None = None,
        summary: str = "",
    ) -> Engagement:
        validate_slug(slug)
        path = self._path_for(slug)
        if path.exists():
            raise EngagementError(f"engagement {slug!r} already exists at {path}")
        path.mkdir(parents=True, exist_ok=True)

        eng = Engagement(
            slug=slug,
            name=name or slug,
            in_scope=list(in_scope or []),
            out_of_scope=list(out_of_scope or []),
            summary=summary,
            client=client,
        )
        # #135: write the schema the daemon's EngagementLoader consumes
        # (top-level slug/name/in_scope/out_of_scope/summary). The old template
        # emitted `id` + a nested `scope:` block + dead keys
        # (objectives/current_phase/...) that nothing reads from engagement.yaml,
        # so the loader silently loaded empty scope -> deny-all.
        template = {
            "slug": eng.slug,
            "name": eng.name,
            "client": eng.client,
            "in_scope": eng.in_scope,
            "out_of_scope": eng.out_of_scope,
            "summary": eng.summary,
        }
        (path / ENGAGEMENT_YAML).write_text(yaml.safe_dump(template, sort_keys=False))

        # Initialize empty working memory (objectives/phase/etc. live HERE, not
        # in engagement.yaml).
        WorkingMemoryRepo(path / "working_memory.json").save(
            WorkingMemory(engagement_id=slug, client=client)
        )

        return eng
```

- [ ] **Step 6: Fix the two `.id` readers of the return value**

In `redrobin/cli_helpers/engagement_cmd.py`, in `run_new` (lines 28-29), change `eng.id` to `eng.slug`:

```python
    print(f"[+] Created engagement {eng.slug}")
    print(f"[+] Edit {redrobin_root()}/engagements/{eng.slug}/engagement.yaml")
```

In `tests/unit/test_engagement_service.py`, line 33, change:

```python
    assert eng.slug == "acme-q2"
```

- [ ] **Step 7: Update the now-stale comment in `test_engagement_active_file.py`**

In `tests/integration/test_engagement_active_file.py`, replace the comment block at lines 42-45 (the one explaining the `id`→`slug` fallback) with:

```python
        # 3. Lifespan ran; engagement should be resolved. As of Plan H (#135)
        #    the writer emits `slug` directly (one unified schema), so this no
        #    longer relies on the loader's id->slug fallback.
```

(The assertions on the next lines stay unchanged — they still pass.)

- [ ] **Step 8: Run the round-trip test + the full engagement suite**

Run: `uv run pytest tests/integration/test_engagement_new_roundtrip.py tests/unit/test_engagement_service.py tests/integration/test_engagement_scope.py tests/integration/test_engagement_active_file.py tests/unit/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add redrobin/domain/engagement.py redrobin/application/engagement_loader.py redrobin/application/engagement_service.py redrobin/cli_helpers/engagement_cmd.py tests/unit/test_engagement_service.py tests/integration/test_engagement_active_file.py tests/integration/test_engagement_new_roundtrip.py
git commit -m "fix(engagement): unify Engagement model + write loader-consumable YAML (#135)"
```

---

### Task 2: CLI flags `--name/--in-scope/--out-of-scope/--summary` (#135 usability)

**Files:**
- Modify: `redrobin/cli_helpers/engagement_cmd.py` (`run_new` signature + `_csv_list`)
- Modify: `redrobin/cli.py:115-119` (`cmd_engagement_new`) and `:264-267` (argparse)
- Test: `tests/unit/test_engagement_cmd.py` (create), `tests/integration/test_engagement_new_flags.py` (create)

- [ ] **Step 1: Write the failing helper + flags tests**

Create `tests/unit/test_engagement_cmd.py`:

```python
"""CLI helper tests for `engagement new` scope flags (Plan H, #135)."""
from __future__ import annotations

from redrobin.cli_helpers.engagement_cmd import _csv_list


def test_csv_list_flattens_repeats_and_commas():
    # --in-scope a,b --in-scope c  -> ["a", "b", "c"]
    assert _csv_list(["a,b", "c"]) == ["a", "b", "c"]


def test_csv_list_strips_and_drops_empties():
    assert _csv_list([" a , ,b ", ""]) == ["a", "b"]


def test_csv_list_none_is_empty():
    assert _csv_list(None) == []
```

Create `tests/integration/test_engagement_new_flags.py`:

```python
"""`engagement new --in-scope ...` writes scope the loader consumes (Plan H)."""
from __future__ import annotations

from redrobin.application.engagement_loader import EngagementLoader


def test_cli_new_with_scope_flags_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.cli import main

    assert main(["init", "--non-interactive"]) == 0
    rc = main(
        [
            "engagement", "new", "acme-q2",
            "--name", "Acme Q2",
            "--client", "Acme Corp",
            "--in-scope", "corp.acme.local,10.10.0.0/16",
            "--in-scope", "dc01.acme.local",
            "--out-of-scope", "prod.acme.com",
            "--summary", "Internal pentest engagement.",
        ]
    )
    assert rc == 0

    eng = EngagementLoader(
        engagements_root=tmp_path / ".redrobin" / "engagements"
    ).load("acme-q2")
    assert eng.name == "Acme Q2"
    assert eng.in_scope == ["corp.acme.local", "10.10.0.0/16", "dc01.acme.local"]
    assert eng.out_of_scope == ["prod.acme.com"]
    assert eng.summary == "Internal pentest engagement."
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_engagement_cmd.py tests/integration/test_engagement_new_flags.py -v`
Expected: FAIL — `ImportError: cannot import name '_csv_list'` (unit) and `unrecognized arguments: --name` (integration).

- [ ] **Step 3: Add `_csv_list` + extend `run_new` in the helper**

In `redrobin/cli_helpers/engagement_cmd.py`, add the helper just below the imports (after line 13):

```python
def _csv_list(values: list[str] | None) -> list[str]:
    """Flatten repeatable + comma-separated flag values into a clean list.

    ``--in-scope a,b --in-scope c`` -> ``["a", "b", "c"]``. Trims whitespace
    and drops empties so a stray comma never injects a blank scope entry.
    """
    out: list[str] = []
    for v in values or []:
        out.extend(part.strip() for part in v.split(",") if part.strip())
    return out
```

Replace `run_new` (lines 22-30) with:

```python
def run_new(
    *,
    svc: EngagementService,
    slug: str,
    client: str | None,
    name: str | None = None,
    in_scope: list[str] | None = None,
    out_of_scope: list[str] | None = None,
    summary: str | None = None,
) -> int:
    parsed_in = _csv_list(in_scope)
    parsed_out = _csv_list(out_of_scope)
    try:
        eng = svc.new(
            slug,
            name=name or "",
            client=client or "",
            in_scope=parsed_in,
            out_of_scope=parsed_out,
            summary=summary or "",
        )
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Created engagement {eng.slug}")
    print(f"[+] Edit {redrobin_root()}/engagements/{eng.slug}/engagement.yaml")
    return 0
```

- [ ] **Step 4: Wire the argparse flags + dispatch in `cli.py`**

In `redrobin/cli.py`, replace `cmd_engagement_new` (lines 115-119) with:

```python
def cmd_engagement_new(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_new

    return run_new(
        svc=make_service(),
        slug=args.slug,
        client=args.client,
        name=args.name,
        in_scope=args.in_scope,
        out_of_scope=args.out_of_scope,
        summary=args.summary,
    )
```

Replace the `new` subparser block (lines 264-267) with:

```python
    new_eng = eng_sub.add_parser("new")
    new_eng.add_argument("slug")
    new_eng.add_argument("--client", default=None)
    new_eng.add_argument("--name", default=None, help="Friendly engagement name")
    new_eng.add_argument(
        "--in-scope",
        action="append",
        default=None,
        dest="in_scope",
        help="In-scope host/IP/CIDR (repeatable; comma-separated allowed)",
    )
    new_eng.add_argument(
        "--out-of-scope",
        action="append",
        default=None,
        dest="out_of_scope",
        help="Out-of-scope host/IP/CIDR (repeatable; comma-separated allowed)",
    )
    new_eng.add_argument(
        "--summary",
        default=None,
        help="Short engagement summary surfaced to Layer B",
    )
    new_eng.set_defaults(func=cmd_engagement_new)
```

- [ ] **Step 5: Run the Task 2 tests + the CLI suite**

Run: `uv run pytest tests/unit/test_engagement_cmd.py tests/integration/test_engagement_new_flags.py tests/unit/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add redrobin/cli_helpers/engagement_cmd.py redrobin/cli.py tests/unit/test_engagement_cmd.py tests/integration/test_engagement_new_flags.py
git commit -m "feat(cli): add --name/--in-scope/--out-of-scope/--summary to engagement new (#135)"
```

---

### Task 3: Warn loudly on empty-scope at daemon load + banner (#142)

**Files:**
- Modify: `redrobin/api/lifespan_helpers.py:259-271` (set `app.state.scope_empty` + WARN)
- Modify: `redrobin/application/startup_banner.py:16-23,50-56` (add `scope_empty` param + line)
- Modify: `redrobin/api/app.py:114-122` (pass `scope_empty`)
- Test: `tests/integration/test_engagement_empty_scope_warn.py` (create), `tests/unit/test_startup_banner.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_engagement_empty_scope_warn.py`:

```python
"""#142: a loaded engagement with empty in_scope must surface LOUDLY, not
silently deny-all. The daemon sets app.state.scope_empty and logs a WARNING."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.application.engagement_service import EngagementService


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    yield tmp_path


def test_empty_scope_engagement_sets_flag_and_warns(isolated_root, caplog):
    svc = EngagementService(root=isolated_root)
    svc.new("empty-eng")  # no --in-scope -> empty scope
    svc.use("empty-eng")

    app = create_app()
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            assert app.state.engagement is not None
            assert app.state.scope_empty is True
    assert any("empty" in r.message.lower() for r in caplog.records)


def test_scoped_engagement_flag_false(isolated_root):
    svc = EngagementService(root=isolated_root)
    svc.new("scoped-eng", in_scope=["corp.local"])
    svc.use("scoped-eng")

    app = create_app()
    with TestClient(app):
        assert app.state.scope_empty is False


def test_no_engagement_flag_false(isolated_root):
    # Wildcard fallback (no engagement) is a DIFFERENT case (already banner-warned);
    # scope_empty is reserved for "engagement loaded but scope empty".
    app = create_app()
    with TestClient(app):
        assert app.state.engagement is None
        assert app.state.scope_empty is False
```

Create `tests/unit/test_startup_banner.py`:

```python
"""Banner surfaces an empty-scope warning (Plan H #142)."""
from __future__ import annotations

from redrobin.application.startup_banner import format_banner
from redrobin.infrastructure.config import Settings


def _banner(**kw):
    defaults = dict(
        settings=Settings(),
        rules_count=3,
        engagement_name="Acme Q2",
        version="0.1.0",
        adapter_names=[],
    )
    defaults.update(kw)
    return format_banner(**defaults)


def test_banner_warns_when_scope_empty():
    out = _banner(scope_empty=True)
    assert "EMPTY" in out
    assert "BLOCKED" in out


def test_banner_no_scope_warning_when_scoped():
    out = _banner(scope_empty=False)
    assert "EMPTY" not in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/integration/test_engagement_empty_scope_warn.py tests/unit/test_startup_banner.py -v`
Expected: FAIL — `AttributeError: ... 'scope_empty'` (state never set) and `TypeError: format_banner() got an unexpected keyword argument 'scope_empty'`.

- [ ] **Step 3: Set `scope_empty` + WARN in the lifespan**

In `redrobin/api/lifespan_helpers.py`, replace the `if engagement is None: ... else: ...` block (lines 259-271) with:

```python
    if engagement is None:
        scope = ScopeEnforcer(in_scope=["*"], out_of_scope=[])
        app.state.engagement = None
        app.state.scope_empty = False
    else:
        scope = ScopeEnforcer(
            in_scope=engagement.in_scope, out_of_scope=engagement.out_of_scope
        )
        app.state.engagement = engagement
        # Keep app.state.engagement_id consistent with the loaded slug so the
        # working-memory reducer + downstream consumers see a single ID.
        app.state.engagement_id = engagement.slug
        # #142: an engagement with empty in_scope is fail-closed deny-all — the
        # gate will BLOCK every targeted command (jump/psexec/wmi/winrm/...) as
        # out-of-scope. That's the correct safe posture, but it MUST be loud, not
        # silent. Surface it via state (banner) + a WARNING log.
        app.state.scope_empty = not engagement.in_scope
        if app.state.scope_empty:
            logger.warning(
                "engagement %r loaded with EMPTY in_scope - every targeted "
                "command (jump/psexec/wmi/winrm/link) will be BLOCKED as "
                "out-of-scope. Set scope: `redrobin engagement new <slug> "
                "--in-scope <host/cidr>` or edit %s/%s/engagement.yaml and "
                "restart the daemon.",
                engagement.slug,
                engagements_dir,
                engagement.slug,
            )

    app.state.scope = scope

    return None, None
```

- [ ] **Step 4: Add the `scope_empty` line to the banner**

In `redrobin/application/startup_banner.py`, add `scope_empty: bool = False,` to the `format_banner` signature (after `adapter_names: list[str],` on line 22):

```python
def format_banner(
    *,
    settings: Settings,
    rules_count: int,
    engagement_name: str | None,
    version: str,
    adapter_names: list[str],
    scope_empty: bool = False,
) -> str:
```

Then replace the engagement `if/else` block (lines 50-56) with:

```python
    if engagement_name:
        lines.append(f"  Engagement: {engagement_name}")
        if scope_empty:
            lines.append(
                "  Scope:      EMPTY - WARNING: every targeted command will be "
                'BLOCKED (out-of-scope). Set scope via "redrobin engagement new '
                '--in-scope ..." or edit engagement.yaml + restart.'
            )
    else:
        lines.append(
            '  Engagement: (none) - WARNING: verdicts will NOT be persisted. '
            'Run "redrobin engagement use <slug>" first.'
        )
```

- [ ] **Step 5: Pass `scope_empty` from the lifespan into the banner**

In `redrobin/api/app.py`, replace the `format_banner(...)` call (lines 114-122) with:

```python
    _banner = format_banner(
        settings=settings,
        rules_count=len(app.state.rules_loader.list_rules()),
        engagement_name=(
            app.state.engagement.name if app.state.engagement is not None else None
        ),
        version=_version,
        adapter_names=list(app.state.cs_adapters.keys()),
        scope_empty=getattr(app.state, "scope_empty", False),
    )
```

- [ ] **Step 6: Run the Task 3 tests + the lifespan/scope suite**

Run: `uv run pytest tests/integration/test_engagement_empty_scope_warn.py tests/unit/test_startup_banner.py tests/integration/test_engagement_scope.py tests/integration/test_engagement_active_file.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add redrobin/api/lifespan_helpers.py redrobin/application/startup_banner.py redrobin/api/app.py tests/integration/test_engagement_empty_scope_warn.py tests/unit/test_startup_banner.py
git commit -m "feat(daemon): warn loudly on empty-scope engagement at load + banner (#142)"
```

---

### Task 4: Creation-time empty-scope notice + #135 end-to-end acceptance

**Files:**
- Modify: `redrobin/cli_helpers/engagement_cmd.py` (`run_new` empty-scope notice)
- Test: `tests/unit/test_engagement_cmd.py` (append), `tests/integration/test_engagement_new_flags.py` (append acceptance)

- [ ] **Step 1: Write the failing notice + acceptance tests**

Append to `tests/unit/test_engagement_cmd.py`:

```python
def test_run_new_warns_when_no_scope(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.application.engagement_service import EngagementService
    from redrobin.cli_helpers.engagement_cmd import run_new

    svc = EngagementService(root=tmp_path / ".redrobin")
    rc = run_new(svc=svc, slug="no-scope", client=None)
    assert rc == 0
    err = capsys.readouterr().err
    assert "scope" in err.lower()
    assert "BLOCKED" in err


def test_run_new_no_warn_when_scoped(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.application.engagement_service import EngagementService
    from redrobin.cli_helpers.engagement_cmd import run_new

    svc = EngagementService(root=tmp_path / ".redrobin")
    rc = run_new(svc=svc, slug="scoped", client=None, in_scope=["corp.local"])
    assert rc == 0
    assert "BLOCKED" not in capsys.readouterr().err
```

Append to `tests/integration/test_engagement_new_flags.py`:

```python
def test_new_then_boot_yields_usable_scope(tmp_path, monkeypatch):
    """#135 acceptance: `engagement new --in-scope` + `use` -> daemon loads a
    REAL scope, so a targeted in-scope command PASSES and an out-of-scope one
    BLOCKS. No hand-editing of engagement.yaml."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    from redrobin.cli import main

    assert main(["init", "--non-interactive"]) == 0
    assert main(["engagement", "new", "acme", "--in-scope", "10.10.0.0/16"]) == 0
    assert main(["engagement", "use", "acme"]) == 0

    from redrobin.api.app import create_app

    app = create_app()
    with TestClient(app):
        assert app.state.scope_empty is False
        scope = app.state.scope
        assert scope.check(["10.10.0.5"]) is None          # in-scope -> PASS
        assert scope.check(["8.8.8.8"]) is not None         # out-of-scope -> BLOCK
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_engagement_cmd.py::test_run_new_warns_when_no_scope "tests/integration/test_engagement_new_flags.py::test_new_then_boot_yields_usable_scope" -v`
Expected: FAIL — the notice assertion fails (no warning printed yet); the acceptance test passes only once Tasks 1-3 are in (it should already pass on scope, but `scope_empty` is from Task 3 — run after Task 3).

- [ ] **Step 3: Add the empty-scope notice to `run_new`**

In `redrobin/cli_helpers/engagement_cmd.py`, in `run_new`, insert the notice immediately before the final `return 0` (after the two `print(...)` lines):

```python
    if not eng.in_scope:
        print(
            "[!] No in-scope targets set - targeted commands (jump/psexec/wmi/"
            "winrm/link) will be BLOCKED as out-of-scope until you set scope. "
            "Re-run with --in-scope <host/cidr>, or edit the engagement.yaml "
            "above.",
            file=sys.stderr,
        )
    return 0
```

- [ ] **Step 4: Run the Task 4 tests**

Run: `uv run pytest tests/unit/test_engagement_cmd.py tests/integration/test_engagement_new_flags.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add redrobin/cli_helpers/engagement_cmd.py tests/unit/test_engagement_cmd.py tests/integration/test_engagement_new_flags.py
git commit -m "feat(cli): notice on empty scope at creation + #135 end-to-end acceptance"
```

---

### Task 5: Full-suite regression + manual CLI smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite the way CI does**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run pytest tests/ -q`
Expected: all PASS / skipped, 0 errors. (The `REDROBIN_ALLOW_DEGRADED=1` prefix avoids the pre-existing API-key boot-validation errors in `test_adapter_events_ws.py` that are unrelated to this plan; CI sets the keys as secrets.)

If anything unrelated to Plan H fails, STOP and report — do not "fix" out-of-scope tests.

- [ ] **Step 2: Manual CLI smoke in a throwaway root (no pollution of ~/.redrobin)**

Run:
```bash
RR="$(mktemp -d 2>/dev/null || echo "${TEMP}/rr_planh_smoke")"; mkdir -p "$RR"
REDROBIN_ROOT="$RR" uv run redrobin engagement new acme --in-scope 10.10.0.0/16,corp.local --summary "smoke"
echo "--- written engagement.yaml ---"; cat "$RR/engagements/acme/engagement.yaml"
REDROBIN_ROOT="$RR" uv run redrobin engagement new bare   # expect the empty-scope notice
```
Expected:
- `acme/engagement.yaml` has top-level `slug: acme`, `in_scope: [10.10.0.0/16, corp.local]`, `summary: smoke` — NO `id`, NO nested `scope:`.
- `engagement new bare` prints `[!] No in-scope targets set ... BLOCKED ...`.

Then remove the throwaway root: `rm -rf "$RR"`.

- [ ] **Step 3: Final commit (if Step 1/2 surfaced any in-scope fix)**

Only if a fix was needed:
```bash
git add -A
git commit -m "test(engagement): close Plan H regression gaps"
```

---

## Self-Review

**Spec coverage:**
- #135 writer-emits-wrong-schema → Task 1 (unify model + write loader schema) + round-trip test. ✓
- #135 no way to set scope from CLI → Task 2 (`--in-scope/--out-of-scope/--summary/--name`). ✓
- #135 acceptance ("engagement new + a jump works, no hand-editing") → Task 4 Step 1 `test_new_then_boot_yields_usable_scope`. ✓
- #142 silent empty-scope → Task 3 (lifespan WARN + `scope_empty` state + banner line) + Task 4 (CLI creation notice). ✓
- Fail-closed posture (NOT wildcard seed) → preserved: empty in_scope still deny-all; only made loud + settable. ✓
- Unify/remove dead domain model → Task 1 Step 3 (remove `Engagement`/`EngagementScope`, keep `validate_slug`). ✓
- Migration of broken on-disk YAMLs → INTENTIONALLY DROPPED (the 2 test engagements were deleted 2026-05-25; `acme-q2-2026`/`e2e-verify` already load fine; the writer fix prevents new ones — no `migrate` command, per scope decision).

**Placeholder scan:** no TBD/TODO; every code step shows complete code; every test step shows the full test; every run step has an exact command + expected result. ✓

**Type/name consistency:** the unified `Engagement` (`engagement_loader.py`) is imported by `engagement_service.py` (return type) and `lifespan_helpers.py` (unchanged — fields `slug/name/in_scope/out_of_scope/summary` all preserved, `client` added with default). `new()` kwargs (`name/client/in_scope/out_of_scope/summary`) match `run_new`'s forwarding and `_csv_list`'s output type (`list[str]`). `scope_empty` is set in `lifespan_helpers` (`app.state.scope_empty`), read in `app.py` (`getattr(..., "scope_empty", False)`), and is a `format_banner` kwarg — matching name throughout. `eng.id` → `eng.slug` fixed at both readers (`engagement_cmd.py`, `test_engagement_service.py`). ✓

**Layering:** `engagement_service` (application) importing `Engagement` from `engagement_loader` (application) is same-layer — no import-linter `layers` violation. `validate_slug` stays in `domain`. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-25-plan-h-engagement-new.md`.

Per the established workflow: next is `/auditing-plans` (audit this plan against the codebase before implementing), then `/subagent-driven-development` (Opus 4.7, fresh subagent per task + two-stage review). Verification = the unit/integration tests above (no live beacon needed for Plan H).
