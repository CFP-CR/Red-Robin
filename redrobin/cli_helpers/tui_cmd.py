"""``redrobin tui`` business logic — extracted in Slice 12.

The CLI wrapper in :mod:`redrobin.cli` loads ``.env`` and resolves the bearer
token from the local file when ``--token`` is omitted; :func:`run_tui` here
just launches the textual app.
"""

from __future__ import annotations

from redrobin.application.transport_auth import get_or_create_token


def resolve_token(explicit: str | None) -> str | None:
    """Return ``explicit`` if provided, else read the operator's local token.

    Falling back to ``get_or_create_token()`` means ``redrobin tui`` Just Works
    against the local daemon without a ``--token`` flag. Failures are silenced
    so the TUI can still show its login banner if the file is missing.
    """
    if explicit is not None:
        return explicit
    try:
        return get_or_create_token()
    except Exception:
        return None


def run_tui(
    *,
    connect_ws: str,
    connect_http: str,
    token: str | None,
) -> int:
    # The Textual client is a read-only WS observer — it consumes the daemon's
    # /v1/tui stream and never POSTs commands, so connect_http is unused here
    # (kept in the signature for CLI compatibility).
    from redrobin.tui.textual_ui.app import RedRobinApp

    RedRobinApp(ws_url=connect_ws, token=token).run()
    return 0
