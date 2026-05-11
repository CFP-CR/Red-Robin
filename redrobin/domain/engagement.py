"""redrobin/domain/engagement.py — Engagement metadata + YAML schema."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class EngagementScope:
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)


@dataclass
class Engagement:
    id: str  # slug, e.g., "acme-q2-2026"
    client: str = ""
    scope: EngagementScope = field(default_factory=EngagementScope)
    rules_of_engagement: list[str] = field(default_factory=list)


def validate_slug(slug: str) -> None:
    """Engagement slugs are filesystem-safe + URL-safe."""
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid engagement slug {slug!r}. "
            "Use lowercase alphanumeric + hyphens/underscores (max 64 chars)."
        )
