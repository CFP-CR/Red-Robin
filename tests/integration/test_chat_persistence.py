from __future__ import annotations

import asyncio

import pytest

from redrobin.application.chat.history import build_history_reader
from redrobin.application.chat.recorder import ChatRecorder
from redrobin.application.event_bus import EventBus
from redrobin.infrastructure.config import Settings
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo


def test_settings_expose_the_two_chat_window_knobs():
    s = Settings()
    assert s.assistant_chat_dialogue_turns == 10
    assert s.assistant_chat_advisory_turns == 5


def test_settings_chat_knobs_read_from_env(monkeypatch):
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS", "3")
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS", "1")
    s = Settings()
    assert s.assistant_chat_dialogue_turns == 3
    assert s.assistant_chat_advisory_turns == 1


def test_settings_chat_knobs_accept_zero_but_reject_negative(monkeypatch):
    # 0 legitimately disables a window (the selector selects nothing from that group);
    # a negative window is nonsense and must not boot.
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS", "0")
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS", "0")
    s = Settings()
    assert (s.assistant_chat_dialogue_turns, s.assistant_chat_advisory_turns) == (0, 0)

    # pydantic reports the ENV ALIAS, which is what the operator actually set.
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS", "-1")
    with pytest.raises(Exception) as exc:
        Settings()
    assert "REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS" in str(exc.value)
    assert "greater than or equal to 0" in str(exc.value)

    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_DIALOGUE_TURNS", "10")
    monkeypatch.setenv("REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS", "-5")
    with pytest.raises(Exception) as exc:
        Settings()
    assert "REDROBIN_ASSISTANT_CHAT_ADVISORY_TURNS" in str(exc.value)


@pytest.mark.asyncio
async def test_bus_events_become_a_readable_conversation(tmp_path):
    # End-to-end over a real EventBus + a real repo: the recorder persists the stream and
    # the reader renders it back for the model.
    bus = EventBus()
    repo = ChatHistoryRepo(tmp_path / "engagements" / "vantage" / "chat_history.jsonl")
    recorder = ChatRecorder(bus=bus, repo=repo)
    started = asyncio.Event()
    recorder.on_started = started.set
    task = asyncio.create_task(recorder.run())
    await asyncio.wait_for(started.wait(), 1.0)

    await bus.publish(
        {"type": "operator.question", "payload": {"text": "path to DA?", "source": "operator"}}
    )
    await bus.publish(
        {
            "type": "operator.answer",
            "payload": {"text": "Kerberoast svc_sql", "question_id": "opq-0"},
        }
    )
    await bus.publish(
        {
            "type": "advisor.recommendation",
            "payload": {
                "suggested_action": "Validate svc_reports",
                "rationale": "quiet bind",
                "opsec_risk": "WARN",
                "confidence": "high",
                "correlation_id": "B-7:whoami",
            },
        }
    )
    await bus.publish({"type": "enrichment.completed", "payload": {"command": "whoami"}})  # ignored

    for _ in range(100):
        await asyncio.sleep(0.01)
        if len(repo.tail()) >= 3:
            break

    turns = repo.tail()
    assert [(t.role, t.kind) for t in turns] == [
        ("operator", "question"),
        ("assistant", "answer"),
        ("assistant", "advisory"),
    ]

    rendered = build_history_reader(repo, dialogue_max=10, volunteered_max=5)()
    assert "[operator] path to DA?" in rendered
    assert "[assistant] Kerberoast svc_sql" in rendered
    assert "[assistant · advisory] Validate svc_reports" in rendered

    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_transcript_survives_a_recorder_restart(tmp_path):
    # A daemon restart must not lose the thread: a fresh recorder appends to the same file.
    path = tmp_path / "chat_history.jsonl"
    for text in ("first run", "second run"):
        bus = EventBus()
        repo = ChatHistoryRepo(path)
        recorder = ChatRecorder(bus=bus, repo=repo)
        started = asyncio.Event()
        recorder.on_started = started.set
        task = asyncio.create_task(recorder.run())
        await asyncio.wait_for(started.wait(), 1.0)
        await bus.publish(
            {"type": "operator.question", "payload": {"text": text, "source": "operator"}}
        )
        for _ in range(100):
            await asyncio.sleep(0.01)
            if any(t.text == text for t in repo.tail()):
                break
        await bus.publish({"_type": "_close"})
        await asyncio.wait_for(task, 1.0)

    assert [t.text for t in ChatHistoryRepo(path).tail()] == ["first run", "second run"]


def _boot_env(tmp_path, monkeypatch) -> None:
    """The degraded-boot env the other lifespan tests use (no engagement, no API keys)."""
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-chat")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)


@pytest.mark.asyncio
async def test_lifespan_records_chat_beside_the_working_memory(tmp_path, monkeypatch):
    # The activation: with the assistant on, the recorder runs and its file is DERIVED
    # from the engagement dir start_working_memory already resolved (never re-resolved).
    _boot_env(tmp_path, monkeypatch)
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "1")
    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        repo = app.state.chat_history_repo
        assert repo.path == app.state.working_memory_repo.path.parent / "chat_history.jsonl"
        assert app.state.chat_recorder is not None
        assert not app.state.chat_recorder_task.done()

        await app.state.event_bus.publish(
            {"type": "operator.question", "payload": {"text": "am I loud?", "source": "operator"}}
        )
        # Poll for THIS turn, not for "any turn": the assistant task is live on the same
        # bus, so waiting on the first arrival and then asserting a 1-element list would
        # race anything else the daemon appends.
        for _ in range(100):
            await asyncio.sleep(0.01)
            turns = repo.tail()
            if any(t.text == "am I loud?" for t in turns):
                break
        recorded = [(t.role, t.kind, t.text) for t in repo.tail()]
        assert ("operator", "question", "am I loud?") in recorded

    # Clean teardown: the recorder drains on the bus sentinel (close_bus runs before
    # close_assistant), NOT via the 2.0 s cancel fallback.
    assert app.state.chat_recorder_task.done()
    assert app.state.chat_recorder_task.cancelled() is False


@pytest.mark.asyncio
async def test_no_chat_recorder_when_the_assistant_is_disabled(tmp_path, monkeypatch):
    # Gated like kb/journal: with the assistant off there is no stream to record, so
    # nothing is built and no transcript file is opened.
    _boot_env(tmp_path, monkeypatch)
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "0")
    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert getattr(app.state, "chat_recorder", None) is None
        assert getattr(app.state, "chat_history_repo", None) is None
        assert getattr(app.state, "chat_recorder_task", None) is None
        # Drive the bus with an event the recorder WOULD have recorded. Asserting on the
        # path alone proves nothing — the repo only holds a Path and creates the file in
        # append, so a built-but-idle recorder leaves no file either.
        await app.state.event_bus.publish(
            {"type": "operator.question", "payload": {"text": "am I loud?", "source": "operator"}}
        )
        for _ in range(20):
            await asyncio.sleep(0.01)
    assert list(tmp_path.rglob("chat_history.jsonl")) == []
