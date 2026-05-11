"""redrobin/infrastructure/llm/api_keys.py — strict env-var-only API key loading.

Mitigation for spec §6 T2 (API key exfiltration). Refuses to read API keys from
config.toml; the operator must export ANTHROPIC_API_KEY and VOYAGE_API_KEY as
environment variables. Documented in SECURITY.md.
"""

from __future__ import annotations

import os
from typing import Any


def get_anthropic_key() -> str:
    """Load ANTHROPIC_API_KEY from env. Raise if missing."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY env var not set. "
            "Red Robin refuses to read API keys from config files for security "
            "(see spec §6 T2). Export it:\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'"
        )
    return key


def get_voyage_key() -> str:
    """Load VOYAGE_API_KEY from env. Raise if missing."""
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise RuntimeError(
            "VOYAGE_API_KEY env var not set. "
            "Red Robin refuses to read API keys from config files for security "
            "(see spec §6 T2). Export it:\n"
            "  export VOYAGE_API_KEY='pa-...'"
        )
    return key


def refuse_api_key_in_config(toml_data: dict[str, Any]) -> None:
    """Reject any `api_key` field in config.toml [llm] or [voyage] sections.

    Called by the config loader at daemon startup. If the operator ever places
    an api_key field in config.toml, fail loud at startup rather than silently
    accept it.
    """
    if "api_key" in toml_data.get("llm", {}):
        raise ValueError(
            "Found api_key in config.toml [llm] section — this is not allowed. "
            "Set ANTHROPIC_API_KEY env var instead. See SECURITY.md for rationale."
        )
    if "api_key" in toml_data.get("voyage", {}):
        raise ValueError(
            "Found api_key in config.toml [voyage] section — this is not allowed. "
            "Set VOYAGE_API_KEY env var instead. See SECURITY.md for rationale."
        )
