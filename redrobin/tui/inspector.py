"""redrobin/tui/inspector.py — Memory inspector overlay (Ctrl+I).

Per spec §5.9.6 + storyboard scene 10: full-engagement-state view including
hosts, accounts, defenses, tools, kill chain, lessons, episodic.

For v0.1: text rendering only — overlay activation handled by app.py
key binding in Task 39 (full-app integration).
"""

from __future__ import annotations

from redrobin.domain.memory import WorkingMemory
from redrobin.tui.i18n import Labels


def render_inspector(
    wm: WorkingMemory,
    *,
    episodic_lessons: list[str] | None = None,
    labels: Labels | None = None,
) -> str:
    """Render full-engagement-state overlay text."""
    if labels is None:
        from redrobin.tui.i18n import load_labels

        labels = Labels(load_labels())
    if episodic_lessons is None:
        episodic_lessons = []

    sections: list[str] = []
    sections.append(f"═══ MEMORY INSPECTOR — {wm.engagement_id} ═══\n")

    sections.append(f"{labels.tui.engagement}")
    sections.append(f"  ID: {wm.engagement_id}")
    sections.append(f"  Client: {wm.client or '-'}")
    sections.append(f"  Phase: {wm.current_phase}")
    sections.append("")

    sections.append("SCOPE")
    sections.append(f"  in_scope:     {wm.scope_in_scope or '[]'}")
    sections.append(f"  out_of_scope: {wm.scope_out_of_scope or '[]'}")
    sections.append("")

    sections.append("ROE")
    for r in wm.rules_of_engagement:
        sections.append(f"  - {r}")
    if not wm.rules_of_engagement:
        sections.append("  (none defined)")
    sections.append("")

    sections.append(f"{labels.tui.objectives} ({len(wm.objectives)})")
    for obj in wm.objectives:
        sections.append(
            f"  {obj.id}: {obj.goal} [{obj.status}, {obj.progress_pct}%, prio={obj.priority}]"
        )
        for m in obj.milestones:
            sections.append(f"    - milestone: {m}")
    sections.append("")

    sections.append("KILL CHAIN")
    for phase, status in wm.kill_chain_status.items():
        sections.append(f"  {phase}: {status}")
    if not wm.kill_chain_status:
        sections.append("  (not yet started)")
    sections.append("")

    sections.append(f"{labels.tui.hosts} ({len(wm.hosts)})")
    for host in wm.hosts:
        sections.append(
            f"  {host.name} [{host.os or '-'}] EDR={host.edr_detected or '-'} "
            f"access={host.access_level or '-'} integrity={host.integrity_level or '-'}"
        )
    sections.append("")

    sections.append(f"{labels.tui.accounts} ({len(wm.accounts)})")
    for acct in wm.accounts:
        sections.append(
            f"  {acct.name} [{acct.kind}] source={acct.source_host or '-'} "
            f"captured={acct.captured_at or '-'}"
        )
    sections.append("")

    sections.append(f"{labels.tui.defenses}")
    for d in wm.defenses_observed:
        sections.append(f"  - {d}")
    if not wm.defenses_observed:
        sections.append("  (none observed)")
    sections.append("")

    sections.append(f"{labels.tui.tools}")
    for tool in wm.tools_used:
        sections.append(
            f"  {tool.name} on {tool.used_on} at {tool.used_at} — {tool.detection_observed}"
        )
    if not wm.tools_used:
        sections.append("  (none yet)")
    sections.append("")

    sections.append(f"CREDENTIALS ({len(wm.credentials)})")
    for cred in wm.credentials:
        sections.append(
            f"  {cred.domain}\\{cred.username} "
            f"hash={cred.ntlm_hash or '-'} src={cred.source_beacon_id or '-'}"
        )
    if not wm.credentials:
        sections.append("  (none captured)")
    sections.append("")

    sections.append(f"FINDINGS ({len(wm.findings)})")
    for finding in wm.findings:
        sections.append(
            f"  [{finding.kind}] {finding.summary} (src={finding.source_beacon_id or '-'})"
        )
    if not wm.findings:
        sections.append("  (none yet)")
    sections.append("")

    sections.append(f"{labels.tui.lessons} ({len(episodic_lessons)})")
    for lesson in episodic_lessons:
        sections.append(f"  • {lesson}")
    if not episodic_lessons:
        sections.append("  (no lessons yet — episodic memory accumulates as engagement proceeds)")

    return "\n".join(sections)
