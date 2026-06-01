"""Project an engagement's definition onto its working memory (#103/#156).

``engagement.yaml`` is the source of truth for the static *definition* —
``client`` + scope + rules-of-engagement + objective goals. The working-memory
JSON mirrors them so the TUI (Working Memory panel + Inspector) shows the real
engagement instead of ``(none)``. Applied at engagement creation and re-applied
at daemon engagement-load.

Per the writer-split: the operator authors the definition (here); the future AI
assistant owns the dynamic *interpretation* (objective ``status``/``progress_pct``,
``current_phase``, ``kill_chain_status``). So objectives are MERGED BY ID — a
daemon reload refreshes an objective's definition (``goal``/``priority``) from
yaml while PRESERVING any interpretation already in working memory, and never
touches ``current_phase``/``kill_chain_status``. Definition-writer and
interpretation-writer do not clobber each other.
"""

from __future__ import annotations

from dataclasses import replace

from redrobin.application.engagement_loader import Engagement
from redrobin.domain.memory import Objective, WorkingMemory


def apply_engagement_scope(wm: WorkingMemory, engagement: Engagement) -> WorkingMemory:
    """Return a copy of ``wm`` with client/scope/ROE set from ``engagement`` and
    objectives merged by id (definition refreshed, interpretation preserved)."""
    existing = {o.id: o for o in wm.objectives}
    merged_objectives: list[Objective] = []
    for spec in engagement.objectives:
        prior = existing.get(spec.id)
        if prior is not None:
            # definition (goal/priority) from yaml; interpretation preserved from WM
            merged_objectives.append(
                replace(
                    spec,
                    status=prior.status,
                    progress_pct=prior.progress_pct,
                    milestones=list(prior.milestones),
                    pending=list(prior.pending),
                )
            )
        else:
            merged_objectives.append(spec)  # new objective — clean interpretation
    return replace(
        wm,
        client=engagement.client,
        scope_in_scope=list(engagement.in_scope),
        scope_out_of_scope=list(engagement.out_of_scope),
        rules_of_engagement=list(engagement.rules_of_engagement),
        objectives=merged_objectives,
    )
