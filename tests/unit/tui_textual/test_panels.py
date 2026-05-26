from rich.text import Text

from redrobin.tui.textual_ui import panels


def test_format_verdict_is_text_with_command_and_source():
    out = panels.format_verdict(
        {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a", "reason": "lsass"}
    )
    assert isinstance(out, Text)
    plain = out.plain
    assert "BLOCK" in plain
    assert "mimikatz" in plain
    assert "layer_a" in plain
    assert "lsass" in plain


def test_format_verdict_no_reason_omits_dash():
    out = panels.format_verdict({"verdict": "PASS", "command": "whoami", "source": "layer_b"})
    assert "—" not in out.plain


def test_apply_verdict_appends_layer_a():
    events = panels.apply_verdict(
        [], {"correlation_id": "c1", "verdict": "PASS", "source": "layer_a"}
    )
    assert len(events) == 1
    assert events[0]["verdict"] == "PASS"


def test_apply_verdict_layer_b_corrects_in_place():
    events = panels.apply_verdict(
        [], {"correlation_id": "c1", "verdict": "PASS", "source": "layer_a", "command": "mimikatz"}
    )
    events = panels.apply_verdict(
        events,
        {
            "correlation_id": "c1",
            "verdict": "BLOCK",
            "previous_verdict": "PASS",
            "source": "layer_b",
        },
    )
    assert len(events) == 1  # corrected in place, not appended
    assert events[0]["verdict"] == "BLOCK"
    assert events[0]["previous_verdict"] == "PASS"
    assert events[0]["command"] == "mimikatz"  # original fields preserved


def test_apply_verdict_layer_b_no_match_appends():
    events = panels.apply_verdict(
        [], {"correlation_id": "zzz", "verdict": "BLOCK", "source": "layer_b"}
    )
    assert len(events) == 1


def test_apply_verdict_caps_at_500():
    events: list[dict] = []
    for i in range(520):
        events = panels.apply_verdict(
            events, {"correlation_id": str(i), "verdict": "PASS", "source": "layer_a"}
        )
    assert len(events) == 500
    assert events[-1]["correlation_id"] == "519"
