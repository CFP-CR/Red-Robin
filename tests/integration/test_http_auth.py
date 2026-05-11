"""HTTP auth integration tests — bearer token + open ping."""

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr("redrobin.infrastructure.filesystem.REDROBIN_ROOT", tmp_path / ".redrobin")
    (tmp_path / ".redrobin").mkdir(parents=True, exist_ok=True)
    yield tmp_path / ".redrobin"


@pytest.fixture
def client(_isolated_root):
    return TestClient(create_app())


def test_ping_is_open(client):
    r = client.get("/v1/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_protected_rejects_missing_token(client):
    r = client.get("/v1/protected")
    # FastAPI returns 422 when required header is absent
    assert r.status_code in (401, 422)


def test_protected_rejects_wrong_token(client):
    r = client.get("/v1/protected", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_protected_accepts_correct_token(_isolated_root, client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    token = get_or_create_token()
    r = client.get("/v1/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"status": "authenticated"}


def test_get_or_create_token_is_idempotent(_isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    t1 = get_or_create_token()
    t2 = get_or_create_token()
    assert t1 == t2
    assert len(t1) >= 32
