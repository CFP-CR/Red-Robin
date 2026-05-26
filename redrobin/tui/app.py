"""Main full-screen TUI application — three panels, input, inspector overlay.

Layout::

    +-------------+--------------------------+------------+
    | Sessions    |  Events                  |  Working   |
    | (left)      |  (center)                |  Memory    |
    |             |                          |  (right)   |
    +-------------+--------------------------+------------+
    | Status bar                                           |
    +------------------------------------------------------+
    | Input                                                |
    +------------------------------------------------------+

A FloatContainer wraps the root layout; the memory inspector overlay is a
``Float`` rendered when ``state.focus_overlay == "inspector"``.

Each named container/window carries an ``_rr_name`` attribute so tests can walk
the layout tree and assert reachability of the three panels and the input
buffer.

``run_tui()`` is the operator-facing entry point: it builds the WS URL with the
``?token=`` query param expected by ``/v1/tui``, opens the daemon's HTTP API
through an ``OperatorDispatcher``, and runs the prompt_toolkit Application
alongside the auto-reconnecting WS consumer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.containers import WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

from redrobin.tui.dispatch import OperatorDispatcher
from redrobin.tui.i18n import Labels, load_labels
from redrobin.tui.inspector import render_inspector as render_inspector_full
from redrobin.tui.key_bindings import build_key_bindings
from redrobin.tui.panels.working_memory import render_working_memory
from redrobin.tui.slash_commands import parse as parse_slash
from redrobin.tui.snapshot_adapter import working_memory_from_snapshot
from redrobin.tui.state import TUIState
from redrobin.tui.ws_client import run_client

logger = logging.getLogger(__name__)


def _tag(container, name: str):
    """Attach a stable name to a container so tests can find it."""
    container._rr_name = name  # type: ignore[attr-defined]
    return container


def _format_event_line(event: dict[str, Any]) -> str:
    etype = event.get("type", "?")
    payload = event.get("payload") or {}
    if etype == "opsec.verdict":
        verdict = payload.get("verdict", "?")
        cmd = payload.get("command", "")
        source = payload.get("source", "?")
        reason = payload.get("reason", "")
        return f"[{verdict:<8}] {cmd}  ({source}) {('— ' + reason) if reason else ''}"
    # Generic event line.
    return f"[{etype}] {json.dumps(payload, default=str)}"


def _render_events(state: TUIState) -> str:
    if not state.events:
        return "(no events yet)"
    return "\n".join(_format_event_line(e) for e in state.events)


def _render_sessions(state: TUIState) -> str:
    sessions = state.snapshot.get("sessions") or []
    if not sessions:
        return "(no active sessions)"
    lines = []
    for s in sessions:
        if isinstance(s, dict):
            # Session serializes as beacon_id/hostname/user/integrity (#143).
            ident = s.get("beacon_id") or "?"
            host = s.get("hostname") or "-"
            user = s.get("user") or "-"
            integ = s.get("integrity") or "-"
            lines.append(f"  {ident:<14} {host:<18} {user} [{integ}]")
        else:
            lines.append(f"  {s}")
    return "\n".join(lines)


def _render_memory(state: TUIState, labels: Labels) -> str:
    """Working-memory panel: rich rendering of hosts, accounts, kill chain,
    objectives, tools and defenses via ``panels/working_memory.py``.

    The daemon's WS snapshot is a dict; hydrate it into a typed
    :class:`WorkingMemory` via the snapshot adapter so the panel renderer
    stays decoupled from the wire shape.
    """
    wm = working_memory_from_snapshot(state.snapshot)
    rendered = render_working_memory(wm, labels)
    return rendered or "(snapshot empty — waiting for daemon)"


def _render_inspector(state: TUIState, labels: Labels) -> str:
    """Inspector overlay: full engagement-state view per spec §5.9.6.

    Wraps :func:`render_inspector` from ``tui/inspector.py``, hydrating the
    WS snapshot into a typed :class:`WorkingMemory` and surfacing the latest
    verdict's reason as a single Episodic lesson so operators get verdict
    context even while the rich Episodic store isn't yet streamed to the TUI.
    """
    wm = working_memory_from_snapshot(state.snapshot)
    latest = state.latest_verdict()
    lessons: list[str] = []
    if latest is not None:
        payload = latest.get("payload") or {}
        verdict = payload.get("verdict", "?")
        command = payload.get("command", "?")
        reason = payload.get("reason", "")
        if reason:
            lessons.append(f"[{verdict}] {command} — {reason}")
        else:
            lessons.append(f"[{verdict}] {command}")
    return render_inspector_full(wm, episodic_lessons=lessons, labels=labels)


def _panel(get_text, title: str, name: str, weight: int = 1):
    """Build a titled panel: top bar with title, then body text. Tagged for tests."""
    title_window = _tag(
        Window(
            content=FormattedTextControl(text=lambda: f" {title} "),
            height=1,
            style="class:panel-title",
            align=WindowAlign.LEFT,
        ),
        f"{name}_title",
    )
    body_window = _tag(
        Window(
            content=FormattedTextControl(text=get_text),
            wrap_lines=True,
            style="class:panel-body",
        ),
        f"{name}_body",
    )
    return _tag(HSplit([title_window, body_window], width=D(weight=weight)), name)


def build_tui_app(
    *,
    state: TUIState,
    dispatcher: OperatorDispatcher,
    input=None,  # noqa: A002 — prompt_toolkit uses this name
    output=None,
    labels: Labels | None = None,
) -> Application:
    """Construct the prompt_toolkit Application. Pure builder — no I/O.

    The ``input``/``output`` parameters let tests inject ``create_pipe_input()``
    and ``DummyOutput`` so the Application can be instantiated in environments
    without a real terminal (CI, pytest on Windows under MINGW).

    ``labels`` defaults to the i18n pack detected from ``$LANG``. Tests can
    pass an explicit :class:`Labels` to force English regardless of the host
    locale.
    """
    if labels is None:
        labels = Labels(load_labels())
    input_buffer = Buffer(multiline=False)

    sessions_pane = _panel(lambda: _render_sessions(state), "Sessions", "sessions_panel", weight=2)
    events_pane = _panel(lambda: _render_events(state), "Events", "events_panel", weight=4)
    memory_pane = _panel(
        lambda: _render_memory(state, labels), "Working Memory", "memory_panel", weight=2
    )

    main_row = VSplit([sessions_pane, events_pane, memory_pane])

    status_bar = _tag(
        Window(
            content=FormattedTextControl(
                text=lambda: " ".join(
                    [
                        f"engagement: {state.snapshot.get('engagement_id') or '-'}",
                        f"events: {len(state.events)}",
                        f"overlay: {state.focus_overlay or 'none'}",
                    ]
                )
            ),
            height=1,
            style="class:status-bar",
        ),
        "status_bar",
    )

    input_window = _tag(
        Window(
            content=BufferControl(buffer=input_buffer),
            height=1,
            style="class:input",
        ),
        "input_buffer",
    )

    body = HSplit([main_row, status_bar, input_window])

    inspector_overlay = _tag(
        ConditionalContainer(
            content=Frame(
                Window(
                    content=FormattedTextControl(text=lambda: _render_inspector(state, labels)),
                    wrap_lines=True,
                    style="class:inspector-body",
                ),
                title="Memory Inspector",
            ),
            filter=Condition(lambda: state.focus_overlay == "inspector"),
        ),
        "inspector_overlay",
    )

    root = FloatContainer(
        content=body,
        floats=[Float(content=inspector_overlay, top=2, left=4, right=4, bottom=2)],
    )

    async def on_submit(text: str) -> None:
        """Route operator input.

        ``:rr <verb>`` and ``/<verb>`` are parsed locally by
        :func:`redrobin.tui.slash_commands.parse`. Verb-handling is surfaced
        as a synthetic event in the TUI stream — slash dispatch is operator-
        facing UI only; nothing crosses to the daemon. The bare ``inspector``
        verb toggles the inspector overlay locally so the keybinding has a
        textual alternative.

        Plain text is forwarded to the daemon's verdict endpoint via the
        dispatcher. Errors are caught and surfaced as synthetic events in the
        stream so the TUI never crashes on a transient daemon disconnect.
        """
        slash = parse_slash(text)
        if slash is not None:
            if slash.verb == "inspector":
                state.focus_overlay = "inspector" if state.focus_overlay != "inspector" else None
            state.append_event(
                {
                    "type": "tui.slash_command",
                    "payload": {"verb": slash.verb, "args": list(slash.args)},
                }
            )
            return
        try:
            await dispatcher.submit(text)
        except Exception as exc:  # noqa: BLE001 — operator must see this
            logger.warning("dispatch failed: %s", exc)
            state.append_event(
                {
                    "type": "tui.dispatch_error",
                    "payload": {"command": text, "error": str(exc)},
                }
            )

    def _invalidate() -> None:
        # Stored late so we can reference `app` after construction.
        if _app_ref["app"] is not None:
            _app_ref["app"].invalidate()

    _app_ref: dict[str, Application | None] = {"app": None}

    kb = build_key_bindings(
        state=state,
        input_buffer=input_buffer,
        on_submit=on_submit,
        invalidate=_invalidate,
    )

    style = Style.from_dict(
        {
            "panel-title": "reverse bold",
            "panel-body": "",
            "status-bar": "reverse",
            "input": "",
            "inspector-body": "",
        }
    )

    app_kwargs: dict[str, Any] = dict(
        layout=Layout(root, focused_element=input_window),
        key_bindings=kb,
        full_screen=True,
        style=style,
        mouse_support=False,
    )
    if input is not None:
        app_kwargs["input"] = input
    if output is not None:
        app_kwargs["output"] = output
    app: Application = Application(**app_kwargs)
    _app_ref["app"] = app
    return app


def _build_ws_url(connect_ws: str, token: str | None) -> str:
    """Append ``?token=`` to the WS URL — the daemon validates query auth."""
    if not token:
        return connect_ws
    split = urlsplit(connect_ws)
    existing = split.query
    new_q = urlencode({"token": token})
    query = f"{existing}&{new_q}" if existing else new_q
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


async def _amain(
    *,
    connect_ws: str,
    connect_http: str,
    token: str | None,
) -> None:
    state = TUIState()
    stop_event = asyncio.Event()
    ws_url = _build_ws_url(connect_ws, token)

    async with httpx.AsyncClient() as http_client:
        dispatcher = OperatorDispatcher(client=http_client, base_url=connect_http, token=token)
        app = build_tui_app(state=state, dispatcher=dispatcher)

        def _invalidate() -> None:
            try:
                app.invalidate()
            except Exception:
                pass

        ws_task = asyncio.create_task(
            run_client(
                url=ws_url,
                token=token,
                state=state,
                invalidate=_invalidate,
                stop_event=stop_event,
            )
        )
        try:
            await app.run_async()
        finally:
            stop_event.set()
            ws_task.cancel()
            try:
                await ws_task
            except (asyncio.CancelledError, Exception):
                pass


def run_tui(
    *,
    connect_ws: str,
    connect_http: str,
    token: str | None,
) -> None:
    """Blocking entry point — operator runs ``redrobin tui --connect-ws ...``."""
    asyncio.run(_amain(connect_ws=connect_ws, connect_http=connect_http, token=token))


# ---------------------------------------------------------------------------
# Backwards-compatible facade — the original ``run()`` function continues to
# accept no arguments and falls back to localhost defaults so the legacy
# ``redrobin tui`` call still works.
# ---------------------------------------------------------------------------
def run() -> None:
    """Legacy entry: `redrobin tui` with no flags defaults to localhost."""
    run_tui(
        connect_ws="ws://127.0.0.1:8765/v1/tui",
        connect_http="http://127.0.0.1:8765",
        token=None,
    )
