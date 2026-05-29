"""Integration tests for the events_router dedup gate (B3)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Boot the daemon in degraded mode against an isolated root.

    Creates a minimal ``eng-b3`` engagement directory + ``.active_engagement``
    marker so the events router's jsonl-append path resolves to a real
    per-engagement directory. The dedup-gate path itself still falls to the
    degraded ``<root>/dedup_keys.json`` because we don't write a
    ``engagement.yaml`` here (the ``EngagementLoader`` would fail and the
    lifespan logs a warning + falls back to wildcard scope) — that's fine for
    these tests: the gate writes its file under root, but ingest still records
    to ``engagements/eng-b3/events.jsonl`` via ``EngagementService.current()``.
    """
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    eng_dir = root / "engagements" / "eng-b3"
    eng_dir.mkdir(parents=True, exist_ok=True)
    (root / ".active_engagement").write_text("eng-b3", encoding="utf-8")
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-b3")
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return get_or_create_token()


_VALID_ID = "evt_01HZX9KXXR8QF3T2ABCDEFGHJK"


def _envelope(
    *,
    bid: str = "B-1",
    correlation_id: str = "abc123",
    event_type: str = "beacon.command_output",
    output: str = "hello",
    envelope_id: str = _VALID_ID,
) -> dict[str, Any]:
    """Build a minimal canonical wire envelope for tests.

    The envelope id matches the ULID regex enforced by
    ``protocol/schemas/v0.1/envelope.schema.json``
    (``^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$``). The dedup gate keys on
    ``(bid, correlation_id, type)`` from ``event``, not the envelope id —
    so a stable id is fine: the dedup-hit assertion below depends on a
    repeat bid+corr, not a repeat envelope id.
    """
    return {
        "spec_version": "0.1.0",
        "id": envelope_id,
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": event_type,
            "bid": bid,
            "task_id": "",
            "output": output,
            "correlation_id": correlation_id,
        },
    }


def test_lifespan_attaches_dedup_keys(daemon) -> None:
    app, _ = daemon
    from redrobin.application.dedup_keys import DedupKeys

    assert hasattr(app.state, "dedup_keys")
    assert isinstance(app.state.dedup_keys, DedupKeys)


def test_ingest_dedups_repeated_envelope(daemon) -> None:
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = _envelope()

    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)

    assert r1.status_code == 200
    assert r1.json().get("stored") is True
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True
    import os

    events_jsonl = Path(os.environ["REDROBIN_ROOT"]) / "engagements" / "eng-b3" / "events.jsonl"
    if events_jsonl.exists():
        lines = events_jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1


def test_replay_dedups_repeated_bus_event(daemon) -> None:
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    bus_event = {
        "type": "beacon.command_output",
        "bid": "B-2",
        "task_id": "",
        "output": "x",
        "correlation_id": "xyz789",
    }
    r1 = client.post("/v1/events/replay", json=bus_event, headers=headers)
    r2 = client.post("/v1/events/replay", json=bus_event, headers=headers)
    assert r1.status_code == 202
    assert r1.json()["status"] == "accepted"
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"


def test_ingest_passes_through_event_without_correlation_id(daemon) -> None:
    """An event whose derive_key returns None (no correlation_id) is NOT
    deduped — both POSTs should reach the bus + jsonl."""
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "spec_version": "0.1.0",
        "id": _VALID_ID,
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.command_output",
            "bid": "B-9",
            "task_id": "",
            "output": "no-corr",
        },
    }
    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)
    assert r1.status_code == 200 and r1.json().get("stored") is True
    assert r2.status_code == 200 and r2.json().get("stored") is True
    assert "duplicate" not in r2.json()


def _beacon_initial_payload(bid: str = "B-3") -> dict[str, Any]:
    return {
        "spec_version": "0.1.0",
        "id": _VALID_ID,
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.initial",
            "bid": bid,
            "host": "DC01",
            "user": "CORP\\admin",
            "integrity_level": "high",
            "arch": "x64",
            "pid": 1234,
            "ppid": 5678,
        },
    }


def test_beacon_initial_is_not_deduped(daemon) -> None:
    """Group D: beacon.initial is pass-through. A 2nd identical POST is NOT
    dropped by the dedup gate (inverts the B3 behavior) — it must re-reach the
    reducer so the .cna backfill can re-seed sessions after a clean."""
    _, client = daemon
    headers = {"Authorization": f"Bearer {_token()}"}
    payload = _beacon_initial_payload()
    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)
    assert r1.status_code == 200 and r1.json().get("stored") is True
    assert r2.status_code == 200 and r2.json().get("stored") is True
    assert "duplicate" not in r2.json()


