"""E2E: structured beacon.initial event replayed → reducer writes a Session → snapshot exposes it.

Exercises the full real path:

    POST /v1/events/replay (auth) → EventBus → WorkingMemoryReducer
                                                       ↓
                                                  Session upsert
                                                       ↓
    WS /v1/tui connect → working_memory_snapshot frame includes the new session.

The replay endpoint requires Bearer auth (Slice 11) and SKIPS schema validation
(operator owns trace contents) — a bare bus-shape dict is published verbatim.
beacon.initial is the structured event the reducer handles to upsert a Session
keyed by ``bid``. (The legacy regex extraction of creds/hosts from
``beacon.command_output`` was retired in Plan B2; the LLM enricher (Plan B1)
is the sole extraction path now, via ``working_memory.deltas`` events the
reducer merges through ``apply_deltas`` — see
``test_working_memory_deltas_event_merges_and_reemits`` for that path.)
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-mem")
    # Slice 3: opt into degraded Layer B explicitly. No API keys here.
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return get_or_create_token()


def test_beacon_initial_propagates_to_snapshot(daemon):
    app, client = daemon
    token = _token()

    payload = {
        "type": "beacon.initial",
        "payload": {
            "bid": "B-1",
            "host": "WIN10",
            "user": "CORP\\alice",
            "integrity_level": "high",
        },
    }
    resp = client.post(
        "/v1/events/replay",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202

    # Poll the live working-memory repo until the reducer records the session.
    deadline = time.time() + 2.0
    repo = app.state.working_memory_repo
    while time.time() < deadline:
        sessions = repo.load().sessions
        if any(s.beacon_id == "B-1" for s in sessions):
            break
        time.sleep(0.05)
    else:
        pytest.fail("reducer did not record session within 2s; sessions=%r" % repo.load().sessions)

    # Connect a fresh WS — the initial snapshot is built from the live repo.
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        snap = ws.receive_json()
    assert snap["type"] == "working_memory_snapshot"
    sessions = snap["payload"]["sessions"]
    assert any(s["beacon_id"] == "B-1" and s["hostname"] == "WIN10" for s in sessions), sessions
