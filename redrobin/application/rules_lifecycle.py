"""redrobin/application/rules_lifecycle.py — application facade for rules loader lifecycle.

Re-exports the infrastructure ``RulesLoader`` so the api layer can manage rules
hot-reload at daemon boot without violating the importlinter contract
``no-direct-infra-from-presentation``.
"""

from __future__ import annotations

from redrobin.infrastructure.rules_loader import RulesLoader

__all__ = ["RulesLoader"]
