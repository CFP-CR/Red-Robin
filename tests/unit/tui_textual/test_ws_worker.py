import asyncio

import pytest

from redrobin.tui.textual_ui import ws_worker
from redrobin.tui.textual_ui.frames import Registry


class _Target:
    pass


def test_build_ws_url_appends_token():
    url = ws_worker.build_ws_url("ws://127.0.0.1:8765/v1/tui", "TOK")
    assert url == "ws://127.0.0.1:8765/v1/tui?token=TOK"


def test_build_ws_url_no_token():
    url = ws_worker.build_ws_url("ws://h/v1/tui", None)
    assert url == "ws://h/v1/tui"


def test_handle_raw_dispatches_valid_frame():
    reg = Registry()
    seen = {}
    reg.register("opsec.verdict")(lambda t, p: seen.update(p))
    target = _Target()

    ws_worker.handle_raw(
        target=target,
        raw='{"type":"opsec.verdict","payload":{"verdict":"BLOCK"}}',
        registry=reg,
    )

    assert seen == {"verdict": "BLOCK"}


def test_handle_raw_ignores_bad_json():
    reg = Registry()
    assert ws_worker.handle_raw(target=None, raw="not json", registry=reg) is False


def test_handle_raw_ignores_unknown_type():
    reg = Registry()
    assert ws_worker.handle_raw(target=None, raw='{"type":"weird"}', registry=reg) is False


@pytest.mark.asyncio
async def test_ws_loop_reports_connecting_then_connected(monkeypatch):
    class _FakeWS:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration  # no frames; connection ends immediately

    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()  # one connection, then the generator is exhausted → loop ends

    monkeypatch.setattr(ws_worker, "connect", _fake_connect)
    states: list[str] = []
    await ws_worker.ws_loop(
        target=object(),
        url="ws://x/v1/tui",
        token=None,
        registry=Registry(),
        stop_event=asyncio.Event(),
        set_state=states.append,
    )
    assert states[:2] == ["connecting", "connected"]
