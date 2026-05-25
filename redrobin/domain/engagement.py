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
