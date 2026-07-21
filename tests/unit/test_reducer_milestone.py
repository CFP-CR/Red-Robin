from __future__ import annotations

from redrobin.domain.memory import Objective, WorkingMemory
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def _repo(tmp_path) -> WorkingMemoryRepo:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(
        WorkingMemory(engagement_id="eng-A", objectives=[Objective(id="obj1", goal="Domain Admin")])
    )
    return repo


def test_commit_milestone_writes_phase_objective_killchain(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(
        phase="lateral-movement", milestone_id="obj1", status="COMPLETE", progress_pct=100
    )
    wm = repo.load()
    assert wm.current_phase == "lateral-movement"
    assert wm.objectives[0].status == "COMPLETE"
    assert wm.objectives[0].progress_pct == 100
    assert wm.kill_chain_status["obj1"] == "COMPLETE"


def test_pending_milestone_is_recorded_not_applied(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(
        phase="lateral-movement",
        milestone_id="obj1",
        status="COMPLETE",
        progress_pct=100,
        pending=True,
        prompt="Objective obj1 -> COMPLETE?",
    )
    wm = repo.load()
    assert wm.current_phase != "lateral-movement"  # NOT applied
    assert wm.objectives[0].status == "not_started"  # unchanged
    assert [p.milestone_id for p in wm.pending_milestones] == ["obj1"]
    assert wm.pending_milestones[0].prompt == "Objective obj1 -> COMPLETE?"


def test_commit_removes_matching_pending(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(
        phase="p",
        milestone_id="obj1",
        status="COMPLETE",
        progress_pct=100,
        pending=True,
        prompt="?",
    )
    repo.apply_milestone(
        phase="lateral", milestone_id="obj1", status="COMPLETE", progress_pct=100
    )  # commit
    wm = repo.load()
    assert wm.pending_milestones == []  # pending consumed
    assert wm.objectives[0].status == "COMPLETE"


def test_drop_removes_pending_without_applying(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(
        phase="p",
        milestone_id="obj1",
        status="COMPLETE",
        progress_pct=100,
        pending=True,
        prompt="?",
    )
    repo.apply_milestone(
        phase="", milestone_id="obj1", status="", progress_pct=0, drop=True
    )  # reject
    wm = repo.load()
    assert wm.pending_milestones == []
    assert wm.objectives[0].status == "not_started"  # never applied


def test_reducer_dispatch_routes_engagement_milestone(tmp_path):
    # the reducer treats engagement.milestone as a mutating (snapshot-re-emitting) event
    repo = _repo(tmp_path)
    from redrobin.application.working_memory_reducer import WorkingMemoryReducer

    reducer = WorkingMemoryReducer.__new__(WorkingMemoryReducer)  # construct minimally
    reducer._repo = repo  # type: ignore[attr-defined]
    reducer._episodic = None  # type: ignore[attr-defined]
    reducer._engagement_id = "eng-A"  # type: ignore[attr-defined]
    handled = reducer._dispatch(
        {
            "type": "engagement.milestone",
            "payload": {
                "phase": "recon",
                "milestone_id": "obj1",
                "status": "in_progress",
                "progress_pct": 40,
            },
        }
    )
    assert handled is True
    assert repo.load().current_phase == "recon"
