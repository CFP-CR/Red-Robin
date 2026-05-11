"""TUIState — mutable container shared between WebSocket listener and panels."""
from __future__ import annotations

from redrobin.tui.state import TUIState


def test_append_event_respects_cap():
    state = TUIState(max_events=3)
    for i in range(5):
        state.append_event({"type": "x", "payload": {"i": i}})
    assert len(state.events) == 3
    # Deque truncates from the left, so we keep the most recent three.
    assert [e["payload"]["i"] for e in state.events] == [2, 3, 4]


def test_replace_snapshot_overwrites_shape():
    state = TUIState()
    new_snapshot = {
        "engagement_id": "eng-1",
        "hosts": [{"name": "DC01"}],
        "credentials": [],
        "findings": [],
        "sessions": [],
    }
    state.replace_snapshot(new_snapshot)
    assert state.snapshot == new_snapshot
    assert state.snapshot["engagement_id"] == "eng-1"
    assert state.snapshot["hosts"] == [{"name": "DC01"}]


def test_update_verdict_in_place_when_correlation_matches():
    state = TUIState()
    state.append_event(
        {
            "type": "opsec.verdict",
            "payload": {
                "correlation_id": "abc",
                "verdict": "PASS",
                "source": "layer_a",
            },
        }
    )
    before = list(state.events)

    state.update_verdict("abc", {"verdict": "BLOCK", "source": "layer_b", "reason": "lsass"})

    # No new event was appended — the existing one was mutated.
    assert len(state.events) == 1
    after = state.events[0]
    assert after["type"] == "opsec.verdict"
    assert after["payload"]["correlation_id"] == "abc"
    assert after["payload"]["verdict"] == "BLOCK"
    assert after["payload"]["source"] == "layer_b"
    assert after["payload"]["reason"] == "lsass"
    # Sanity: the original list was not preserved beyond reference (we mutated in place).
    assert before[0] is after


def test_update_verdict_appends_when_correlation_missing():
    state = TUIState()
    state.append_event(
        {"type": "opsec.verdict", "payload": {"correlation_id": "other", "verdict": "PASS"}}
    )
    state.update_verdict("missing", {"verdict": "BLOCK"})

    assert len(state.events) == 2
    appended = state.events[-1]
    assert appended["type"] == "opsec.verdict"
    assert appended["payload"]["correlation_id"] == "missing"
    assert appended["payload"]["verdict"] == "BLOCK"


def test_update_verdict_only_updates_most_recent_match():
    """If two verdicts share a correlation_id, only the most recent one is mutated."""
    state = TUIState()
    state.append_event(
        {"type": "opsec.verdict", "payload": {"correlation_id": "x", "verdict": "PASS"}}
    )
    state.append_event({"type": "other", "payload": {}})
    state.append_event(
        {"type": "opsec.verdict", "payload": {"correlation_id": "x", "verdict": "WARN"}}
    )

    state.update_verdict("x", {"verdict": "BLOCK"})
    # The most recent verdict for "x" was mutated; the older one is untouched.
    assert state.events[0]["payload"]["verdict"] == "PASS"
    assert state.events[2]["payload"]["verdict"] == "BLOCK"


def test_latest_verdict_returns_most_recent():
    state = TUIState()
    assert state.latest_verdict() is None

    state.append_event({"type": "opsec.verdict", "payload": {"correlation_id": "1"}})
    state.append_event({"type": "other", "payload": {}})
    state.append_event({"type": "opsec.verdict", "payload": {"correlation_id": "2"}})

    latest = state.latest_verdict()
    assert latest is not None
    assert latest["payload"]["correlation_id"] == "2"


def test_initial_state_defaults():
    state = TUIState()
    assert len(state.events) == 0
    assert state.snapshot["engagement_id"] is None
    assert state.snapshot["hosts"] == []
    assert state.snapshot["credentials"] == []
    assert state.snapshot["findings"] == []
    assert state.snapshot["sessions"] == []
    assert state.focus_overlay is None
    assert state.selected_correlation_id is None
