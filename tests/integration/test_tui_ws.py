"""TUI WebSocket bridge — auth, the initial snapshot, and chat hydration.

Covers both connect frames the endpoint sends: ``working_memory_snapshot`` and, since
#41, ``chat_history``. The echo loop tests were removed in Slice 3: the endpoint no
longer echoes operator input. Bus-driven broadcasting is covered by
``test_tui_ws_broadcast.py``.

Every connect-frame receive goes through ``receive_json_bounded`` — see that helper for
why an unbounded ``receive_json`` would hang the session instead of failing a test.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from tests.integration._ws_helpers import receive_json_bounded


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    # Pin the assistant OFF rather than inheriting it. ``redrobin.cli._load_env_for_cli``
    # calls ``load_dotenv`` on os.environ, so once a CLI test has run, a dev box whose
    # .env sets REDROBIN_ASSISTANT_ENABLED leaks it into every later test in the session —
    # and the lifespan would then leave a chat_history_repo on app.state. These tests own
    # what sits on app.state, so the boot state must not depend on suite order or on a
    # local .env.
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "0")
    # Same reasoning for the boot gate: exits(2) without API keys, and these
    # tests own the socket, not the LLM surface. Pin it rather than inheriting
    # keys some earlier test leaked into os.environ (issue #51).
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    yield root


@pytest.fixture
def client(_isolated_root):
    # Use the ``with`` context so FastAPI's lifespan runs and app.state.event_bus
    # is populated — Slice 3 onwards the WS endpoint reads it on accept.
    with TestClient(create_app()) as client:
        yield client


def test_ws_rejects_wrong_token(client):
    try:
        with client.websocket_connect("/v1/tui?token=wrong"):
            pass
    except Exception:
        # Expected: connection rejected
        return
    pytest.fail("expected websocket to be rejected")


def test_ws_accepts_correct_token_sends_initial_snapshot(client, _isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        msg = receive_json_bounded(ws)
        assert msg["type"] == "working_memory_snapshot"


def test_ws_sends_chat_history_after_the_snapshot(client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    # the assistant is disabled in this fixture, so the lifespan sets no repo: the empty
    # case is the COMMON path, not an edge case
    assert not hasattr(client.app.state, "chat_history_repo")
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = receive_json_bounded(ws)
        second = receive_json_bounded(ws)
    assert first["type"] == "working_memory_snapshot"
    assert second["type"] == "chat_history"
    assert second["payload"]["turns"] == []


def test_ws_chat_history_carries_the_full_turn_shape(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(
        ChatTurn(ts="2026-07-27T00:00:00+00:00", role="operator", kind="question", text="hello")
    )
    repo.append(
        ChatTurn(
            ts="2026-07-27T00:00:01+00:00",
            role="assistant",
            kind="advisory",
            text="enumerate the subnet",
            correlation_id="B-1:whoami",
        )
    )
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        receive_json_bounded(ws)  # working_memory_snapshot
        frame = receive_json_bounded(ws)
    turns = frame["payload"]["turns"]
    assert [t["text"] for t in turns] == ["hello", "enumerate the subnet"]  # oldest first
    # the frame carries the whole ChatTurn, not a subset (spec: the frame contract)
    assert set(turns[0]) == {"ts", "role", "kind", "text", "correlation_id"}
    assert turns[0]["correlation_id"] is None
    assert turns[1]["correlation_id"] == "B-1:whoami"


def test_ws_chat_history_reads_one_bounded_tail_of_the_most_recent_turns(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    inner = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    for i in range(60):
        inner.append(
            ChatTurn(
                ts=f"2026-07-27T00:00:{i:02d}+00:00",
                role="operator",
                kind="question",
                text=f"turn-{i}",
            )
        )

    class _RecordingRepo:
        """Pins that the builder asks the STORE for a bounded window.

        Without this, a `tail()[-50:]` implementation would pass the assertions below
        while reading the whole 2000-line file on every connect.
        """

        def __init__(self, delegate):
            self._delegate = delegate
            self.max_lines_seen = None

        def tail(self, max_lines=None):
            self.max_lines_seen = max_lines
            return self._delegate.tail(max_lines=max_lines)

    repo = _RecordingRepo(inner)
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        receive_json_bounded(ws)
        frame = receive_json_bounded(ws)
    assert repo.max_lines_seen == 50  # one bounded read, not a full read then a slice
    texts = [t["text"] for t in frame["payload"]["turns"]]
    assert len(texts) == 50
    assert texts[0] == "turn-10"  # the MOST RECENT 50...
    assert texts[-1] == "turn-59"  # ...still oldest-first


def test_ws_chat_history_survives_a_failing_repo(client, caplog):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    class _BoomRepo:
        def tail(self, max_lines=None):
            raise OSError("transcript unreadable")

    client.app.state.chat_history_repo = _BoomRepo()
    token = get_or_create_token()
    with caplog.at_level(logging.ERROR):
        with client.websocket_connect(f"/v1/tui?token={token}") as ws:
            receive_json_bounded(ws)
            frame = receive_json_bounded(ws)
    assert frame["type"] == "chat_history"
    assert frame["payload"]["turns"] == []
    # The degradation must be VISIBLE. A bare ``except Exception: pass`` satisfies both
    # assertions above, so without this the operator's only clue that the thread silently
    # failed to hydrate would be an empty pane.
    assert "could not read the chat transcript" in caplog.text
    assert "transcript unreadable" in caplog.text  # logger.exception carried the traceback


def test_ws_chat_history_survives_a_repo_returning_non_turns(client):
    """The connect must not crash on a repo that returns something unexpected.

    ``asdict`` raises TypeError on a plain dict, and the endpoint's outer try catches
    only WebSocketDisconnect, so an unguarded mapping would close the socket with 1011
    and drop the TUI into its reconnect loop.
    """
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    class _WrongShapeRepo:
        def tail(self, max_lines=None):
            return [{"ts": "t", "role": "operator", "kind": "question", "text": "not a turn"}]

    client.app.state.chat_history_repo = _WrongShapeRepo()
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        receive_json_bounded(ws)
        frame = receive_json_bounded(ws)
    assert frame["type"] == "chat_history"
    assert frame["payload"]["turns"] == []
