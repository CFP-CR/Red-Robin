"""``redrobin memory edit`` business logic — extracted in Slice 12."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_memory_edit(*, target: Path) -> int:
    if not target.exists():
        print(f"[!] {target} does not exist", file=sys.stderr)
        return 1
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
    subprocess.call([editor, str(target)])
    return 0
