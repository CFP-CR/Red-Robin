from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pytest

from redrobin.application.chat.recorder import ChatRecorder, advisory_text, turn_for
from redrobin.application.event_bus import EventBus
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo

_TS = "2026-07-24T05:00:01.000000+00:00"


def test_operator_question_maps_to_a_question_turn():
    turn = turn_for("operator.question", {"text": "path to DA?", "source": "operator"}, ts=_TS)
    assert turn is not None
    assert (turn.role, turn.kind, turn.text) == ("operator", "question", "path to DA?")


def test_assistant_confirm_proposal_maps_to_a_confirm_turn():
    # the assistant's own proposal is discriminated by context_refs (is_own_proposal_question)
    turn = turn_for(
        "operator.question",
        {"text": "Mark obj-2 COMPLETE?", "context_refs": ["milestone:obj-2"]},
        ts=_TS,
    )
    assert turn is not None
    assert (turn.role, turn.kind) == ("assistant", "confirm")


def test_assistant_answer_maps_to_an_assistant_answer_turn():
    turn = turn_for(
        "operator.answer", {"text": "Kerberoast svc_sql", "question_id": "opq-0"}, ts=_TS
    )
    assert turn is not None
    assert (turn.role, turn.kind) == ("assistant", "answer")


def test_operator_confirm_reply_maps_to_an_operator_answer_turn():
    turn = turn_for("operator.answer", {"text": "yes", "source": "operator"}, ts=_TS)
    assert turn is not None
    assert (turn.role, turn.kind, turn.text) == ("operator", "answer", "yes")


def test_reactive_recommendation_is_an_advisory_with_correlation_id():
    turn = turn_for(
        "advisor.recommendation",
        {
            "suggested_action": "Validate svc_reports",
            "rationale": "a quiet bind beats a loud dump",
            "opsec_risk": "WARN",
            "confidence": "high",
            "correlation_id": "B-7:type db_backup.config",
        },
        ts=_TS,
    )
    assert turn is not None
    assert turn.kind == "advisory"
    assert turn.correlation_id == "B-7:type db_backup.config"
    assert "Validate svc_reports" in turn.text and "WARN" in turn.text


def test_proactive_recommendation_is_a_heads_up_without_correlation_id():
    turn = turn_for(
        "advisor.recommendation",
        {
            "suggested_action": "Baseline the host defences",
            "rationale": "a lull",
            "correlation_id": None,
        },
        ts=_TS,
    )
    assert turn is not None
    assert turn.kind == "heads_up"
    assert turn.correlation_id is None


def test_irrelevant_and_empty_events_are_not_recorded():
    assert turn_for("enrichment.completed", {"command": "whoami"}, ts=_TS) is None
    assert turn_for("opsec.verdict", {"verdict": "BLOCK"}, ts=_TS) is None
    assert turn_for("working_memory.deltas", {"hosts": ["h1"]}, ts=_TS) is None
    assert turn_for("operator.question", {"text": "", "source": "operator"}, ts=_TS) is None
    assert turn_for("operator.answer", {"text": ""}, ts=_TS) is None
    assert turn_for("advisor.recommendation", {"suggested_action": ""}, ts=_TS) is None


def test_advisory_text_omits_absent_fields():
    text = advisory_text({"suggested_action": "do X"})
    assert text == "do X"


@pytest.mark.asyncio
async def test_run_records_a_published_event_with_the_injected_clock(tmp_path):
    bus = EventBus()
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    fixed = datetime(2026, 7, 24, 5, 0, 1, tzinfo=timezone.utc)
    rec = ChatRecorder(bus=bus, repo=repo, now=lambda: fixed)
    started = asyncio.Event()
    rec.on_started = started.set
    task = asyncio.create_task(rec.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "operator.question", "payload": {"text": "hola", "source": "operator"}}
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        if repo.tail():
            break
    turns = repo.tail()
    assert [t.text for t in turns] == ["hola"]
    assert turns[0].ts == fixed.isoformat()  # the injected clock stamped it
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_run_survives_an_append_failure_and_never_logs_the_text(tmp_path, caplog):
    class _BoomRepo:
        path = tmp_path / "chat_history.jsonl"

        def append(self, turn):
            raise OSError("disk full")

    bus = EventBus()
    rec = ChatRecorder(bus=bus, repo=_BoomRepo())  # type: ignore[arg-type]
    started = asyncio.Event()
    rec.on_started = started.set
    task = asyncio.create_task(rec.run())
    await asyncio.wait_for(started.wait(), 1.0)
    with caplog.at_level(logging.DEBUG):
        await bus.publish(
            {
                "type": "operator.question",
                "payload": {"text": "SECRET-PASSWORD", "source": "operator"},
            }
        )
        await asyncio.sleep(0.05)
        assert not task.done()  # the failed append did NOT kill the loop
        assert "SECRET-PASSWORD" not in caplog.text  # a turn's text is never logged
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
