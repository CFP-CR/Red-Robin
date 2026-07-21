from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import RichLog

from redrobin.tui.textual_ui.chat import (
    ChatPane,
    format_answer,
    format_confirm,
    format_operator_echo,
    is_confirm_question,
    next_operator_message,
    register_chat,
)
from redrobin.domain.assistant import OPERATOR_QUESTION_ID_PREFIX
from redrobin.tui.textual_ui.frames import Registry


def test_format_answer_labels_assistant():
    t = format_answer({"text": "kerberoast then crack", "citations": ["kb:x"]})
    s = t.plain
    assert "Red Robin" in s and "kerberoast then crack" in s


def test_format_confirm_shows_yn_affordance():
    t = format_confirm({"text": "Objective DA -> COMPLETE?", "context_refs": ["milestone:obj1"]})
    s = t.plain
    assert "CONFIRM" in s and "Objective DA -> COMPLETE?" in s
    assert "/y" in s.lower() and "/n" in s.lower()


def test_format_operator_echo_labels_you():
    assert "You" in format_operator_echo("what's my path to DA?").plain


@pytest.mark.asyncio
async def test_chat_pane_composes_and_appends():
    class _Harness(App):
        def compose(self) -> ComposeResult:
            yield ChatPane(id="events")

    async with _Harness().run_test() as pilot:
        pane = pilot.app.query_one(ChatPane)
        pane.append_line(Text("hello"))
        # the RichLog + Input mounted, no exception
        pane.query_one("#chat-input")
        pane.query_one("#chat-log")


# ---- Task 28.3: frame handlers + Input routing (ask vs confirm) ----


def test_is_confirm_question():
    assert is_confirm_question({"context_refs": ["milestone:obj1"]}) is True
    assert is_confirm_question({"context_refs": []}) is False
    assert is_confirm_question({"text": "hi"}) is False


def test_next_operator_message_routes_ask_vs_confirm():
    # no pending -> a new question with a minted id (reserved-namespace prefix, #28 review #1)
    kind, msg = next_operator_message("what's my path to DA?", pending=[], q_index=0)
    assert kind == "question" and msg["type"] == "operator.question"
    assert msg["question_id"] == "opq-0"
    # pending confirm + the EXPLICIT /y token -> an answer to the oldest pending
    kind, msg = next_operator_message("/y", pending=["obj1"], q_index=3)
    assert kind == "answer"
    assert msg["type"] == "operator.answer"
    assert msg["question_id"] == "obj1"
    assert msg["text"] == "yes"
    kind, msg = next_operator_message("/n", pending=["obj1"], q_index=3)
    assert kind == "answer" and msg["text"] == "no"
    # an ORDINARY short word is NEVER a confirm answer, even with a pending -> a question
    # (this is the false-commit fix: "ok" must not commit the milestone)
    kind, msg = next_operator_message("ok", pending=["obj1"], q_index=3)
    assert kind == "question"
    kind, msg = next_operator_message("no creds?", pending=["obj1"], q_index=3)
    assert kind == "question"


def test_next_operator_message_mints_reserved_prefix_id():
    # A minted question id carries OPERATOR_QUESTION_ID_PREFIX — exactly the prefix
    # parse_interpretation rejects in a milestone_id — so a minted question id can NEVER
    # equal an accepted milestone_id (the two namespaces are disjoint by construction).
    kind, msg = next_operator_message("what's my path?", pending=[], q_index=0)
    assert kind == "question"
    assert msg["question_id"].startswith(OPERATOR_QUESTION_ID_PREFIX)
    assert msg["question_id"] == "opq-0"


class _ChatHarness(App):
    def compose(self) -> ComposeResult:
        yield ChatPane(id="events")  # the ChatPane IS the #events pane


@pytest.mark.asyncio
async def test_operator_answer_renders_assistant_skips_own_echo():
    reg = Registry()
    register_chat(reg)
    async with _ChatHarness().run_test() as pilot:
        # our own echo (source=operator) is skipped; the assistant reply renders
        reg.dispatch(
            target=pilot.app,
            frame={"type": "operator.answer", "payload": {"text": "hi", "source": "operator"}},
        )
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.answer",
                "payload": {"text": "kerberoast", "source": "assistant"},
            },
        )
        assert len(pilot.app.query_one("#chat-log", RichLog).lines) == 1  # broken skip -> 2


@pytest.mark.asyncio
async def test_operator_question_confirm_renders_and_tracks_pending():
    reg = Registry()
    register_chat(reg)
    async with _ChatHarness().run_test() as pilot:
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.question",
                "payload": {
                    "text": "Obj DA COMPLETE?",
                    "context_refs": ["milestone:obj1"],
                    "source": "assistant",
                },
            },
        )
        pane = pilot.app.query_one("#events", ChatPane)
        assert pane._pending == ["obj1"]
        assert len(pane.query_one("#chat-log", RichLog).lines) == 1
        # an operator-sourced echo must NOT track or render
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.question",
                "payload": {
                    "text": "x",
                    "context_refs": ["milestone:z"],
                    "source": "operator",
                },
            },
        )
        assert pane._pending == ["obj1"]


@pytest.mark.asyncio
async def test_chat_log_wraps_no_horizontal_overflow():
    # Unit-2 review Minor: recover the overflow coverage 28.2 dropped with #events-log.
    # A wrapping RichLog (wrap=True, min_width=0) never grows a horizontal scrollbar.
    async with _ChatHarness().run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        log = pane.query_one("#chat-log", RichLog)
        assert log.wrap is True
        assert log.min_width == 0
