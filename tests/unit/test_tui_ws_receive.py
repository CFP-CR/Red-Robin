from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_operator_question_from_tui_is_published_with_source(monkeypatch, tmp_path):
    # A message the TUI sends over /v1/tui is published on the bus as operator.question
    # tagged source="operator" (so the assistant consumes it and the TUI won't re-render it).
    from redrobin.application.event_bus import EventBus
    from redrobin.api.tui_ws import _publish_operator_message  # pure helper (below)

    bus = EventBus()
    sub = await bus.subscribe()
    await _publish_operator_message(
        bus, {"type": "operator.question", "text": "path to DA?", "question_id": "q1"}
    )
    evt = sub.get_nowait()
    assert evt["type"] == "operator.question"
    assert evt["payload"]["text"] == "path to DA?"
    assert evt["payload"]["question_id"] == "q1"
    assert evt["payload"]["source"] == "operator"


@pytest.mark.asyncio
async def test_non_allowlisted_type_from_tui_is_dropped():
    from redrobin.application.event_bus import EventBus
    from redrobin.api.tui_ws import _publish_operator_message

    bus = EventBus()
    sub = await bus.subscribe()
    # the TUI must NOT be able to inject arbitrary events (e.g. engagement.milestone)
    await _publish_operator_message(
        bus, {"type": "engagement.milestone", "milestone_id": "x", "status": "COMPLETE"}
    )
    assert sub.empty()


@pytest.mark.asyncio
async def test_publish_operator_message_never_raises():
    from redrobin.application.event_bus import EventBus
    from redrobin.api.tui_ws import _publish_operator_message

    bus = EventBus()
    await _publish_operator_message(bus, "not a dict")  # type: ignore[arg-type]
    await _publish_operator_message(bus, {"no": "type"})
    # no assertion needed — the point is it does not raise


# --- Integration: a real /v1/tui socket exercises the concurrent send/receive
# refactor + the never-crash-on-bad-frame path (not covered by the helper tests).

from fastapi.testclient import TestClient  # noqa: E402

from redrobin.api.app import create_app  # noqa: E402
from redrobin.infrastructure.transport.bearer import get_or_create_token  # noqa: E402


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def test_operator_question_round_trips_over_socket(app_client):
    # An operator.question sent over the socket is published on the bus and fans
    # back out the same socket, tagged source="operator".
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # discard the initial snapshot
        ws.send_json({"type": "operator.question", "text": "path?", "question_id": "q1"})
        for _ in range(10):  # bounded: read frames until the operator.question arrives
            evt = ws.receive_json()
            if evt.get("type") == "operator.question":
                assert evt["payload"]["source"] == "operator"
                assert evt["payload"]["question_id"] == "q1"
                break
        else:  # pragma: no cover - only hit on regression
            pytest.fail("operator.question was never published back over the socket")


def test_malformed_inbound_frame_does_not_kill_socket(app_client):
    # C6 guard: a bad inbound frame must NOT crash the receive loop — the socket
    # must still deliver a subsequently published bus event.
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # discard the initial snapshot
        ws.send_text("not json")  # malformed — would kill an un-guarded receive loop
        client.portal.call(
            app.state.event_bus.publish,
            {"type": "opsec.verdict", "payload": {"verdict": "PASS"}},
        )
        for _ in range(10):  # bounded: read until the post-malformed event arrives
            evt = ws.receive_json()
            if evt.get("type") == "opsec.verdict":
                assert evt["payload"]["verdict"] == "PASS"
                break
        else:  # pragma: no cover - only hit on regression
            pytest.fail("socket stopped delivering after a malformed inbound frame")
