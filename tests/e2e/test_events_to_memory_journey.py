"""E2E: canonical wire envelope POST → bus → reducer → working memory snapshot.

Exercises the real production path that real CS adapters use:

    POST /v1/events (envelope, auth) → validate + append events.jsonl
                                     → translate envelope to bus shape
                                     → publish on EventBus
                                     → WorkingMemoryReducer dispatches
                                     → Credential/Host upserts

    WS /v1/tui connect → working_memory_snapshot frame includes the new state.

This test exists to catch the wire-event regression that the original
``test_memory_journey.py`` missed by using ``/v1/events/replay`` with
synthetic ``c2.*`` types. Real CS adapters POST canonical wire envelopes
(``beacon.command_output`` / ``beacon.initial``) to ``/v1/events``; this
test exercises that exact path.
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
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-wire")
    # Slice 3: opt into degraded Layer B explicitly. No API keys here.
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return get_or_create_token()


def _envelope(event: dict) -> dict:
    """Build a valid wire envelope around ``event``."""
    return {
        "spec_version": "0.1.0",
        "id": "evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        "ts": "2026-05-10T14:32:00.000Z",
        "source": {
            "adapter": "cs-aggressor",
            "version": "1.0.0",
            "engagement_id": "eng-wire",
        },
        "event": event,
    }


def test_post_events_canonical_command_output_reaches_reducer(daemon):
    """Real CS path: POST /v1/events with canonical beacon.command_output."""
    app, client = daemon
    token = _token()

    envelope = _envelope(
        {
            "type": "beacon.command_output",
            "bid": "B-wire-1",
            "task_id": "task-1",
            "output": (
                "Username: CORP\\bob\n"
                "NTLM: aad3b435b51404eeaad3b435b51404ee:"
                "22222222222222222222222222222222\n"
                "Hostname: WIN11\n"
            ),
            "exit_status": 0,
        }
    )

    resp = client.post(
        "/v1/events",
        json=envelope,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    # Poll for the reducer to consume the bus event and update working memory.
    deadline = time.time() + 2.0
    repo = app.state.working_memory_repo
    while time.time() < deadline:
        creds = repo.list_credentials()
        if any(c.username == "bob" for c in creds):
            break
        time.sleep(0.05)
    else:
        pytest.fail(
            "reducer never saw the bus-published wire event; creds=%r" % repo.list_credentials()
        )

    # Connect a fresh WS — the snapshot is built from the live repo.
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        snap = ws.receive_json()
    assert snap["type"] == "working_memory_snapshot"
    creds = snap["payload"]["credentials"]
    assert any(c["username"] == "bob" and c["domain"] == "CORP" for c in creds), creds
    hosts = snap["payload"]["hosts"]
    assert any(h["name"] == "WIN11" for h in hosts), hosts


def test_post_events_canonical_beacon_initial_creates_session(daemon):
    """Real CS path: POST /v1/events with canonical beacon.initial."""
    app, client = daemon
    token = _token()

    envelope = _envelope(
        {
            "type": "beacon.initial",
            "bid": "B-wire-2",
            "host": "DC02",
            "user": "CORP\\carol",
            "integrity_level": "high",
            "arch": "x64",
            "pid": 4321,
            "ppid": 1234,
        }
    )

    resp = client.post(
        "/v1/events",
        json=envelope,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    deadline = time.time() + 2.0
    repo = app.state.working_memory_repo
    while time.time() < deadline:
        sessions = repo.load().sessions
        if any(s.beacon_id == "B-wire-2" for s in sessions):
            break
        time.sleep(0.05)
    else:
        pytest.fail(
            "reducer never recorded the beacon.initial session; sessions=%r" % repo.load().sessions
        )

    sessions = repo.load().sessions
    sess = next(s for s in sessions if s.beacon_id == "B-wire-2")
    assert sess.hostname == "DC02"
    assert sess.user == "CORP\\carol"
    assert sess.integrity == "high"