def test_beacon_initial_reseeds_session(daemon) -> None:
    """End-to-end: a POSTed beacon.initial reaches the reducer and seeds the
    session; a 2nd POST (not deduped) re-seeds it idempotently. Reads the live
    working-memory repo via ``app.state.working_memory_repo`` — the reducer
    consumes the bus asynchronously, so poll until the session appears. Reading
    the repo object (not a hardcoded path) survives any future change to how the
    WM path resolves; this is the same approach the green e2e journey test uses
    (``tests/e2e/test_events_to_memory_journey.py`` reads
    ``app.state.working_memory_repo.load().sessions``)."""
    app, client = daemon
    headers = {"Authorization": f"Bearer {_token()}"}
    repo = app.state.working_memory_repo

    def _session_present(bid: str) -> bool:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                if any(s.beacon_id == bid for s in repo.load().sessions):
                    return True
            except FileNotFoundError:
                pass  # reducer hasn't written the WM file yet
            time.sleep(0.05)
        return False

    payload = _beacon_initial_payload(bid="B-7")
    client.post("/v1/events", json=payload, headers=headers)
    assert _session_present("B-7"), "reducer did not seed the session from beacon.initial"

    # 2nd POST is not deduped → reaches the reducer again → still exactly one
    # session for the bid (upsert_session replaces by bid — idempotent).
    client.post("/v1/events", json=payload, headers=headers)
    assert _session_present("B-7")
    sessions = [s for s in repo.load().sessions if s.beacon_id == "B-7"]
    assert len(sessions) == 1


def test_state_survives_test_client_restart(tmp_path: Path, monkeypatch) -> None:
    """Persist + reload: a second TestClient over the same root sees keys."""
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    eng_dir = root / "engagements" / "eng-b3"
    eng_dir.mkdir(parents=True, exist_ok=True)
    (root / ".active_engagement").write_text("eng-b3", encoding="utf-8")
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-b3")
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.api.app import create_app
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    payload = {
        "spec_version": "0.1.0",
        "id": _VALID_ID,
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.command_output",
            "bid": "B-4",
            "task_id": "",
            "output": "x",
            "correlation_id": "persist1",
        },
    }
    headers = {"Authorization": f"Bearer {get_or_create_token()}"}
    app1 = create_app()
    with TestClient(app1) as c1:
        r1 = c1.post("/v1/events", json=payload, headers=headers)
        assert r1.status_code == 200 and r1.json().get("stored") is True
    app2 = create_app()
    with TestClient(app2) as c2:
        r2 = c2.post("/v1/events", json=payload, headers=headers)
        assert r2.status_code == 200 and r2.json().get("duplicate") is True


def test_lifespan_attaches_dedup_keys_under_engagement_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-engagement happy path: the keys file lives under
    ``engagements/<slug>/dedup_keys.json``, NOT directly under root.

    Distinct from ``test_lifespan_attaches_dedup_keys`` which only exercises
    the degraded path (no engagement.yaml resolves under the existing fixture,
    so ``start_engagement`` returns ``app.state.engagement = None`` and
    ``start_dedup`` writes the file under ``<root>/dedup_keys.json``).

    Here we set ``REDROBIN_ENGAGEMENT_SLUG`` AND write a valid
    ``engagement.yaml`` so ``EngagementLoader`` succeeds, ``app.state.engagement``
    is populated, and ``start_dedup`` picks the per-engagement path.
    """
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    eng_slug = "eng-b3"
    eng_dir = root / "engagements" / eng_slug
    eng_dir.mkdir(parents=True, exist_ok=True)
    (eng_dir / "engagement.yaml").write_text(
        "slug: eng-b3\n"
        "name: eng-b3\n"
        "client: acme\n"
        "in_scope:\n"
        "  - corp.local\n"
        "out_of_scope:\n"
        "  - bigcorp.com\n"
        "summary: per-engagement dedup test\n",
        encoding="utf-8",
    )
    # ``.active_engagement`` so the events_router's jsonl-append path
    # (``EngagementService.current()``) also resolves to ``eng-b3`` and we get
    # ``stored: True`` on POST — the gate writes its file under the engagement
    # dir REGARDLESS (since the lifespan resolves the engagement from the SLUG
    # env var), but proving ``stored=True`` end-to-end demonstrates the gate
    # AND the per-engagement append both work together.
    (root / ".active_engagement").write_text(eng_slug, encoding="utf-8")
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    # Critical: SLUG (not ID) is what start_engagement reads.
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", eng_slug)
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.api.app import create_app
    from redrobin.application.dedup_keys import DedupKeys
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    app = create_app()
    with TestClient(app) as client:
        assert isinstance(app.state.dedup_keys, DedupKeys)
        # Introspect the underlying repo path: must be under the engagement
        # directory (not the degraded root fallback).
        keys_path = app.state.dedup_keys._repo.path
        assert keys_path == eng_dir / "dedup_keys.json"

        # POST an envelope so the gate actually writes the file, then verify
        # it appears at the per-engagement path.
        headers = {"Authorization": f"Bearer {get_or_create_token()}"}
        payload = _envelope(bid="B-EH", correlation_id="ehhappy", output="happy-path")
        r = client.post("/v1/events", json=payload, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json().get("stored") is True
        assert (eng_dir / "dedup_keys.json").is_file()
        # And NOT at the degraded root fallback.
        assert not (root / "dedup_keys.json").exists()
