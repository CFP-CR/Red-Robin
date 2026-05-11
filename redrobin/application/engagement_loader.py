"""redrobin/application/engagement_loader.py — load engagement.yaml.

Slice 6 of Phase 2: reads the per-engagement ROE file from
``~/.redrobin/engagements/<slug>/engagement.yaml`` (root configurable via
``EngagementLoader(engagements_root=...)``) and returns an immutable
:class:`Engagement` dataclass the lifespan uses to build the verdict
pipeline's :class:`~redrobin.application.scope_enforcer.ScopeEnforcer`.

Schema (v0):
  slug: <str>
  name: <str>
  in_scope: list[str]       # hostnames, suffix patterns, CIDRs, or "*"
  out_of_scope: list[str]   # same shape; out_of_scope wins on conflict
  summary: <str>            # short engagement summary surfaced to Layer B

Missing fields fall back to safe defaults so a malformed file still produces a
usable :class:`Engagement` (the lifespan logs the slug + path on load).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class Engagement:
    slug: str
    name: str
    in_scope: list[str]
    out_of_scope: list[str]
    summary: str


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
        )
