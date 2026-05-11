"""redrobin/tui/panels/working_memory.py — Left pane: engagement state."""

from __future__ import annotations

from redrobin.domain.memory import WorkingMemory
from redrobin.tui.i18n import Labels


def render_working_memory(wm: WorkingMemory, labels: Labels) -> str:
    """Render the left-pane plain-text view of working memory.

    Format (compact, fixed-width-friendly):
        ENGAGEMENT  acme-q2-2026                Day 4 / ~10
        PHASE       credential_access            ████░░░░ 60%
        KILL CHAIN  recon✓ initial✓ priv-esc✓ creds⚙ lateral— exfil—

        OBJECTIVES
          obj_1  Compromise primary DC          ████░░ 60% in_progress

        HOSTS (n)
          DC01           WS2022      CrowdStrike    SYSTEM

        ACCOUNTS (n)
          acme\\svc_sql  plaintext   (DC01)

        DEFENSES OBSERVED
          CrowdStrike Falcon on DC01

        TOOLS USED
          nanodump.bof   (DC01, no detection)

        LESSONS  (n)
          (empty until episodic memory wired in v0.1.1+)
    """
    lines: list[str] = []
    lines.append(f"{labels.tui.engagement:<11} {wm.engagement_id}")
    lines.append(f"{labels.tui.phase:<11} {wm.current_phase}")

    # Kill chain
    if wm.kill_chain_status:
        chain_parts: list[str] = []
        for phase, status in wm.kill_chain_status.items():
            mark = "✓" if status == "complete" else "⚙" if status == "in_progress" else "—"
            chain_parts.append(f"{phase}{mark}")
        lines.append(f"{labels.tui.kill_chain:<11} {' '.join(chain_parts)}")
    lines.append("")

    if wm.objectives:
        lines.append(labels.tui.objectives)
        for obj in wm.objectives:
            bar_len = 20
            filled = int(bar_len * obj.progress_pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            lines.append(f"  {obj.id:<7} {obj.goal:<35} {bar} {obj.progress_pct:>3}% {obj.status}")
        lines.append("")

    if wm.hosts:
        lines.append(f"{labels.tui.hosts} ({len(wm.hosts)})")
        for host in wm.hosts:
            lines.append(
                f"  {host.name:<14} {(host.os or '-'):<11} "
                f"{(host.edr_detected or '-'):<15} {(host.access_level or '-')}"
            )
        lines.append("")

    if wm.accounts:
        lines.append(f"{labels.tui.accounts} ({len(wm.accounts)})")
        for acct in wm.accounts:
            lines.append(f"  {acct.name:<20} {acct.kind:<10} ({acct.source_host or '-'})")
        lines.append("")

    if wm.defenses_observed:
        lines.append(labels.tui.defenses)
        for defense in wm.defenses_observed:
            lines.append(f"  {defense}")
        lines.append("")

    if wm.tools_used:
        lines.append(labels.tui.tools)
        for tool in wm.tools_used:
            lines.append(f"  {tool.name:<15} ({tool.used_on}, {tool.detection_observed})")
        lines.append("")

    return "\n".join(lines)
