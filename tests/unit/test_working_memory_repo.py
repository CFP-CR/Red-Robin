"""Working memory repo — JSON read/write round-trip."""

import pytest

from redrobin.domain.memory import Account, Host, Objective, WorkingMemory
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def test_save_and_load_round_trip(tmp_path):
    wm = WorkingMemory(
        engagement_id="acme-q2-2026",
        client="Acme Corp",
        scope_in_scope=["*.acme.local"],
        scope_out_of_scope=["10.0.99.0/24"],
        objectives=[
            Objective(id="obj_1", goal="Compromise DC", priority="high", progress_pct=60),
        ],
        current_phase="credential_access",
        hosts=[Host(name="DC01", os="Win2022", edr_detected="CrowdStrike", access_level="SYSTEM")],
        accounts=[Account(name="acme\\svc_sql", kind="plaintext", source_host="DC01")],
        defenses_observed=["CrowdStrike Falcon"],
    )
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(wm)
    loaded = repo.load()
    assert loaded.engagement_id == "acme-q2-2026"
    assert loaded.objectives[0].progress_pct == 60
    assert loaded.hosts[0].edr_detected == "CrowdStrike"
    assert loaded.accounts[0].kind == "plaintext"


def test_load_missing_file_raises(tmp_path):
    repo = WorkingMemoryRepo(tmp_path / "nonexistent.json")
    with pytest.raises(FileNotFoundError):
        repo.load()


def test_save_creates_parent_dirs(tmp_path):
    nested = tmp_path / "engagements" / "acme" / "wm.json"
    wm = WorkingMemory(engagement_id="acme")
    WorkingMemoryRepo(nested).save(wm)
    assert nested.exists()


def test_exists_returns_false_for_missing_file(tmp_path):
    repo = WorkingMemoryRepo(tmp_path / "nope.json")
    assert repo.exists() is False


def test_save_leaves_no_temp_file(tmp_path):
    """Atomic save() cleans up its temp via the replace — no leftover .tmp."""
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(WorkingMemory(engagement_id="acme"))
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_overwrites_stale_temp_file(tmp_path):
    """A leftover temp from a crashed prior save must not break the next save."""
    target = tmp_path / "wm.json"
    (tmp_path / "wm.json.tmp").write_text("garbage-half-write")
    repo = WorkingMemoryRepo(target)
    repo.save(WorkingMemory(engagement_id="acme"))
    assert repo.load().engagement_id == "acme"
    assert list(tmp_path.glob("*.tmp")) == []
