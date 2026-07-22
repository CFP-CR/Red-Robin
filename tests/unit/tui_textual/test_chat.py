from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog, Static

from redrobin.tui.textual_ui.chat import (
    ChatPane,
    _PLACEHOLDER_CONFIRM,
    _PLACEHOLDER_IDLE,
    format_answer,
    format_confirm_bar,
    format_operator_echo,
    format_recommendation,
    format_resolution,
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


def test_format_confirm_bar_shows_yn_affordance():
    s = format_confirm_bar("Mark obj-1 COMPLETE?").plain
    assert "Mark obj-1 COMPLETE?" in s
    assert "/y" in s.lower() and "/n" in s.lower()


def test_format_resolution_confirmed_and_declined():
    ok = format_resolution("obj-1", True).plain
    assert "obj-1" in ok and "confirmed" in ok.lower()
    no = format_resolution("obj-1", False).plain
    assert "obj-1" in no and "declined" in no.lower()


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
        bar = pane.query_one("#confirm-bar", Static)
        assert bar.display is True  # the confirm reveals the prominent bar...
        assert pane._pending_prompts["obj1"] == "Obj DA COMPLETE?"  # ...tracking the question
        assert len(pane.query_one("#chat-log", RichLog).lines) == 0  # NOT a chat line anymore
        # an operator-sourced echo must NOT track or reveal
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.question",
                "payload": {"text": "x", "context_refs": ["milestone:z"], "source": "operator"},
            },
        )
        assert pane._pending == ["obj1"]


# ---- #28 review #2: consume the pending only on a confirmed send ----


class _SendHarness(App):
    """Harness whose send_operator_message returns a fixed result and records the sends,
    so on_input_submitted's consume-on-confirmed-send logic can be driven directly."""

    def __init__(self, sent_result: bool) -> None:
        super().__init__()
        self._sent_result = sent_result
        self.sent_msgs: list[dict] = []

    def compose(self) -> ComposeResult:
        yield ChatPane(id="events")  # the ChatPane IS the #events pane

    async def send_operator_message(self, msg: dict) -> bool:
        self.sent_msgs.append(msg)
        return self._sent_result


@pytest.mark.asyncio
async def test_pending_not_consumed_when_send_fails():
    # A disconnected/failed send (False) must LEAVE the pending so a re-typed /y after
    # reconnect can still resolve the confirm from the chat.
    async with _SendHarness(sent_result=False).run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        pane._pending = ["obj-x"]
        inp = pane.query_one("#chat-input", Input)
        await pane.on_input_submitted(Input.Submitted(inp, "/y"))
        assert pilot.app.sent_msgs and pilot.app.sent_msgs[0]["question_id"] == "obj-x"
        assert pane._pending == ["obj-x"]  # NOT consumed on a failed send


@pytest.mark.asyncio
async def test_pending_consumed_when_send_confirmed():
    # A confirmed send (True) consumes the pending it targeted.
    async with _SendHarness(sent_result=True).run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        pane._pending = ["obj-x"]
        inp = pane.query_one("#chat-input", Input)
        await pane.on_input_submitted(Input.Submitted(inp, "/y"))
        assert pane._pending == []  # consumed on a confirmed send


@pytest.mark.asyncio
async def test_chat_log_wraps_no_horizontal_overflow():
    # Unit-2 review Minor: recover the overflow coverage 28.2 dropped with #events-log.
    # A wrapping RichLog (wrap=True, min_width=0) never grows a horizontal scrollbar.
    async with _ChatHarness().run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        log = pane.query_one("#chat-log", RichLog)
        assert log.wrap is True
        assert log.min_width == 0


