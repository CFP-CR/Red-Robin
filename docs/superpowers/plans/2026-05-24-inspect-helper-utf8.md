# Plan B — `tools/inspect_episodic.py` UTF-8-safe (closes #131)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with Opus 4.7. Single atomic commit.

**Goal:** Ship a version-controlled, UTF-8-safe copy of the operator's `inspect_episodic.py` helper in `tools/inspect_episodic.py`. The operator's local copy at `C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py` crashes with `UnicodeEncodeError` on Windows when Layer B reasons contain Unicode chars (em-dash, en-dash, arrow, smart quotes), because PowerShell's default cp1252 stdout encoding can't represent them. This was confirmed during T14 smoke 2026-05-23 — the script died mid-output during TC4 cross-check.

**Architecture:** Add a new `tools/inspect_episodic.py` mirroring the operator's existing script behavior + an explicit `sys.stdout` reconfigure to UTF-8 at script start. The operator's local copy at `~/redrobin-smoke/` stays untouched (operator's choice whether to replace it). Update `docs/operator-setup.md` to point at the in-repo version going forward.

**Tech Stack:** Python 3 stdlib (sqlite3, sys, io). No new deps.

**Verified facts driving this plan:**

1. **Bug surface confirmed:** read `C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py` 2026-05-24 — uses bare `print()` for all output. No `sys.stdout` reconfigure. Verified crash mode during 2026-05-23 smoke (`UnicodeEncodeError: 'charmap' codec can't encode character '→' in position 292`).
2. **Fix mechanism verified:** Python 3.7+ has `sys.stdout.reconfigure(encoding='utf-8')` which mutates the existing stream in-place. Alternative for older Python: `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`. The repo's `pyproject.toml` requires Python 3.11+ (per prior verification), so `reconfigure` is safe.
3. **No `tools/` directory exists yet** at repo root — verified by `ls`. Creating it adds a new dir.
4. **No conflicts** with existing files: `tools/inspect_episodic.py` doesn't exist; `docs/operator-setup.md` exists and currently references the operator's local path (verify and update reference).

**Risk note:** Single new file + one docs update. No production code touched. No tests required — the helper isn't shipped with the daemon, it's operator tooling.

**Starting state assumption:** `dev-juan` HEAD = `57c575d` (Plan A close) or later.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `tools/inspect_episodic.py` | Create | UTF-8-safe inspect helper, mirrors operator's local script + fixes unicode bug |
| `docs/operator-setup.md` | Modify | Update reference to point at `tools/inspect_episodic.py` (in-repo, not personal path) |
| `docs/superpowers/backlog.md` | Modify | Close out `#131` (status table row + entry heading + body) with Task 1 commit SHA |

---

## Task 1: Create `tools/inspect_episodic.py`

**Files:**
- Create: `tools/inspect_episodic.py`

- [ ] **Step 1: Create the file**

Create `tools/inspect_episodic.py` with EXACTLY this content (operator's script + UTF-8 reconfigure + one-line CLI arg for DB path as a quality-of-life addition while we're touching it; the original hardcoded same-dir behavior is preserved as the default):

```python
"""Inspect a Red Robin episodic.db.

UTF-8-safe replacement for the operator's local _inspect_episodic.py helper.
The original crashed on Windows PowerShell with UnicodeEncodeError when Layer B
reasons contained Unicode characters (em-dash, arrow, smart quotes) because
cp1252 is the default stdout encoding. This version explicitly reconfigures
stdout to UTF-8 at start so any reason text renders cleanly.

Usage:
    python tools/inspect_episodic.py                              # defaults to ~/.redrobin/episodic.db
    python tools/inspect_episodic.py <path/to/episodic.db>        # explicit path
    python tools/inspect_episodic.py engagements/<slug>/episodic.db    # per-engagement DB

Closes backlog #131.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows (PowerShell defaults to cp1252). Python 3.7+.
# Without this, Layer B reasons containing em-dash / arrow / smart quotes
# crash the script mid-output with UnicodeEncodeError.
sys.stdout.reconfigure(encoding="utf-8")


def _default_db_path() -> Path:
    """Resolve the canonical episodic.db location.

    Default to the root-level ~/.redrobin/episodic.db (per the architecture
    confirmed in the engagement-isolation work — per-engagement DBs live
    under ~/.redrobin/engagements/<slug>/episodic.db and are passed
    explicitly via argv when needed).
    """
    return Path.home() / ".redrobin" / "episodic.db"


def main(argv: list[str]) -> int:
    db_path = Path(argv[1]) if len(argv) > 1 else _default_db_path()
    if not db_path.exists():
        print(f"ERROR: episodic.db not found at {db_path}", file=sys.stderr)
        return 2

    db = sqlite3.connect(db_path)
    cur = db.cursor()

    print(f"=== {db_path} ===\n")

    print("=== TABLES ===")
    for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        print(f"  - {r[0]}")

    print("\n=== SCHEMA ===")
    for r in cur.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall():
        print(r[0] or "(no sql)")
        print()

    print("=== ROW COUNTS ===")
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for tbl in tables:
        n = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {n}")

    print("\n=== LAST 5 ROWS PER TABLE ===")
    for tbl in tables:
        print(f"\n--- {tbl} ---")
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({tbl})").fetchall()]
        print(f"  columns: {cols}")
        try:
            rows = cur.execute(f"SELECT * FROM {tbl} ORDER BY ROWID DESC LIMIT 5").fetchall()
            for row in rows:
                d = dict(zip(cols, row))
                # truncate long fields so the per-row dict stays readable
                for k, v in d.items():
                    if isinstance(v, str) and len(v) > 200:
                        d[k] = v[:200] + "...(truncated)"
                print(f"    {d}")
        except sqlite3.OperationalError as e:
            print(f"  ERROR: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Changes from the operator's local version:
1. **UTF-8 reconfigure** at script start (line 26) — the core #131 fix.
2. **CLI arg for DB path** — `python tools/inspect_episodic.py [path]` (the operator's version hardcoded `Path(__file__).parent / "episodic.db"`, requiring manual DB copy). Default falls to `~/.redrobin/episodic.db` (the root DB per the architecture).
3. **Exit code 2 on missing DB** — operator-friendly error vs. raw `sqlite3.OperationalError`.
4. **Docstring + usage block** — discoverable via `--help` (next iteration could add argparse; for now docstring + `_default_db_path` self-documents).
5. **Path header line** — prints the DB path being inspected so the operator knows which engagement they're looking at.

Everything else is byte-for-byte equivalent: TABLES + SCHEMA + ROW COUNTS + LAST 5 ROWS PER TABLE, same truncation logic, same error handling.

- [ ] **Step 2: Verify the script runs cleanly**

```powershell
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
python tools/inspect_episodic.py
```

Expected: prints `=== C:\Users\jcspi\.redrobin\episodic.db ===` header, then TABLES/SCHEMA/ROW COUNTS/LAST 5 ROWS, no `UnicodeEncodeError`. The DB might contain Layer B reasons with `→`, `—`, smart quotes — all should render correctly under UTF-8 stdout.

If the DB doesn't exist at the default path, the script prints `ERROR: episodic.db not found at ...` and exits 2. Operator can pass an explicit path.

- [ ] **Step 3: Verify the per-engagement DB path works**

```powershell
python tools/inspect_episodic.py C:\Users\jcspi\.redrobin\engagements\tc4-smoke\episodic.db
```

Expected: prints contents of the per-engagement DB.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add tools/inspect_episodic.py
git commit -m "tools: UTF-8-safe inspect_episodic.py helper (closes #131)

Operator's local helper at C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py
crashes on Windows PowerShell with UnicodeEncodeError when Layer B reasons
contain em-dash / arrow / smart-quote characters (verified 2026-05-23 T14
smoke: 'charmap codec can't encode character \\u2192'). Operator's local
file is outside the repo so the fix can't be committed there directly.

Ship the canonical version in tools/inspect_episodic.py instead:

  - sys.stdout.reconfigure(encoding=\"utf-8\") at start — fixes the core bug
  - CLI arg for DB path with sensible default (~/.redrobin/episodic.db) —
    eliminates the manual db-copy workflow the operator was doing
  - exit code 2 on missing DB — operator-friendly vs raw OperationalError
  - prints the DB path being inspected — explicit which engagement

Operator can replace their personal copy with:
  copy tools\\inspect_episodic.py C:\\Users\\jcspi\\redrobin-smoke\\

Per the architecture established in B2 (engagement isolation, #136),
the root ~/.redrobin/episodic.db is the global DB. Per-engagement DBs
live at ~/.redrobin/engagements/<slug>/episodic.db and are passed
explicitly as an argv (eliminates the helper's prior need to be co-
located with the DB).

Spec: docs/superpowers/plans/2026-05-24-inspect-helper-utf8.md"
```

---

## Task 2: Update `operator-setup.md` to reference the in-repo helper

**Files:**
- Modify: `docs/operator-setup.md`

- [ ] **Step 1: Locate the existing reference**

```bash
grep -n "_inspect_episodic\|inspect_episodic\|redrobin-smoke" docs/operator-setup.md
```

Expected: exactly **one** occurrence to update at `docs/operator-setup.md:284` (verified 2026-05-24 — `python C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py`), embedded in a `powershell` code block at lines 283-285 with a follow-up parenthetical at lines 287-288.

Other matches in specs/prior plans/backlog are historical artifacts — out of scope.

- [ ] **Step 2: Replace lines 283-288 as a unit**

The current block at `docs/operator-setup.md:283-288` is:

```markdown
` ` `powershell
python C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py
` ` `

(That helper script is in the engagement root, created during the
2026-05-18 smoke session — copy it to new engagements as needed.)
```

Both the code block AND the parenthetical sentence need to be replaced as a unit — leaving the "engagement root" sentence intact while the code block points at `tools/` would create contradictory guidance.

Replace with (use the Edit tool — anchor on the unique `_inspect_episodic.py` token):

```markdown
` ` `bash
uv run python tools/inspect_episodic.py
` ` `

(Helper lives at `tools/inspect_episodic.py` in the repo. It defaults to
`~/.redrobin/episodic.db`; pass an explicit path to inspect a per-engagement DB,
e.g. `uv run python tools/inspect_episodic.py ~/.redrobin/engagements/<slug>/episodic.db`.)
```

Rationale for each change:
- `powershell` → `bash` — matches the rest of the file's snippet conventions (the surrounding `uv run` snippets are tagged `bash`).
- `python` → `uv run python` — uses the project's environment-managed interpreter (consistent with `Section 6` directly below).
- "engagement root" → "`tools/` in the repo" — the new architecture (root DB at `~/.redrobin/episodic.db` per `#136`) makes the old "copy to engagements" instruction obsolete.

- [ ] **Step 3: Commit**

```bash
git add docs/operator-setup.md
git commit -m "docs(operator-setup): point at tools/inspect_episodic.py (in-repo)

Plan B Task 2 — update the operator-setup doc to reference the
version-controlled UTF-8-safe helper at tools/inspect_episodic.py
instead of the operator's old personal copy at
C:\\Users\\jcspi\\redrobin-smoke\\_inspect_episodic.py (outside the repo).
The new helper accepts an optional path arg, so the snippet now
runs from the repo root with no manual DB copy required.

Backlog: pairs with #131 resolution at tools/inspect_episodic.py."
```

---

## Task 3: Close out `#131` in the backlog

Matches the pattern Plan A used for `#125`/`#127`/`#137` — close-out lives in the same PR so the backlog stays honest.

**Files:**
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Capture the Task 1 commit SHA**

After Task 1's commit lands, capture its SHA. Pass it as `<TASK1_SHA>` below.

- [ ] **Step 2: Update the scope table row (line 16)**

Find:
```markdown
| B | 1 — operator helper | `#131` | ~10 LOC + decision | Pending |
```

Replace with:
```markdown
| B | 1 — operator helper | `#131` | ~10 LOC + decision | **DONE 2026-05-24** (commit `<TASK1_SHA>`) |
```

- [ ] **Step 3: Replace the `#131` entry (lines 81-85)**

Find the existing heading + body at `backlog.md:81-85`:

```markdown
### #131 — `_inspect_episodic.py` crashes on Windows cp1252
**Source:** T14 smoke 2026-05-22, `UnicodeEncodeError: '→'` from print statements when Layer B reason contains `→`.
**Symptom:** PowerShell's default cp1252 encoding crashes the operator helper script on common Unicode chars from Layer B reasoning.
**Files:** `C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py` (operator-local, NOT in repo).
**Fix:** Add `sys.stdout.reconfigure(encoding="utf-8")` at top of script. Trivial one-line fix; operator can do it themselves.
```

Replace with:

```markdown
### #131 — `_inspect_episodic.py` crashes on Windows cp1252 (RESOLVED 2026-05-24 commit `<TASK1_SHA>`)
Plan B T1 shipped. Created `tools/inspect_episodic.py` — UTF-8-safe in-repo replacement for the operator's local `_inspect_episodic.py`. Adds `sys.stdout.reconfigure(encoding="utf-8")` at start (fixes the cp1252 crash on Layer B Unicode reasons), CLI arg for DB path with `~/.redrobin/episodic.db` as the default, and an explicit missing-DB error instead of sqlite3 auto-creating an empty file. T2 updated `docs/operator-setup.md` to point at the in-repo helper.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/backlog.md
git commit -m "docs(backlog): mark #131 RESOLVED — UTF-8-safe inspect helper

Plan B T3 — backlog hygiene. Tasks 1+2 shipped tools/inspect_episodic.py
and updated docs/operator-setup.md to reference it. Pattern matches the
close-outs for #125/#127/#137 from Plan A: status table row flipped to
DONE with the Task 1 SHA, entry heading updated to RESOLVED, body
rewritten to summarize what shipped.

Spec: docs/superpowers/plans/2026-05-24-inspect-helper-utf8.md"
```

---

## Self-Review

- **Spec coverage:** #131 is fixed by Task 1 (UTF-8 reconfigure). Bonus QoL (CLI arg, exit codes, header) is in-scope because we're touching the script anyway and the changes are minimal.
- **Placeholder scan:** No TBD/TODO/"see above". All code is concrete.
- **Type / name consistency:** `_default_db_path()` defined in Task 1 → no external callers. CLI accepts argv[1] as path → matches docstring usage examples.

Ready for execution.
