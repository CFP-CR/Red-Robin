"""Events ingestion endpoint."""

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


def _valid_envelope():
    return {
        "spec_version": "0.1.0",
        "id": "evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        "ts": "2026-05-10T14:32:00.000Z",
        "source": {
            "adapter": "cs-aggressor",
            "version": "1.0.0",
            "engagement_id": "acme-q2",
        },
        "event": {"type": "adapter.heartbeat"},
    }


def test_ingest_valid_envelope_returns_ok(client, auth):
    r = client.post("/v1/events", json=_valid_envelope(), headers=auth)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_ingest_invalid_envelope_returns_400(client, auth):
    bad = _valid_envelope()
    del bad["spec_version"]
    r = client.post("/v1/events", json=bad, headers=auth)
    assert r.status_code == 400
    assert "envelope_errors" in r.json()["detail"]


def test_ingest_invalid_event_payload_returns_400(client, auth):
    envelope = _valid_envelope()
    envelope["event"] = {
        "type": "beacon.command_input",
        # missing required bid, operator, command, raw
    }
    r = client.post("/v1/events", json=envelope, headers=auth)
    assert r.status_code == 400
    assert "event_errors" in r.json()["detail"]


def test_ingest_requires_auth(client):
    r = client.post("/v1/events", json=_valid_envelope())
    assert r.status_code in (401, 422)


def test_ingest_appends_to_events_jsonl_when_engagement_active(client, auth, _isolated_root):
    from redrobin.application.engagement_service import EngagementService

    svc = EngagementService(root=_isolated_root)
    svc.new("acme-q2")
    svc.use("acme-q2")

    envelope = _valid_envelope()
    r = client.post("/v1/events", json=envelope, headers=auth)
    assert r.status_code == 200
    assert r.json()["stored"] is True

    jsonl = _isolated_root / "engagements" / "acme-q2" / "events.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text().strip().split("\n")
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored["id"] == envelope["id"]


def test_ingest_skips_storage_when_no_active_engagement(client, auth):
    r = client.post("/v1/events", json=_valid_envelope(), headers=auth)
    assert r.status_code == 200
    assert r.json()["stored"] is False
