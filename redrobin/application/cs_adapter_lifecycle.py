"""redrobin/application/cs_adapter_lifecycle.py — application facade for CS adapter loading.

Re-exports the infrastructure loader so the api layer can build CS clients at
daemon boot without violating the importlinter contract
``no-direct-infra-from-presentation``. Tests should monkeypatch
``_default_transport`` on this module (or the underlying infrastructure module
directly — both point at the same function object).
"""

from __future__ import annotations

from redrobin.infrastructure.cs_adapter_loader import (
    AdapterConfig,
    _default_transport,
    build_adapter,
    load_adapter_configs,
)

__all__ = [
    "AdapterConfig",
    "build_adapter",
    "load_adapter_configs",
    "_default_transport",
]
