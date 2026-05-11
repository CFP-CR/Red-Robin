"""Slash command parser tests."""

from redrobin.tui.slash_commands import SlashCommand, parse


def test_parse_returns_none_for_non_slash():
    assert parse("mimikatz logonpasswords") is None


def test_parse_returns_none_for_no_prefix():
    """Plain operator commands without a prefix are dispatcher input, not slash."""
    assert parse("alts please") is None


def test_parse_alts():
    assert parse(":rr alts") == SlashCommand(verb="alts", args=[])


# -----------------------------------------------------------------------------
# Slash short-form: ``/<verb> [args]`` is the TUI palette shorthand.
# Added in Phase 2 condition 3 to wire the ``/`` keybinding to the parser.
# -----------------------------------------------------------------------------


def test_parse_slash_shorthand_alts():
    assert parse("/alts") == SlashCommand(verb="alts", args=[])


def test_parse_slash_shorthand_with_args():
    cmd = parse('/override "client requested live demo"')
    assert cmd == SlashCommand(verb="override", args=["client requested live demo"])


def test_parse_slash_shorthand_exec():
    cmd = parse("/exec mimikatz logonpasswords")
    assert cmd == SlashCommand(verb="exec", args=["mimikatz", "logonpasswords"])


def test_parse_slash_shorthand_empty_routes_to_help():
    """Bare ``/`` opens the palette → routes to help so the operator sees verbs."""
    assert parse("/") == SlashCommand(verb="help", args=[])


def test_parse_slash_shorthand_unknown_verb_routes_to_help():
    cmd = parse("/rr alts")
    assert cmd is not None
    assert cmd.verb == "help"
    assert cmd.args == ["rr", "alts"]


def test_parse_explain_with_args():
    cmd = parse(":rr explain")
    assert cmd == SlashCommand(verb="explain", args=[])


def test_parse_override_with_quoted_reason():
    cmd = parse(':rr override "client requested live demo"')
    assert cmd == SlashCommand(verb="override", args=["client requested live demo"])


def test_parse_exec_with_command():
    cmd = parse(":rr exec mimikatz logonpasswords")
    assert cmd == SlashCommand(verb="exec", args=["mimikatz", "logonpasswords"])


def test_parse_promote_with_quoted_lesson():
    cmd = parse(':rr promote "nanodump invisible to CrowdStrike"')
    assert cmd == SlashCommand(verb="promote", args=["nanodump invisible to CrowdStrike"])


def test_parse_milestone_with_quoted_text():
    cmd = parse(':rr milestone "Got initial foothold"')
    assert cmd == SlashCommand(verb="milestone", args=["Got initial foothold"])


def test_parse_phase_with_name():
    cmd = parse(":rr phase credential_access")
    assert cmd == SlashCommand(verb="phase", args=["credential_access"])


def test_parse_objective_with_pct():
    cmd = parse(":rr objective obj_1 75")
    assert cmd == SlashCommand(verb="objective", args=["obj_1", "75"])


def test_parse_inspector_no_args():
    cmd = parse(":rr inspector")
    assert cmd == SlashCommand(verb="inspector", args=[])


def test_parse_skip_no_args():
    cmd = parse(":rr skip")
    assert cmd == SlashCommand(verb="skip", args=[])


def test_parse_help_returns_help():
    cmd = parse(":rr help")
    assert cmd == SlashCommand(verb="help", args=[])


def test_parse_unknown_verb_routes_to_help():
    cmd = parse(":rr nonsense args")
    assert cmd is not None
    assert cmd.verb == "help"
    assert cmd.args == ["nonsense", "args"]


def test_parse_empty_after_rr_routes_to_help():
    cmd = parse(":rr")
    assert cmd == SlashCommand(verb="help", args=[])


def test_parse_just_rr_with_space_routes_to_help():
    cmd = parse(":rr   ")
    assert cmd == SlashCommand(verb="help", args=[])


def test_parse_unbalanced_quotes_returns_none():
    assert parse(':rr override "unclosed') is None


def test_parse_strips_leading_trailing_whitespace():
    cmd = parse("  :rr alts  ")
    assert cmd == SlashCommand(verb="alts", args=[])


def test_parse_lowercases_verb():
    # Mixed-case verbs route through lower() and resolve to canonical verb
    cmd = parse(":rr ALTS")
    assert cmd == SlashCommand(verb="alts", args=[])