def test_format_recommendation_reactive_has_five_parts_and_advisory_tag():
    payload = {
        "suggested_action": "Validate svc_reports vs SQL01",
        "rationale": "a quiet bind beats a loud dump",
        "opsec_risk": "WARN",
        "confidence": "high cred, unsure scope",
        "references": ["vault/mssql.md#chunk_0"],
        "correlation_id": "B-7:type db_backup.config",  # present -> reactive
    }
    s = format_recommendation(payload).plain
    assert "Red Robin" in s
    assert "advisory" in s and "heads-up" not in s  # reactive tag
    assert "Validate svc_reports" in s  # WHAT
    assert "quiet bind" in s  # WHY
    assert "WARN" in s  # OPSEC
    assert "unsure scope" in s  # confidence
    assert "vault/mssql.md#chunk_0" in s  # refs
    assert "nothing dispatched" in s


def test_format_recommendation_proactive_has_heads_up_tag():
    payload = {
        "suggested_action": "Baseline the host defences",
        "rationale": "no command ran; a lull is a good time to check EDR",
        "opsec_risk": "INFO",
        "confidence": "medium",
        "references": [],
        "correlation_id": None,  # None -> proactive
    }
    s = format_recommendation(payload).plain
    assert "Red Robin" in s
    assert "heads-up" in s and "advisory" not in s
    assert "(none)" in s  # empty refs render "(none)"


@pytest.mark.asyncio
async def test_on_recommendation_appends_to_chat_log():
    from redrobin.tui.textual_ui.chat import _on_recommendation

    async with _ChatHarness().run_test() as pilot:
        _on_recommendation(
            pilot.app,
            {
                "suggested_action": "x",
                "rationale": "y",
                "opsec_risk": "INFO",
                "confidence": "z",
                "references": [],
                "correlation_id": "B:whoami",
            },
        )
        assert len(pilot.app.query_one("#chat-log", RichLog).lines) >= 1


@pytest.mark.asyncio
async def test_chat_log_has_max_lines_cap():
    async with _ChatHarness().run_test() as pilot:
        log = pilot.app.query_one("#chat-log", RichLog)
        assert log.max_lines == 2000


@pytest.mark.asyncio
async def test_recommendation_routes_to_chat_and_dock_is_gone():
    # The app REGISTRY now routes advisor.recommendation to the chat log, and the
    # #advisor-dock no longer exists in the tree.
    from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp

    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        assert not app.query("#advisor-dock")  # dock removed
        assert REGISTRY.has_handler("advisor.recommendation")  # chat now handles it
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "advisor.recommendation",
                "payload": {
                    "suggested_action": "quiet baseline check",
                    "rationale": "why",
                    "opsec_risk": "INFO",
                    "confidence": "high",
                    "references": [],
                    "correlation_id": "B:whoami",
                },
            },
        )
        await pilot.pause()
        assert len(app.query_one("#chat-log", RichLog).lines) >= 1


@pytest.mark.asyncio
async def test_confirm_bar_reveals_on_question_and_hides_on_resolve():
    reg = Registry()
    register_chat(reg)
    async with _SendHarness(sent_result=True).run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        bar = pane.query_one("#confirm-bar", Static)
        inp = pane.query_one("#chat-input", Input)
        assert bar.display is False and inp.placeholder == _PLACEHOLDER_IDLE  # idle at rest
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.question",
                "payload": {
                    "text": "Mark obj-1 COMPLETE?",
                    "context_refs": ["milestone:obj-1"],
                    "source": "assistant",
                },
            },
        )
        assert bar.display is True  # revealed
        assert pane._pending_prompts["obj-1"] == "Mark obj-1 COMPLETE?"  # bar renders this prompt
        assert inp.placeholder == _PLACEHOLDER_CONFIRM  # placeholder reflects the pending confirm
        assert pane._pending == ["obj-1"]
        # resolve with /y -> pending consumed, bar hides, placeholder restores, trace in the log
        await pane.on_input_submitted(Input.Submitted(inp, "/y"))
        assert pane._pending == []
        assert bar.display is False
        assert inp.placeholder == _PLACEHOLDER_IDLE
        # the echo ("/y") + the resolution trace both landed in the chat log
        assert len(pane.query_one("#chat-log", RichLog).lines) >= 2
