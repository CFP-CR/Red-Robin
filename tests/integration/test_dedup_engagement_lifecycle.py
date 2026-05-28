"""Integration tests: engagement lifecycle correctly handles the dedup_keys
file (no code in engagement_service.py — the existing semantics suffice; this
test locks the behavior so a future change cannot silently regress it)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from redrobin.application.engagement_service import EngagementService


@pytest.fixture()
def svc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    return EngagementService(root=root), root


def test_new_engagement_has_no_dedup_file(svc_root) -> None:
    """new() does not create a dedup_keys.json file — DedupKeysRepo.load
    returns the empty set for a missing file, so fresh engagements start
    with an empty dedup state automatically."""
    svc, root = svc_root
    svc.new("eng-x", name="x", client="acme", in_scope=["host-1"])
    dedup_path = root / "engagements" / "eng-x" / "dedup_keys.json"
    assert not dedup_path.exists()


def test_archive_keeps_dedup_file_inside_tarball(svc_root) -> None:
    """archive() tarballs the engagement dir; the dedup file goes WITH it."""
    svc, root = svc_root
    svc.new("eng-y", name="y", client="acme", in_scope=["host-1"])
    dedup_path = root / "engagements" / "eng-y" / "dedup_keys.json"
    dedup_path.write_text(
        json.dumps({"keys": ["B-1|abc|beacon.command_output"]}),
        encoding="utf-8",
    )
    assert dedup_path.exists()
    tarball = svc.archive("eng-y")
    assert tarball.exists()
    # Verify the dedup file is INSIDE the tarball (not just that the local
    # copy survived). Future regression: if archive() tars an empty dir or
    # excludes dedup_keys.json, this assertion catches it.
    with tarfile.open(tarball, "r:gz") as tar:
        names = tar.getnames()
        assert any(name.endswith("dedup_keys.json") for name in names), (
            f"dedup_keys.json missing from tarball members: {names}"
        )
    # The local engagement dir itself is unchanged by archive() — only the
    # tarball is produced. The dedup file is still present locally too.
    assert dedup_path.exists()


def test_delete_removes_dedup_file(svc_root) -> None:
    """delete() rm -rf's the engagement dir; dedup_keys.json goes too."""
    svc, root = svc_root
    svc.new("eng-z", name="z", client="acme", in_scope=["host-1"])
    dedup_path = root / "engagements" / "eng-z" / "dedup_keys.json"
    dedup_path.write_text(json.dumps({"keys": ["B-1|abc|beacon.command_output"]}), encoding="utf-8")
    assert dedup_path.exists()
    svc.delete("eng-z")
    assert not dedup_path.exists()
