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
