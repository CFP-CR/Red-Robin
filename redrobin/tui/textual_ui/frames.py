"""Generic WS-frame routing registry — the core extensibility seam.

A frame is ``{"type": str, "payload": dict}``. Features register a handler for
their type; ``dispatch`` routes by type. Adding a new frame type (e.g.
``advisor.recommendation``) is a single ``@registry.register(...)`` — it never
requires editing existing handlers or the dispatch logic.
"""

from __future__ import annotations

from typing import Any, Callable

FrameHandler = Callable[[Any, dict[str, Any]], None]


class Registry:
    """Maps a frame ``type`` to a handler ``(target, payload) -> None``."""

    def __init__(self) -> None:
        self._handlers: dict[str, FrameHandler] = {}

    def register(self, frame_type: str) -> Callable[[FrameHandler], FrameHandler]:
        def deco(fn: FrameHandler) -> FrameHandler:
            self._handlers[frame_type] = fn
            return fn

        return deco

    def has_handler(self, frame_type: str) -> bool:
        return frame_type in self._handlers

    def dispatch(self, *, target: Any, frame: dict[str, Any]) -> bool:
        """Route one frame to its handler. Returns True if handled."""
        handler = self._handlers.get(frame.get("type", ""))
        if handler is None:
            return False
        handler(target, frame.get("payload") or {})
        return True


__all__ = ["Registry", "FrameHandler"]
