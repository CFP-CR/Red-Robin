"""redrobin/application/engagement_loader.py — load engagement.yaml.

Slice 6 of Phase 2: reads the per-engagement ROE file from
``~/.redrobin/engagements/<slug>/engagement.yaml`` (root configurable via
``EngagementLoader(engagements_root=...)``) and returns an immutable
:class:`Engagement` dataclass the lifespan uses to build the verdict
working-memory scope projection (``apply_engagement_scope``).

Schema (v0):
  slug: <str>
  name: <str>
  in_scope: list[str]       # hostnames, suffix patterns, CIDRs, or "*"
  out_of_scope: list[str]   # same shape; out_of_scope wins on conflict
  summary: <str>            # short engagement summary surfaced to Layer B
  rules_of_engagement: list[str]   # ROE bullet lines (static; operator-authored)
  objectives:                       # mission goals (static definition; the
    - id: <str>                     #   assistant owns dynamic status/progress)
      goal: <str>
      priority: low|medium|high

Missing fields fall back to safe defaults so a malformed file still produces a
usable :class:`Engagement` (the lifespan logs the slug + path on load).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from redrobin.domain.memory import Objective

logger = logging.getLogger(__name__)


def _as_str_list(value: object, *, field: str) -> list[str]:
    """Coerce a yaml value to ``list[str]``. A non-list scalar (e.g. the operator
    wrote ``in_scope: host01`` without the ``- `` list syntax) degrades to ``[]``
    WITH a warning — never char-explodes (``list("host01")`` -> ['h','o',...]),
    which would silently corrupt scope matching. ``None`` (absent key) is a quiet
    empty list."""
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning(
            "engagement.yaml: %r must be a yaml list, got %s (%r); ignoring it",
            field,
            type(value).__name__,
            value,
        )
        return []
    return [str(x) for x in value]


@dataclass(frozen=True)
class Engagement:
    slug: str
    name: str
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    summary: str = ""
    client: str = ""
    rules_of_engagement: list[str] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)


class EngagementLoader:
    def __init__(self, engagements_root: Path) -> None:
        self._root = engagements_root

    def load(self, slug: str) -> Engagement:
        eng_file = self._root / slug / "engagement.yaml"
        if not eng_file.is_file():
            raise FileNotFoundError(f"engagement file not found: {eng_file}")
        data = yaml.safe_load(eng_file.read_text()) or {}
        objectives_raw = data.get("objectives")
        if objectives_raw is not None and not isinstance(objectives_raw, list):
            logger.warning(
                "engagement.yaml: 'objectives' must be a yaml list, got %s; ignoring it",
                type(objectives_raw).__name__,
            )
        objectives: list[Objective] = []
        for o in objectives_raw if isinstance(objectives_raw, list) else []:
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
            in_scope=_as_str_list(data.get("in_scope"), field="in_scope"),
            out_of_scope=_as_str_list(data.get("out_of_scope"), field="out_of_scope"),
            summary=str(data.get("summary") or "").strip(),
            client=str(data.get("client") or ""),
            rules_of_engagement=_as_str_list(
                data.get("rules_of_engagement"), field="rules_of_engagement"
            ),
            objectives=objectives,
        )
