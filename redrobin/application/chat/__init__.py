"""redrobin/application/chat — the chat transcript's application layer.

Also the facade for its store: re-exports the infrastructure ``ChatHistoryRepo`` so the
api layer can wire the recorder at daemon boot without violating the importlinter
contract ``no-direct-infra-from-presentation``. Mirrors ``working_memory_lifecycle.py``.
"""

from __future__ import annotations

from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo

__all__ = ["ChatHistoryRepo"]
