"""CLI helper tests for `engagement new` scope flags (Plan H, #135)."""
from __future__ import annotations

from redrobin.cli_helpers.engagement_cmd import _csv_list


def test_csv_list_flattens_repeats_and_commas():
    # --in-scope a,b --in-scope c  -> ["a", "b", "c"]
    assert _csv_list(["a,b", "c"]) == ["a", "b", "c"]


def test_csv_list_strips_and_drops_empties():
    assert _csv_list([" a , ,b ", ""]) == ["a", "b"]


def test_csv_list_none_is_empty():
    assert _csv_list(None) == []


def test_run_new_warns_when_no_scope(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.application.engagement_service import EngagementService
    from redrobin.cli_helpers.engagement_cmd import run_new

    svc = EngagementService(root=tmp_path / ".redrobin")
    rc = run_new(svc=svc, slug="no-scope", client=None)
    assert rc == 0
    err = capsys.readouterr().err
    assert "scope" in err.lower()
    assert "BLOCKED" in err


def test_run_new_no_warn_when_scoped(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    from redrobin.application.engagement_service import EngagementService
    from redrobin.cli_helpers.engagement_cmd import run_new

    svc = EngagementService(root=tmp_path / ".redrobin")
    rc = run_new(svc=svc, slug="scoped", client=None, in_scope=["corp.local"])
    assert rc == 0
    assert "BLOCKED" not in capsys.readouterr().err
