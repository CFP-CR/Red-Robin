"""Shared WebSocket helpers for the ``/v1/tui`` integration tests.

A plain module, deliberately NOT a ``conftest.py``: an ordinary import is visible at the
call site and changes fixture resolution for nothing else under ``tests/integration``.
Pytest never collects it because the filename matches neither of the default ``python_files``
patterns (``test_*.py`` / ``*_test.py``) and this repo sets no pytest config to widen them; the
leading underscore is a readability convention marking it as support code, not the mechanism.
"""

from __future__ import annotations

import threading
from typing import Any


def receive_json_bounded(ws, timeout: float = 5.0) -> Any:
    """A BOUNDED ``ws.receive_json()``.

    starlette's TestClient receives through a portal with no timeout, and after the app
    returns its ``_run`` sleeps forever, so the stream never closes either. Without this,
    a missing frame wedges the whole pytest session rather than failing a test.
    ``pytest-timeout`` is not installed, so nothing else would ever end the run.

    EVERY receive of a connect frame must go through this, in both ``test_tui_ws.py`` and
    ``test_tui_ws_broadcast.py``. A test that skips connect frames and only then publishes
    the event it wants is the worst case: if a connect frame regresses away, the skip
    blocks BEFORE the publish, so no frame can ever arrive to satisfy it.
    """
    box: dict[str, Any] = {}

    def _pull() -> None:
        try:
            box["frame"] = ws.receive_json()
        except Exception as exc:  # the portal can be torn down under us on timeout
            box["error"] = exc

    thread = threading.Thread(target=_pull, daemon=True)
    thread.start()
    thread.join(timeout)
    if "frame" not in box:
        raise AssertionError(f"no frame within {timeout}s (last error: {box.get('error')!r})")
    return box["frame"]
