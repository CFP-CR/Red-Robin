"""E2E Path 3 — BEACON_OUTPUT observation flow.

Simulates the redrobin.cna `set BEACON_OUTPUT` filter pushing events to daemon:
  1. Adapter connects + active engagement set
  2. POST /v1/events with beacon.command_output payload
  3. Verify event lands in events.jsonl
"""

import json
import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()
    yield root


@pytest.fixture
def client(_isolated_root):
    return TestClient(create_app())


@pytest.fixture
def auth(_isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return {"Authorization": f"Bearer {get_or_create_token()}"}


def test_e2e_beacon_output_lands_in_events_jsonl(client, auth, _isolated_root):
    # Set up active engagement
    from redrobin.application.engagement_service import EngagementService

    svc = EngagementService(root=_isolated_root)
    svc.new("acme-q2")
    svc.use("acme-q2")

    envelope = {
        "spec_version": "0.1.0",
        "id": "evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        "ts": "2026-05-10T14:32:00.000Z",
        "source": {
            "adapter": "cs-aggressor",
            "version": "1.0.0",
            "engagement_id": "acme-q2",
        },
        "event": {
            "type": "beacon.command_output",
            "bid": "b1",
            "task_id": "task_001",
            "output": "uid=0(root)\nLinux dc01 5.15.0",
            "exit_status": 0,
        },
    }
    r = client.post("/v1/events", json=envelope, headers=auth)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "stored": True}

    jsonl = _isolated_root / "engagements" / "acme-q2" / "events.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text().strip().split("\n")
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored["event"]["output"] == "uid=0(root)\nLinux dc01 5.15.0"


def test_e2e_beacon_output_invalid_envelope_rejected(client, auth):
    bad = {
        "spec_version": "0.1.0",
        # missing id, ts, source, event
    }
    r = client.post("/v1/events", json=bad, headers=auth)
    assert r.status_code == 400


def test_e2e_command_input_event_audit_trail(client, auth, _isolated_root):
    from redrobin.application.engagement_service import EngagementService

    svc = EngagementService(root=_isolated_root)
    svc.new("acme-q2")
    svc.use("acme-q2")

    # Send 3 command_input events with distinct Crockford-base32-safe IDs.
    # Crockford excludes I, L, O, U — use J/K/M (M = 77 but L = 76 is excluded).
    id_suffixes = ["J", "K", "M"]
    for i in range(3):
        envelope = {
            "spec_version": "0.1.0",
            "id": f"evt_01HZX9KXXR8QF3T2ABCDEFGH{id_suffixes[i]}K",
            "ts": f"2026-05-10T14:32:0{i}.000Z",
            "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "acme-q2"},
            "event": {
                "type": "beacon.command_input",
                "bid": "b1",
                "operator": "juan",
                "command": "shell",
                "raw": f"shell echo cmd{i}",
            },
        }
        r = client.post("/v1/events", json=envelope, headers=auth)
        assert r.status_code == 200

    jsonl = _isolated_root / "engagements" / "acme-q2" / "events.jsonl"
    lines = jsonl.read_text().strip().split("\n")
    assert len(lines) == 3
    for i, line in enumerate(lines):
        stored = json.loads(line)
        assert stored["event"]["raw"] == f"shell echo cmd{i}"
