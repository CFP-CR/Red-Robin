"""TUI WebSocket client — pure handle_message() tests.

We intentionally avoid testing the asyncio reconnect loop in unit tests; that
is covered in the integration test that boots a real TestClient WS.
"""

from __future__ import annotations

import json

from redrobin.tui.state import TUIState
from redrobin.tui.ws_client import handle_message


class _InvalidateCounter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def test_handle_message_drops_invalid_json():
    state = TUIState()
    inv = _InvalidateCounter()

    handle_message(raw="not-json-at-all", state=state, invalidate=inv)

    assert len(state.events) == 0
    # No paint should be triggered when we drop a malformed frame.
    assert inv.calls == 0


def test_handle_message_replaces_snapshot_on_working_memory_snapshot():
    state = TUIState()
    inv = _InvalidateCounter()
    msg = {
        "type": "working_memory_snapshot",
        "payload": {
            "engagement_id": "eng-1",
            "hosts": [{"name": "DC01"}],
            "credentials": [],
            "findings": [],
            "sessions": [],
        },
    }

    handle_message(raw=json.dumps(msg), state=state, invalidate=inv)

    assert state.snapshot["engagement_id"] == "eng-1"
    assert state.snapshot["hosts"] == [{"name": "DC01"}]
    # Snapshot replacements still trigger a redraw.
    assert inv.calls == 1
    # No event was appended for a snapshot frame.
    assert len(state.events) == 0


def test_handle_message_appends_layer_a_verdict():
    state = TUIState()
    inv = _InvalidateCounter()
    msg = {
        "type": "opsec.verdict",
        "payload": {
            "correlation_id": "corr-001",
            "verdict": "PASS",
            "source": "layer_a",
        },
    }

    handle_message(raw=json.dumps(msg), state=state, invalidate=inv)

    assert len(state.events) == 1
    assert state.events[0]["type"] == "opsec.verdict"
    assert state.events[0]["payload"]["source"] == "layer_a"
    assert inv.calls == 1


def test_handle_message_layer_b_updates_existing_verdict_in_place():
    state = TUIState()
    inv = _InvalidateCounter()
    # Seed Layer A verdict.
    layer_a = {
        "type": "opsec.verdict",
        "payload": {
            "correlation_id": "corr-001",
            "verdict": "PASS",
            "source": "layer_a",
        },
    }
    handle_message(raw=json.dumps(layer_a), state=state, invalidate=inv)

    # Layer B retroactive correction with same correlation_id.
    layer_b = {
        "type": "opsec.verdict",
        "payload": {
            "correlation_id": "corr-001",
            "verdict": "BLOCK",
            "previous_verdict": "PASS",
            "reason": "lsass touch",
            "source": "layer_b",
        },
    }
    handle_message(raw=json.dumps(layer_b), state=state, invalidate=inv)

    # Single entry, mutated in place.
    assert len(state.events) == 1
    payload = state.events[0]["payload"]
    assert payload["verdict"] == "BLOCK"
    assert payload["previous_verdict"] == "PASS"
    assert payload["reason"] == "lsass touch"
    assert payload["source"] == "layer_b"
    # Both calls triggered invalidate.
    assert inv.calls == 2


def test_handle_message_layer_b_appends_when_no_matching_correlation():
    state = TUIState()
    inv = _InvalidateCounter()
    msg = {
        "type": "opsec.verdict",
        "payload": {
            "correlation_id": "corr-missing",
            "verdict": "BLOCK",
            "source": "layer_b",
            "reason": "no earlier verdict",
        },
    }

    handle_message(raw=json.dumps(msg), state=state, invalidate=inv)

    # No earlier Layer A event existed, but we still surface the correction.
    assert len(state.events) == 1
    assert state.events[0]["payload"]["correlation_id"] == "corr-missing"
    assert state.events[0]["payload"]["source"] == "layer_b"


def test_handle_message_appends_unknown_event_type():
    state = TUIState()
    inv = _InvalidateCounter()
    msg = {"type": "adapter.connected", "payload": {"adapter_id": "cs-1"}}

    handle_message(raw=json.dumps(msg), state=state, invalidate=inv)

    assert len(state.events) == 1
    assert state.events[0]["type"] == "adapter.connected"
    assert inv.calls == 1


def test_handle_message_layer_a_default_source_appends():
    """If 'source' is missing on opsec.verdict, treat as layer_a (default)."""
    state = TUIState()
    inv = _InvalidateCounter()
    msg = {
        "type": "opsec.verdict",
        "payload": {"correlation_id": "corr-x", "verdict": "PASS"},
    }

    handle_message(raw=json.dumps(msg), state=state, invalidate=inv)

    assert len(state.events) == 1
    assert state.events[0]["payload"]["correlation_id"] == "corr-x"
