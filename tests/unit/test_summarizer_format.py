from __future__ import annotations

from datetime import datetime

from redrobin.application.agents.summarizer import L5Summarizer
from redrobin.domain.memory import Episode


def test_format_renders_a_command_sequence():
    episodes = [
        Episode(
            id=1,
            engagement_id="eng-1",
            correlation_id="c-1",
            command="whoami",
            created_at=datetime(2026, 7, 25, 12, 0, 0),
        ),
        Episode(
            id=2,
            engagement_id="eng-1",
            correlation_id="c-2",
            command="type db_backup.config",
            created_at=datetime(2026, 7, 25, 12, 0, 5),
        ),
    ]
    out = L5Summarizer._format(episodes)
    assert "- 2026-07-25T12:00:00 whoami" in out
    assert "- 2026-07-25T12:00:05 type db_backup.config" in out
    assert "[" not in out.split("\n", 1)[1]  # no leftover verdict bracket in the episode lines


def test_format_header_does_not_promise_verdicts():
    """The prompt header must describe a command sequence, not gate verdicts.

    The header is sent to the summariser LLM verbatim; if it still asks for
    "(command, verdict) patterns" or "blocked tradecraft" the model is being
    told to narrate a signal that no longer exists in its input.
    """
    out = L5Summarizer._format([])
    lowered = out.lower()
    for word in ("verdict", "blocked", "escalation"):
        assert word not in lowered
