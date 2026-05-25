"""`engagement new --in-scope ...` writes scope the loader consumes (Plan H)."""
from __future__ import annotations

from redrobin.application.engagement_loader import EngagementLoader


def test_cli_new_with_scope_flags_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.cli import main

    assert main(["init", "--non-interactive"]) == 0
    rc = main(
        [
            "engagement", "new", "acme-q2",
            "--name", "Acme Q2",
            "--client", "Acme Corp",
            "--in-scope", "corp.acme.local,10.10.0.0/16",
            "--in-scope", "dc01.acme.local",
            "--out-of-scope", "prod.acme.com",
            "--summary", "Internal pentest engagement.",
        ]
    )
    assert rc == 0

    eng = EngagementLoader(
        engagements_root=tmp_path / ".redrobin" / "engagements"
    ).load("acme-q2")
    assert eng.name == "Acme Q2"
    assert eng.in_scope == ["corp.acme.local", "10.10.0.0/16", "dc01.acme.local"]
    assert eng.out_of_scope == ["prod.acme.com"]
    assert eng.summary == "Internal pentest engagement."
