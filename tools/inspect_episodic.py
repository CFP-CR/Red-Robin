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
    tables = [
        r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
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
