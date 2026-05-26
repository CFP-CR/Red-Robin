def test_run_tui_launches_the_textual_app(monkeypatch):
    captured = {}

    class _FakeApp:
        def __init__(self, *, ws_url=None, token=None, **kwargs):
            captured["ws_url"] = ws_url
            captured["token"] = token

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr("redrobin.tui.textual_ui.app.RedRobinApp", _FakeApp)

    from redrobin.cli_helpers.tui_cmd import run_tui

    rc = run_tui(connect_ws="ws://127.0.0.1:7777/v1/tui", connect_http="http://x", token="TOK")

    assert rc == 0
    assert captured["ws_url"] == "ws://127.0.0.1:7777/v1/tui"
    assert captured["token"] == "TOK"
    assert captured["ran"] is True
