"""Slice 6 integration: engagement.yaml → ScopeEnforcer."""

from __future__ import annotations

from pathlib import Path

import pytest

from redrobin.application.engagement_loader import EngagementLoader


def _write_engagement(
    root: Path,
    slug: str,
    in_scope: list[str],
    out_of_scope: list[str],
    summary: str = "Engagement summary text for Layer B.",
) -> Path:
    eng_dir = root / "engagements" / slug
    eng_dir.mkdir(parents=True, exist_ok=True)
    (eng_dir / "engagement.yaml").write_text(
        f"slug: {slug}\n"
        "name: Test Engagement\n"
        "in_scope:\n"
        + "".join(f"  - {x}\n" for x in in_scope)
        + "out_of_scope:\n"
        + "".join(f"  - {x}\n" for x in out_of_scope)
        + f"summary: |\n  {summary}\n"
    )
    return eng_dir


def test_loader_returns_scope(tmp_path):
    _write_engagement(tmp_path, "op-test", ["corp.local", "10.0.0.0/24"], ["dmz.corp.local"])
    loader = EngagementLoader(engagements_root=tmp_path / "engagements")
    eng = loader.load("op-test")
    assert eng.slug == "op-test"
    assert "corp.local" in eng.in_scope
    assert "dmz.corp.local" in eng.out_of_scope
    assert "Engagement summary" in eng.summary


def test_loader_raises_on_missing(tmp_path):
    loader = EngagementLoader(engagements_root=tmp_path / "engagements")
    with pytest.raises(FileNotFoundError):
        loader.load("nope")


def test_scope_enforcer_built_from_engagement(tmp_path):
    from redrobin.application.scope_enforcer import ScopeEnforcer

    _write_engagement(tmp_path, "op-test", ["corp.local"], ["bigcorp.com"])
    loader = EngagementLoader(engagements_root=tmp_path / "engagements")
    eng = loader.load("op-test")
    enforcer = ScopeEnforcer(in_scope=eng.in_scope, out_of_scope=eng.out_of_scope)
    assert enforcer.is_in_scope("dc01.corp.local") is True
    assert enforcer.is_in_scope("api.bigcorp.com") is False


@pytest.mark.asyncio
async def test_lifespan_loads_engagement_scope_into_pipeline(tmp_path, monkeypatch):
    _write_engagement(tmp_path / "rr", "op-lifespan", ["corp.local"], ["bigcorp.com"])
    monkeypatch.setenv("REDROBIN_ENGAGEMENTS_DIR", str(tmp_path / "rr" / "engagements"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", "op-lifespan")
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        scope = app.state.verdict_pipeline._scope
        assert scope.is_in_scope("dc01.corp.local") is True
        assert scope.is_in_scope("api.bigcorp.com") is False
        # Gap #4 closure: the live verdict path calls ``scope.check(targets)``,
        # which must agree with ``is_in_scope``. Before Slice 6 this assertion
        # would have failed — ``check`` walked the fnmatch-based ``_matches_any``
        # while ``is_in_scope`` walked the suffix/CIDR matcher, so the live
        # pipeline did not actually enforce engagement.yaml-loaded scope.
        assert scope.check(["dc01.corp.local"]) is None
        violation = scope.check(["api.bigcorp.com"])
        assert violation is not None
        assert "out-of-scope" in violation
