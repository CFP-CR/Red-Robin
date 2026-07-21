from __future__ import annotations

import pytest

from redrobin.tui.textual_ui.chat import (
    ChatPane,
    format_answer,
    format_confirm,
    format_operator_echo,
)


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
    from textual.app import App, ComposeResult
    from rich.text import Text

    class _Harness(App):
        def compose(self) -> ComposeResult:
            yield ChatPane(id="events")

    async with _Harness().run_test() as pilot:
        pane = pilot.app.query_one(ChatPane)
        pane.append_line(Text("hello"))
        # the RichLog + Input mounted, no exception
        pane.query_one("#chat-input")
        pane.query_one("#chat-log")
