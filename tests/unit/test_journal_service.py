from __future__ import annotations

from redrobin.application.journal_service import JournalEntry, join_transcript


def test_join_pairs_input_and_output_by_correlation_id():
    bodies = [
        {
            "type": "beacon.command_input",
            "correlation_id": "c1",
            "raw": "whoami /all",
            "command": "whoami",
        },
        {"type": "beacon.command_output", "correlation_id": "c1", "output": "nt authority\\system"},
    ]
    assert join_transcript(bodies) == [
        JournalEntry(correlation_id="c1", command="whoami /all", output="nt authority\\system")
    ]


def test_join_prefers_raw_then_command():
    bodies = [
        {"type": "beacon.command_input", "correlation_id": "c2", "command": "ls"},  # no raw
        {"type": "beacon.command_output", "correlation_id": "c2", "output": "file.txt"},
    ]
    assert join_transcript(bodies)[0].command == "ls"


def test_join_skips_events_without_correlation_id():
    bodies = [
        {"type": "beacon.command_input", "raw": "no-corr"},
        {"type": "beacon.command_output", "output": "orphan"},
    ]
    assert join_transcript(bodies) == []


def test_join_output_without_matching_input_yields_empty_command():
    bodies = [{"type": "beacon.command_output", "correlation_id": "c3", "output": "stray output"}]
    assert join_transcript(bodies) == [
        JournalEntry(correlation_id="c3", command="", output="stray output")
    ]


def test_join_ignores_unrelated_event_types():
    bodies = [
        {"type": "beacon.initial", "correlation_id": "cx", "host": "WS01"},
        {"type": "beacon.command_input", "correlation_id": "c4", "raw": "ipconfig"},
        {"type": "beacon.command_output", "correlation_id": "c4", "output": "10.0.0.5"},
    ]
    assert [e.command for e in join_transcript(bodies)] == ["ipconfig"]


def test_join_persistent_commands_map_pairs_across_calls():
    # The input is in one batch, its output in the next — a shared `commands` map
    # lets the second call pair them (this is what the incremental refresh relies on).
    commands: dict[str, str] = {}
    first = join_transcript(
        [{"type": "beacon.command_input", "correlation_id": "c5", "raw": "netstat"}], commands
    )
    assert first == []  # input only, nothing to emit yet
    second = join_transcript(
        [{"type": "beacon.command_output", "correlation_id": "c5", "output": "LISTENING"}], commands
    )
    assert second == [JournalEntry(correlation_id="c5", command="netstat", output="LISTENING")]
