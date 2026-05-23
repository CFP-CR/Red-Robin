"""Renders the daemon startup banner.

Lives in the application layer because both ``redrobin.api.app`` (FastAPI
lifespan) and ``redrobin.cli`` (future ``redrobin status``/diagnostics)
consume it — keeping it out of ``cli_helpers`` avoids a presentation-layer
import from ``redrobin.api`` and satisfies the import-linter ``layers``
contract.

NEVER print secret values. Only print whether they are set/unset.
"""
from __future__ import annotations

from redrobin.infrastructure.config import Settings


def format_banner(
    *,
    settings: Settings,
    rules_count: int,
    engagement_name: str | None,
    version: str,
    adapter_names: list[str],
) -> str:
    """Return a multi-line banner summarising the resolved daemon config.

    Args:
        settings: Fully-resolved Settings (paths derived, env applied).
        rules_count: Number of Sigma rules loaded by ``RulesLoader``.
        engagement_name: Friendly name of the active engagement, or None
            when running with the wildcard scope fallback.
        version: Daemon version string (``importlib.metadata.version``).
        adapter_names: Logical names of every CS adapter the lifespan
            successfully built (empty list when no adapters loaded).

    Returns:
        A newline-joined banner. Caller is responsible for printing to
        stderr (we never touch stdout — the daemon may pipe JSON there).

    Security:
        Secret values for ``ANTHROPIC_API_KEY`` and ``VOYAGE_API_KEY`` are
        NEVER included in the output. We render the literal string
        ``"set"`` when present and ``"MISSING (degraded)"`` when not.
    """
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"  Red Robin v{version}")
    lines.append("=" * 70)
    lines.append(f"  Listening:  http://{settings.host}:{settings.port}")
    lines.append(f"  Root:       {settings.root}")
    if engagement_name:
        lines.append(f"  Engagement: {engagement_name}")
    else:
        lines.append(
            '  Engagement: (none) - WARNING: verdicts will NOT be persisted. '
            'Run "redrobin engagement use <slug>" first.'
        )
    lines.append(f"  Rules:      {rules_count}")
    lines.append(
        f"  Adapters:   {', '.join(adapter_names) if adapter_names else '(none)'}"
    )
    lines.append(
        f"  Anthropic:  {'set' if settings.anthropic_api_key else 'MISSING (degraded)'}"
    )
    lines.append(
        f"  Voyage:     {'set' if settings.voyage_api_key else 'MISSING (degraded)'}"
    )
    lines.append(
        f"  L5 window:  {settings.l5_window} (every {settings.l5_interval_sec}s)"
    )
    lines.append(
        f"  Promoter:   min={settings.promoter_min_occurrences} "
        f"(every {settings.promoter_interval_sec}s)"
    )
    lines.append("=" * 70)
    return "\n".join(lines)


__all__ = ["format_banner"]
