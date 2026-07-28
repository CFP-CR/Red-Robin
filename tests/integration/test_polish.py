"""Slice 14 integration: heartbeat, VectorStore JSON, doctor exit code."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.infrastructure.vector_store import VectorStore


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    """Same isolation pattern as test_http_auth — points REDROBIN_ROOT at tmp."""
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    # Boot validation exits(2) without API keys; the heartbeat test boots the
    # daemon and asserts nothing about the LLM surface. Opt in explicitly rather
    # than depending on keys leaked by an earlier test (issue #51).
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    (tmp_path / ".redrobin").mkdir(parents=True, exist_ok=True)
    yield tmp_path / ".redrobin"


def test_heartbeat_endpoint(tmp_path, monkeypatch, _isolated_root):
    """GET /v1/adapter/heartbeat returns ok + echoes the adapter name.

    This is the simple liveness probe the .cna fires every 30s — distinct
    from the session-bound POST /v1/adapters/heartbeat used during a live
    handshake. The .cna can hit this before/without a connect() handshake
    so the operator sees "daemon unreachable" warnings the moment the
    daemon goes away.
    """
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/v1/adapter/heartbeat?adapter=cs-main")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["adapter"] == "cs-main"


def test_vector_store_metadata_json_serializable(tmp_path):
    """Nested-dict metadata survives a save/load round-trip via JSON.

    The Phase 1 implementation persisted ``metadata`` via ``repr()`` and
    rebuilt it with ``eval()`` — fine for flat dicts but unsafe + lossy for
    anything with a numeric type or nested structure. JSON keeps the shape
    intact and removes the eval() footgun.
    """
    store = VectorStore()
    store.add(
        ids=["d1-0"],
        texts=["hello"],
        vectors=[[0.1, 0.2, 0.3]],
        metadatas=[{"tags": ["x"], "score": 0.9, "obj": {"nested": True}}],
    )
    out_path = tmp_path / "kb.npz"
    store.save(out_path)

    reloaded = VectorStore.load(out_path)
    hits = reloaded.search(query_vector=[0.1, 0.2, 0.3], top_k=1)
    assert len(hits) == 1
    record, _score = hits[0]
    assert record.id == "d1-0"
    assert record.metadata == {"tags": ["x"], "score": 0.9, "obj": {"nested": True}}


def test_vector_store_metadata_persisted_as_json_not_repr(tmp_path):
    """Lock in JSON-on-disk so a future refactor can't quietly regress.

    Reads the .npz raw and asserts each metadata blob parses as JSON. This
    is the bit that actually closes gap #22 — the previous round-trip
    happened to work for flat dicts even with repr(), so a round-trip test
    alone wouldn't catch a regression to repr().
    """
    store = VectorStore()
    store.add(
        ids=["a"],
        texts=["t"],
        vectors=[[1.0, 0.0]],
        metadatas=[{"k": "v", "n": 1}],
    )
    path = tmp_path / "index.npz"
    store.save(path)

    data = np.load(path, allow_pickle=True)
    raw = str(data["metas"][0])
    # JSON-loadable; repr() would emit single quotes and fail json.loads.
    parsed = json.loads(raw)
    assert parsed == {"k": "v", "n": 1}


def test_doctor_exit_code_nonzero_on_failure(monkeypatch, tmp_path):
    """Missing env vars → non-zero exit so CI smoke tests can gate on it.

    Previously doctor always returned 0 even when ANTHROPIC_API_KEY was
    unset — useless as a preflight in any automated context.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    # Point REDROBIN_ROOT at a fresh tmp so the rules-dir check is the only
    # other potential failure surface, and we control it.
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.cli import cmd_doctor

    # quick=True keeps Slice 6's network probes off; we only care about
    # the env-var-presence path returning non-zero here.
    args = type("A", (), {"verbose": False, "quick": True})()
    rc = cmd_doctor(args)
    assert rc != 0


def test_doctor_reports_the_engagement_the_daemon_would_resolve(monkeypatch, tmp_path, capsys):
    """doctor's ENGAGEMENT line must mirror ``start_engagement``'s precedence.

    ``init`` no longer pins ``REDROBIN_ENGAGEMENT_SLUG`` (it outranked the
    ``.active_engagement`` marker and made ``engagement use`` a no-op), and
    doctor only ever read that env var — so without this fallback it would now
    report ``(none)`` on every correctly-set-up install.
    """
    from redrobin.application.engagement_service import EngagementService
    from redrobin.cli_helpers.doctor_cmd import run_doctor
    from redrobin.infrastructure.config import Settings

    root = tmp_path / ".redrobin"
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    svc = EngagementService(root=root)
    svc.new("op-marker")
    svc.use("op-marker")

    run_doctor(settings=Settings(), quick=True)
    assert "op-marker" in capsys.readouterr().out

    # ...and the env var still wins when it IS set (its documented role).
    svc.new("op-override")
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", "op-override")
    run_doctor(settings=Settings(), quick=True)
    out = capsys.readouterr().out
    assert "op-override" in out
    assert "op-marker" not in out


def test_doctor_exit_code_zero_when_all_pass(monkeypatch, tmp_path):
    """Sanity: all checks green → rc == 0 so a healthy daemon doesn't flag.

    Slice 6: API keys set + --quick → no network probes, so test-key values
    don't try to hit api.anthropic.com / api.voyageai.com. Real connectivity
    is exercised via tests/integration/test_doctor_probes.py with MockTransport.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    root = tmp_path / ".redrobin"
    # doctor reports ROOT_EXISTS; create it so the run is OK, not DEGRADED.
    root.mkdir(parents=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    from redrobin.cli import cmd_doctor

    args = type("A", (), {"verbose": False, "quick": True})()
    rc = cmd_doctor(args)
    assert rc == 0
