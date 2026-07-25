"""Slice 14 E2E: redrobin init → daemon boot.

Exercises the full operator journey through the new ``redrobin init``
entry point introduced in Slice 5: the init helper writes the on-disk
layout (``rules/shared``, ``engagements/<slug>/engagement.yaml``,
``.env``, sample lazagne rule), the FastAPI lifespan picks it up
through :class:`Settings`, every startup helper runs end-to-end, and
the startup banner reflects the resolved engagement.

Migrates the prior Phase-2 manual env-var bootstrap to the new
init-driven flow — there is now one canonical way to set up a fresh
``REDROBIN_ROOT`` for both operators and tests. The fixture still pins
:envvar:`REDROBIN_ALLOW_DEGRADED` to ``1`` because the test does not
have real Anthropic / Voyage credentials, but the rest of the bootstrap
goes through the same code an operator runs from the shell.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.cli_helpers.init_cmd import run_init


@pytest.fixture()
def operator_setup(tmp_path, monkeypatch):
    """Replay the operator's ``redrobin init`` flow non-interactively.

    Returns the resolved root path so individual tests can assert against
    the on-disk layout produced by :func:`run_init`. ``REDROBIN_ROOT`` and
    ``REDROBIN_ENGAGEMENT_SLUG`` are wired into the process env so the
    daemon's :class:`Settings` resolves the same layout init just wrote.
    """
    root = tmp_path / "rr"
    rc = run_init(
        root=root,
        anthropic_key="k1-test",
        voyage_key="k2-test",
        engagement_slug="op-e2e",
        interactive=False,
    )
    assert rc == 0

    # The CLI normally loads .env at command entry. TestClient skips the CLI
    # entirely, so wire the same env vars init wrote into the process env so
    # ``create_app()`` sees the init-produced layout.
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", "op-e2e")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1-test")
    monkeypatch.setenv("VOYAGE_API_KEY", "k2-test")
    # Tests don't ship a real Layer B; opt into degraded explicitly so the
    # boot validation does not exit(2).
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    return root


def test_init_to_running_daemon_e2e(operator_setup, capfd):
    """Smoke: the init layout boots successfully through the lifespan.

    Confirms every Slice-8 helper ran (event bus, rules loader, working
    memory, engagement, pipeline, adapters, promoter, summariser) and
    that the structured Slice-7 banner reached stderr.
    """
    root = operator_setup
    app = create_app()
    with TestClient(app) as client:
        # Lifespan ran — app.state should be fully populated by every
        # ``start_*`` helper in :mod:`redrobin.api.lifespan_helpers`.
        assert app.state.rules_loader is not None
        assert app.state.event_bus is not None
        assert app.state.verdict_pipeline is not None
        assert app.state.working_memory_repo is not None
        assert app.state.scope is not None

        # The sample lazagne rule that init drops on disk MUST be in the
        # loaded snapshot — otherwise we lost the rules-loader → init wiring.
        rules = app.state.rules_loader.list_rules()
        assert any(r.id == "rule-sample-lazagne" for r in rules), (
            f"init's sample-lazagne.yaml rule not loaded: rules={[r.id for r in rules]}"
        )

        # Engagement loaded from the init-produced engagement.yaml — the
        # slug AND name both come from the template's ``slug``/``name``
        # placeholders, so they match.
        assert app.state.engagement is not None
        assert app.state.engagement.slug == "op-e2e"
        assert app.state.engagement.name == "op-e2e"

        # Slice 7 banner — emitted to stderr at lifespan startup so the
        # operator's stdout pipe can carry structured JSON. ``capfd``
        # captures fd-level writes, which is what ``flush=True`` in app.py
        # actually produces (capsys would miss it on some platforms).
        captured = capfd.readouterr()
        assert "Red Robin" in captured.err
        assert "op-e2e" in captured.err
        assert str(root) in captured.err

        # Healthz is the canonical liveness probe — verifies the FastAPI
        # app is fully wired and accepting requests.
        resp = client.get("/v1/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
