"""Boot-time validation. Exits 2 on hard failure; prints WARN on degraded ok.

Slice 3 of v0.1.x hardening closes a Phase-2 gap where the daemon would
silently fall into ``_DegradedLayerB`` when API keys were missing. Operators
must now explicitly opt in to running without LLM judgment via the
``--allow-degraded`` CLI flag or ``REDROBIN_ALLOW_DEGRADED=1`` env var
(both wire through to ``Settings.allow_degraded_layer_b``).

Callsites:
  - ``redrobin.cli.cmd_daemon_start`` calls with ``require_root_exists=True``
    so the daemon fails BEFORE binding the port.
  - ``redrobin.api.app.lifespan`` calls with the default
    ``require_root_exists=False`` so TestClient can boot the app without
    a real ``~/.redrobin`` on disk.
"""
from __future__ import annotations

import sys

from redrobin.infrastructure.config import Settings


def validate_for_boot(settings: Settings, *, require_root_exists: bool = False) -> None:
    """Validate Settings at daemon boot. SystemExit(2) on hard failure.

    Hard failures (must fix or pass --allow-degraded):
    - ANTHROPIC_API_KEY missing (Layer B cannot judge)
    - VOYAGE_API_KEY missing (KB retrieval cannot embed)
    - REDROBIN_ROOT does not exist (only when require_root_exists=True;
      ``redrobin daemon start`` calls with this flag; lifespan in tests
      omits it so create_app() in TestClient can boot without a real root)

    Soft warnings (printed to stderr, do not exit):
    - REDROBIN_ENGAGEMENT_SLUG set but engagement.yaml not found
      -> wildcard scope; documented user-visible behavior
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not settings.anthropic_api_key:
        if settings.allow_degraded_layer_b:
            warnings.append(
                "ANTHROPIC_API_KEY missing — running with degraded Layer B "
                "(no LLM judgment)"
            )
        else:
            errors.append(
                "ANTHROPIC_API_KEY required for Layer B. Set it, or pass "
                "--allow-degraded / REDROBIN_ALLOW_DEGRADED=1 to disable Layer B."
            )
    if not settings.voyage_api_key:
        if settings.allow_degraded_layer_b:
            warnings.append("VOYAGE_API_KEY missing — KB retrieval disabled")
        else:
            errors.append(
                "VOYAGE_API_KEY required for KB retrieval. "
                "Set it, or pass --allow-degraded."
            )

    if require_root_exists and not settings.root.is_dir():
        errors.append(
            f"REDROBIN_ROOT does not exist: {settings.root}. "
            "Run `redrobin init` to set it up."
        )

    if settings.engagement_slug:
        engagements_dir = settings.engagements_dir
        # ``_derive_paths`` guarantees ``engagements_dir`` is non-None; the
        # assertion documents that invariant for type-checkers.
        assert engagements_dir is not None
        eng_file = engagements_dir / settings.engagement_slug / "engagement.yaml"
        if not eng_file.is_file():
            warnings.append(
                f"REDROBIN_ENGAGEMENT_SLUG={settings.engagement_slug} but "
                f"{eng_file} not found — using wildcard scope"
            )

    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
