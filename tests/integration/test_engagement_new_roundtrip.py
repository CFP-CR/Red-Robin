"""#135: `engagement new` must write a YAML the daemon's EngagementLoader
consumes WITHOUT losing scope.

Root cause (verified live 2026-05-25): the writer emitted `id` + nested
`scope.in_scope`; the loader reads top-level `slug`/`in_scope`, so scope was
silently dropped -> empty -> ScopeEnforcer deny-all on every targeted command.
This test pins the writer and loader to ONE schema.
"""

from __future__ import annotations

from redrobin.application.engagement_loader import EngagementLoader
from redrobin.application.engagement_service import EngagementService


def test_new_output_round_trips_through_loader(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new(
        "acme-q2",
        name="Acme Q2 2026",
        client="Acme Corp",
        in_scope=["corp.acme.local", "10.10.0.0/16"],
        out_of_scope=["prod.acme.com"],
        summary="Internal pentest.",
    )

    eng = EngagementLoader(engagements_root=tmp_path / "engagements").load("acme-q2")
    assert eng.slug == "acme-q2"
    assert eng.name == "Acme Q2 2026"
    assert eng.in_scope == ["corp.acme.local", "10.10.0.0/16"]
    assert eng.out_of_scope == ["prod.acme.com"]
    assert eng.summary == "Internal pentest."


def test_new_writes_top_level_keys_not_nested_scope(tmp_path):
    import yaml

    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2", in_scope=["corp.acme.local"])
    data = yaml.safe_load((tmp_path / "engagements" / "acme-q2" / "engagement.yaml").read_text())
    # The loader reads these top-level keys; the old writer used `id` + `scope:`.
    assert data["slug"] == "acme-q2"
    assert data["in_scope"] == ["corp.acme.local"]
    assert "scope" not in data  # no nested scope block
    assert "id" not in data  # loader never read `id`


def test_init_template_stays_loader_consumable(tmp_path):
    """init_cmd writes the DEFAULT engagement.yaml via _ENGAGEMENT_TEMPLATE — a
    SECOND writer. It already uses the loader schema; this guard fails if it
    ever regresses to a loader-incompatible shape (reintroducing #135 via init)."""
    from redrobin.application.engagement_loader import EngagementLoader
    from redrobin.cli_helpers.init_cmd import _ENGAGEMENT_TEMPLATE

    eng_dir = tmp_path / "engagements" / "op-default"
    eng_dir.mkdir(parents=True)
    (eng_dir / "engagement.yaml").write_text(_ENGAGEMENT_TEMPLATE.format(slug="op-default"))
    eng = EngagementLoader(engagements_root=tmp_path / "engagements").load("op-default")
    assert eng.slug == "op-default"
    assert eng.in_scope  # template ships a non-empty example scope
