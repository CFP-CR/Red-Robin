"""`engagement new --in-scope ...` writes scope the loader consumes (Plan H)."""

from __future__ import annotations

from redrobin.application.engagement_loader import EngagementLoader


def test_cli_new_with_scope_flags_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.cli import main

    assert main(["init", "--non-interactive"]) == 0
    rc = main(
        [
            "engagement",
            "new",
            "acme-q2",
            "--name",
            "Acme Q2",
            "--client",
            "Acme Corp",
            "--in-scope",
            "corp.acme.local,10.10.0.0/16",
            "--in-scope",
            "dc01.acme.local",
            "--out-of-scope",
            "prod.acme.com",
            "--summary",
            "Internal pentest engagement.",
        ]
    )
    assert rc == 0

    eng = EngagementLoader(engagements_root=tmp_path / ".redrobin" / "engagements").load("acme-q2")
    assert eng.name == "Acme Q2"
    assert eng.in_scope == ["corp.acme.local", "10.10.0.0/16", "dc01.acme.local"]
    assert eng.out_of_scope == ["prod.acme.com"]
    assert eng.summary == "Internal pentest engagement."


def test_new_then_boot_yields_usable_scope(tmp_path, monkeypatch):
    """#135 acceptance: `engagement new --in-scope` -> daemon loads a REAL
    scope, so an in-scope target PASSES and an out-of-scope one BLOCKS, with no
    hand-editing of engagement.yaml. Uses REDROBIN_ENGAGEMENT_SLUG for
    deterministic daemon resolution (no init/.env/.active_engagement noise)."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", "acme")
    from redrobin.cli import main

    # `engagement new` runs ensure_layout() itself - no `init` needed.
    assert main(["engagement", "new", "acme", "--in-scope", "10.10.0.0/16"]) == 0

    from redrobin.api.app import create_app

    app = create_app()
    with TestClient(app):
        assert app.state.engagement is not None
        assert app.state.scope_empty is False
        scope = app.state.scope
        assert scope.check(["10.10.0.5"]) is None  # in-scope -> PASS
        assert scope.check(["8.8.8.8"]) is not None  # out-of-scope -> BLOCK
