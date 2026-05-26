from redrobin.tui.textual_ui import frames


def test_register_and_dispatch_calls_handler():
    seen = {}
    reg = frames.Registry()

    @reg.register("demo.event")
    def _handle(target, payload):
        seen["payload"] = payload

    handled = reg.dispatch(target=object(), frame={"type": "demo.event", "payload": {"a": 1}})

    assert handled is True
    assert seen["payload"] == {"a": 1}


def test_dispatch_unknown_type_is_ignored():
    reg = frames.Registry()
    handled = reg.dispatch(target=object(), frame={"type": "nope", "payload": {}})
    assert handled is False


def test_has_handler():
    reg = frames.Registry()
    reg.register("x")(lambda t, p: None)
    assert reg.has_handler("x") is True
    assert reg.has_handler("y") is False


def test_extensibility_new_type_does_not_disturb_existing():
    """Acceptance for the seam: registering a NEW type leaves existing
    routing untouched and dispatchable."""
    reg = frames.Registry()
    calls = []
    reg.register("working_memory_snapshot")(lambda t, p: calls.append("wm"))
    reg.register("advisor.recommendation")(lambda t, p: calls.append("adv"))

    reg.dispatch(target=None, frame={"type": "working_memory_snapshot", "payload": {}})
    reg.dispatch(target=None, frame={"type": "advisor.recommendation", "payload": {}})

    assert calls == ["wm", "adv"]
