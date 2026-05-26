"""Project an engagement's scope onto its working memory (#156).

``engagement.yaml`` is the source of truth for ``client`` + scope; the
working-memory JSON mirrors them so the TUI (Working Memory panel + Inspector)
shows the real scope instead of ``(none)``. Applied at engagement creation and
re-applied at daemon engagement-load. Only ``client``/``scope_in_scope``/
``scope_out_of_scope`` are touched — all other working-memory state is preserved.
"""

from __future__ import annotations

from dataclasses import replace

from redrobin.application.engagement_loader import Engagement
from redrobin.domain.memory import WorkingMemory


def apply_engagement_scope(wm: WorkingMemory, engagement: Engagement) -> WorkingMemory:
    """Return a copy of ``wm`` with ``client`` + scope set from ``engagement``."""
    return replace(
        wm,
        client=engagement.client,
        scope_in_scope=list(engagement.in_scope),
        scope_out_of_scope=list(engagement.out_of_scope),
    )
