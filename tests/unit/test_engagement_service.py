"""Engagement service lifecycle tests."""

import pytest

from redrobin.application.engagement_service import EngagementError, EngagementService
from redrobin.domain.engagement import validate_slug


def test_validate_slug_accepts_canonical():
    validate_slug("acme-q2-2026")
    validate_slug("globex_q3_2026")
    validate_slug("a")


def test_validate_slug_rejects_uppercase():
    with pytest.raises(ValueError):
        validate_slug("Acme-Q2")


def test_validate_slug_rejects_spaces():
    with pytest.raises(ValueError):
        validate_slug("acme q2")


def test_validate_slug_rejects_path_traversal():
    with pytest.raises(ValueError):
        validate_slug("../etc/passwd")


def test_new_creates_engagement_dir_and_yaml(tmp_path):
    svc = EngagementService(root=tmp_path)
    eng = svc.new("acme-q2", client="Acme Corp")
    assert eng.id == "acme-q2"
    assert (tmp_path / "engagements" / "acme-q2" / "engagement.yaml").exists()
    assert (tmp_path / "engagements" / "acme-q2" / "working_memory.json").exists()


def test_new_rejects_duplicate(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    with pytest.raises(EngagementError, match="already exists"):
        svc.new("acme-q2")


def test_use_sets_active(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    svc.use("acme-q2")
    assert svc.current() == "acme-q2"


def test_use_unknown_engagement_raises(tmp_path):
    svc = EngagementService(root=tmp_path)
    with pytest.raises(EngagementError, match="not found"):
        svc.use("nonexistent")


def test_list_all_returns_engagement_slugs(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    svc.new("globex-q3")
    assert sorted(svc.list_all()) == ["acme-q2", "globex-q3"]


def test_close_clears_active_marker(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    svc.use("acme-q2")
    svc.close("acme-q2")
    assert svc.current() is None


def test_close_active_without_slug(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    svc.use("acme-q2")
    svc.close()
    assert svc.current() is None


def test_archive_produces_tarball(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    tarball = svc.archive("acme-q2")
    assert tarball.exists()
    assert tarball.name.startswith("acme-q2-")
    assert tarball.name.endswith(".tar.gz")


def test_delete_removes_engagement_dir(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    svc.delete("acme-q2")
    assert not (tmp_path / "engagements" / "acme-q2").exists()


def test_delete_refuses_active(tmp_path):
    svc = EngagementService(root=tmp_path)
    svc.new("acme-q2")
    svc.use("acme-q2")
    with pytest.raises(EngagementError, match="active"):
        svc.delete("acme-q2")
