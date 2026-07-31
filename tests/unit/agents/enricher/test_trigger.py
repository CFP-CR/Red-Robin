"""Unit tests for the enrichment trigger (enrich-by-default + denylist)."""

from __future__ import annotations

from redrobin.application.agents.enricher.trigger import should_enrich

DENY = ["ls", "pwd", "sleep", "cd"]


def test_enriches_unknown_command_by_default():
    assert should_enrich("systeminfo", "x" * 50, denylist=DENY, min_output_chars=8)


def test_skips_denylisted_bare_command():
    assert not should_enrich("ls", "a" * 50, denylist=DENY, min_output_chars=8)


def test_skips_denylisted_shell_wrapped_command():
    assert not should_enrich("shell pwd", "a" * 50, denylist=DENY, min_output_chars=8)


def test_enriches_shell_wrapped_unknown_command():
    assert should_enrich("shell systeminfo", "x" * 50, denylist=DENY, min_output_chars=8)


def test_skips_short_or_empty_output():
    assert not should_enrich("systeminfo", "  ", denylist=DENY, min_output_chars=8)
    assert not should_enrich("systeminfo", "tiny", denylist=DENY, min_output_chars=8)


def test_bare_shell_with_no_inner_verb_enriches():
    # "shell" alone: no inner command -> safe default (enrich)
    assert should_enrich("shell", "x" * 50, denylist=DENY, min_output_chars=8)


def test_skips_shell_wrapped_denylisted_with_args():
    # real beacon commands carry args; the inner verb is still matched
    assert not should_enrich("shell ls -la /tmp", "a" * 50, denylist=DENY, min_output_chars=8)
