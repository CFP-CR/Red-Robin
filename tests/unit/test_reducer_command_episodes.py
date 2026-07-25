from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.working_memory_reducer import WorkingMemoryReducer
from redrobin.domain.memory import WorkingMemory
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def _reducer(tmp_path, bus):
    wm_repo = WorkingMemoryRepo(tmp_path / "working_memory.json")
    wm_repo.save(WorkingMemory(engagement_id="eng-1"))
    ep_repo = EpisodicRepo(tmp_path / "episodic.db")
    reducer = WorkingMemoryReducer(bus, wm_repo, "eng-1", episodic_repo=ep_repo)
    return reducer, wm_repo, ep_repo


def test_command_input_records_one_episode(tmp_path):
    reducer, _, ep_repo = _reducer(tmp_path, EventBus())
    reducer._dispatch(
        {
            "type": "beacon.command_input",
            "payload": {
                "bid": "b1",
                "operator": "jose",
                "command": "whoami",
                "raw": "whoami /all",
                "correlation_id": "c-1",
            },
        }
    )
    episodes = ep_repo.list_episodes_for_engagement("eng-1")
    assert len(episodes) == 1
    assert episodes[0].command == "whoami"
    assert episodes[0].correlation_id == "c-1"


def test_command_input_does_not_mutate_working_memory(tmp_path):
    # returning False keeps the reducer from broadcasting a full snapshot per command —
    # nothing in the working memory changed, only the episodic log grew
    reducer, wm_repo, _ = _reducer(tmp_path, EventBus())
    before = wm_repo.load()
    mutated = reducer._dispatch(
        {"type": "beacon.command_input", "payload": {"command": "whoami", "correlation_id": "c-1"}}
    )
    assert mutated is False
    after = wm_repo.load()
    assert after.findings == before.findings
    assert after.sessions == before.sessions


def test_command_input_without_a_command_records_nothing(tmp_path):
    reducer, _, ep_repo = _reducer(tmp_path, EventBus())
    reducer._dispatch({"type": "beacon.command_input", "payload": {"correlation_id": "c-1"}})
    assert ep_repo.list_episodes_for_engagement("eng-1") == []


def test_command_input_missing_correlation_id_still_records(tmp_path):
    reducer, _, ep_repo = _reducer(tmp_path, EventBus())
    reducer._dispatch({"type": "beacon.command_input", "payload": {"command": "ls"}})
    episodes = ep_repo.list_episodes_for_engagement("eng-1")
    assert len(episodes) == 1 and episodes[0].correlation_id == ""


def test_a_failing_episodic_write_does_not_crash_the_reducer(tmp_path):
    class _BoomEpisodic:
        def record(self, **kwargs):
            raise OSError("disk full")

    bus = EventBus()
    wm_repo = WorkingMemoryRepo(tmp_path / "working_memory.json")
    wm_repo.save(WorkingMemory(engagement_id="eng-1"))
    reducer = WorkingMemoryReducer(bus, wm_repo, "eng-1", episodic_repo=_BoomEpisodic())
    # must not raise — the reducer survives a failed episode write
    reducer._dispatch({"type": "beacon.command_input", "payload": {"command": "whoami"}})


def test_episode_has_no_verdict_or_reason_fields():
    import dataclasses

    from redrobin.domain.memory import Episode

    names = {f.name for f in dataclasses.fields(Episode)}
    assert names == {"id", "engagement_id", "correlation_id", "command", "created_at"}


def test_reducer_ignores_opsec_verdict(tmp_path):
    """The pre-flight gate is gone: ``opsec.verdict`` is no longer a handled type.

    It must fall through to the silent-ignore path — no episode, no finding, and
    ``_dispatch`` returning False so no working-memory snapshot is broadcast.
    """
    reducer, wm_repo, ep_repo = _reducer(tmp_path, EventBus())
    mutated = reducer._dispatch(
        {
            "type": "opsec.verdict",
            "payload": {
                "verdict": "BLOCK",
                "command": "mimikatz",
                "reason": "high signal",
                "beacon_id": "B-005",
                "correlation_id": "c-1",
            },
        }
    )
    assert mutated is False
    assert ep_repo.list_episodes_for_engagement("eng-1") == []
    assert wm_repo.load().findings == []


@pytest.mark.asyncio
async def test_end_to_end_over_the_bus(tmp_path):
    bus = EventBus()
    reducer, _, ep_repo = _reducer(tmp_path, bus)
    started = asyncio.Event()
    reducer.on_started = started.set
    task = asyncio.create_task(reducer.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {
            "type": "beacon.command_input",
            "payload": {"command": "seatbelt", "correlation_id": "c-9"},
        }
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if ep_repo.list_episodes_for_engagement("eng-1"):
            break
    assert [e.command for e in ep_repo.list_episodes_for_engagement("eng-1")] == ["seatbelt"]
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
