"""redrobin/infrastructure/persistence/working_memory_repo.py — JSON-backed working memory.

Per spec §5.2: working memory lives at `~/.redrobin/engagements/<slug>/working_memory.json`.
Mutable (no immutable history); operator-curated + auto-updated from events.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from redrobin.domain.memory import (
    Account,
    Host,
    Objective,
    ToolUsage,
    WorkingMemory,
)


class WorkingMemoryRepo:
    """Read/write a single engagement's working memory JSON file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> WorkingMemory:
        if not self.path.exists():
            raise FileNotFoundError(f"working memory not found: {self.path}")
        data = json.loads(self.path.read_text())
        return self._from_dict(data)

    def save(self, wm: WorkingMemory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(wm), indent=2, default=str))

    @staticmethod
    def _from_dict(data: dict) -> WorkingMemory:
        return WorkingMemory(
            engagement_id=data["engagement_id"],
            client=data.get("client", ""),
            scope_in_scope=data.get("scope_in_scope", []),
            scope_out_of_scope=data.get("scope_out_of_scope", []),
            objectives=[Objective(**o) for o in data.get("objectives", [])],
            current_phase=data.get("current_phase", "not_started"),
            kill_chain_status=data.get("kill_chain_status", {}),
            rules_of_engagement=data.get("rules_of_engagement", []),
            hosts=[Host(**h) for h in data.get("hosts", [])],
            accounts=[Account(**a) for a in data.get("accounts", [])],
            defenses_observed=data.get("defenses_observed", []),
            tools_used=[ToolUsage(**t) for t in data.get("tools_used", [])],
        )
