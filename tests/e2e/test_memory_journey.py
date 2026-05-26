"""E2E: beacon output replayed → reducer parses → working memory snapshot exposes creds + host.

Slice 15 Task 15.2. Exercises the full real path with canonical wire types:

    POST /v1/events/replay (auth) → EventBus → WorkingMemoryReducer
                                                       ↓
                                                  Credential/Host upserts
                                                       ↓
    WS /v1/tui connect → working_memory_snapshot frame includes the new state.

The replay endpoint requires Bearer auth (Slice 11). Replay payloads use
the canonical wire shape ``{type: "beacon.command_output", payload: {bid, ...}}``
so the same trace fixture that exercises the bus also matches the wire
schema for in-process replay.

The Host serializer emits the ``name`` field (uppercased by the reducer's
normalisation), and Credentials emit ``domain`` + ``username`` + ``ntlm_hash``.
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


def test_beacon_output_propagates_to_snapshot(daemon):
    app, client = daemon
    token = _token()

    payload = {
        "type": "beacon.command_output",
        "payload": {
            "bid": "B-1",
            "output": (
                "Username: CORP\\alice\n"
                "NTLM: aad3b435b51404eeaad3b435b51404ee:"
                "11111111111111111111111111111111\n"
                "Hostname: WIN10\n"
            ),
        },
    }
    resp = client.post(
        "/v1/events/replay",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202

    # Poll the live working-memory repo for the reducer to finish writing.
    # The repo is the same one feeding the snapshot builder; once it sees the
    # credential the snapshot frame on a fresh WS connection will too.
    deadline = time.time() + 2.0
    repo = app.state.working_memory_repo
    while time.time() < deadline:
        creds = repo.list_credentials()
        if any(c.username == "alice" for c in creds):
            break
        time.sleep(0.05)
    else:
        pytest.fail(
            "reducer did not record credential within 2s; creds=%r" % repo.list_credentials()
        )

    # Connect a fresh WS — the initial snapshot is built from the live repo.
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        snap = ws.receive_json()
    assert snap["type"] == "working_memory_snapshot"
    creds = snap["payload"]["credentials"]
    assert any(c["username"] == "alice" and c["domain"] == "CORP" for c in creds), creds
    hosts = snap["payload"]["hosts"]
    # Host.name is normalised to upper-case by upsert_host.
    assert any(h["name"] == "WIN10" for h in hosts), hosts
