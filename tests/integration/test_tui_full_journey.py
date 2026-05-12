"""Slice 8 integration — build the TUI application and verify the layout.

The Application itself can't be ``run`` without a real terminal, so we build it
and walk the layout container tree to assert the named panels are reachable.
Task 8.6 adds a true daemon-side roundtrip test below.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from prompt_toolkit.application import Application
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from redrobin.api.app import create_app
from redrobin.tui.app import build_tui_app
from redrobin.tui.dispatch import OperatorDispatcher
from redrobin.tui.state import TUIState
from redrobin.tui.ws_client import handle_message


@pytest.fixture
def pty_input():
    with create_pipe_input() as inp:
        yield inp


@pytest.fixture
def pty_output() -> DummyOutput:
    return DummyOutput()


def _walk(container):
    """Yield every container/window in the layout tree.

    Recurses through ``get_children()`` when present (HSplit/VSplit/FloatContainer/
    ConditionalContainer) and through ``content`` for Window leaves.
    """
    yield container
    if hasattr(container, "get_children"):
        for child in container.get_children():
            yield from _walk(child)
    if hasattr(container, "floats"):
        for f in getattr(container, "floats", []) or []:
            yield from _walk(f.content if hasattr(f, "content") else f)


def _collect_names(container) -> set[str]:
    names: set[str] = set()
    for c in _walk(container):
        name = getattr(c, "_rr_name", None)
        if name:
            names.add(name)
    return names


@pytest.fixture
def state() -> TUIState:
    return TUIState()


@pytest.fixture
def dispatcher() -> OperatorDispatcher:
    client = httpx.AsyncClient()
    return OperatorDispatcher(client=client, base_url="http://127.0.0.1:8765", token=None)


def test_build_tui_app_returns_application_instance(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    assert isinstance(app, Application)


def test_build_tui_app_exposes_three_named_panels(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    names = _collect_names(app.layout.container)
    assert "sessions_panel" in names
    assert "events_panel" in names
    assert "memory_panel" in names


def test_build_tui_app_exposes_input_buffer(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    names = _collect_names(app.layout.container)
    assert "input_buffer" in names


def test_build_tui_app_exposes_inspector_float(state, dispatcher, pty_input, pty_output):
    """Inspector overlay must be reachable in the float container tree."""
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    names = _collect_names(app.layout.container)
    assert "inspector_overlay" in names


def test_build_tui_app_has_key_bindings(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    assert app.key_bindings is not None
    # KeyBindings or merged bindings — both expose .bindings.
    bindings = getattr(app.key_bindings, "bindings", None)
    assert bindings is not None
    assert len(bindings) > 0


# ---------------------------------------------------------------------------
# Task 8.6 — end-to-end TUI <-> daemon roundtrip
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    yield root


@pytest.fixture
def daemon(_isolated_root):
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _drive_handle(state: TUIState, raw_json: str) -> None:
    """Feed one raw frame into handle_message without an invalidate hook."""
    invalidate_calls = {"n": 0}

    def _inv() -> None:
        invalidate_calls["n"] += 1

    handle_message(raw=raw_json, state=state, invalidate=_inv)


def test_roundtrip_publish_via_bus_received_by_ws(daemon):
    """Publish opsec.verdict on the bus → TUI WS frame → handle_message → state mutated."""
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    app, client = daemon
    token = get_or_create_token()
    state = TUIState()

    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        # The daemon sends an initial snapshot — drive it through the TUI
        # consumer so the snapshot is reflected on state.
        snapshot_raw = json.dumps(ws.receive_json())
        _drive_handle(state, snapshot_raw)
        assert state.snapshot is not None  # populated, even if empty

        # Now publish a verdict — this is what the verdict pipeline does
        # in production. The TUI WS endpoint forwards it on the same socket.
        client.portal.call(
            app.state.event_bus.publish,
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "corr-roundtrip",
                    "verdict": "PASS",
                    "command": "whoami",
                    "source": "layer_a",
                },
            },
        )

        frame = ws.receive_json()
        _drive_handle(state, json.dumps(frame))

    # After the roundtrip, the verdict is in our TUIState event log.
    assert len(state.events) == 1
    evt = state.events[0]
    assert evt["type"] == "opsec.verdict"
    assert evt["payload"]["correlation_id"] == "corr-roundtrip"
    assert evt["payload"]["command"] == "whoami"
    assert evt["payload"]["source"] == "layer_a"


def test_roundtrip_layer_b_correction_mutates_layer_a_in_place(daemon):
    """Layer A verdict then Layer B retroactive correction → single mutated event."""
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    app, client = daemon
    token = get_or_create_token()
    state = TUIState()

    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        # Drain the snapshot.
        _drive_handle(state, json.dumps(ws.receive_json()))

        client.portal.call(
            app.state.event_bus.publish,
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "corr-001",
                    "verdict": "PASS",
                    "command": "mimikatz",
                    "source": "layer_a",
                },
            },
        )
        _drive_handle(state, json.dumps(ws.receive_json()))

        # Retroactive correction with the same correlation_id.
        client.portal.call(
            app.state.event_bus.publish,
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "corr-001",
                    "verdict": "BLOCK",
                    "previous_verdict": "PASS",
                    "reason": "LLM flagged lsass touch",
                    "source": "layer_b",
                },
            },
        )
        _drive_handle(state, json.dumps(ws.receive_json()))

    # Two frames received, but the second one mutated the first in place.
    assert len(state.events) == 1
    payload = state.events[0]["payload"]
    assert payload["verdict"] == "BLOCK"
    assert payload["previous_verdict"] == "PASS"
    assert payload["source"] == "layer_b"
    assert "lsass" in payload["reason"]


def test_roundtrip_initial_snapshot_replaces_state_snapshot(daemon):
    """The first frame from /v1/tui is always working_memory_snapshot."""
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    app, client = daemon
    token = get_or_create_token()
    state = TUIState()
    # Tag snapshot before connect — the daemon's snapshot must overwrite it.
    state.replace_snapshot({"engagement_id": "pre-existing", "hosts": [], "credentials": [], "findings": [], "sessions": []})

    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "working_memory_snapshot"
        _drive_handle(state, json.dumps(first))

    # The daemon's snapshot (empty engagement_id when no REDROBIN_WORKING_MEMORY_PATH)
    # has overwritten our pre-existing tag.
    assert state.snapshot.get("engagement_id") != "pre-existing"
    # All five expected snapshot keys remain present.
    for key in ("engagement_id", "hosts", "credentials", "findings", "sessions"):
        assert key in state.snapshot
