"""Unit tests for the self-annotation stripper (Red Robin's own console lines)."""

from __future__ import annotations

from redrobin.application.agents.enricher.sanitize import strip_self_annotations

PREFIX = "[Red Robin]"


def test_strips_only_the_annotation_line_keeps_real_output():
    out = "[Red Robin] novel pattern — Layer B reviewing async\njcsp\\jcspi"
    assert strip_self_annotations(out, prefix=PREFIX) == "jcsp\\jcspi"


def test_annotation_only_collapses_to_empty():
    out = "[Red Robin] novel pattern — Layer B reviewing async"
    assert strip_self_annotations(out, prefix=PREFIX) == ""


def test_strips_interior_annotation_between_real_lines():
    out = "Host Name: JCSP\n[Red Robin] novel pattern\nDomain: WORKGROUP"
    assert strip_self_annotations(out, prefix=PREFIX) == "Host Name: JCSP\nDomain: WORKGROUP"


def test_strips_leading_whitespace_before_prefix():
    out = "   [Red Robin] WARN: lsass touch\nreal line"
    assert strip_self_annotations(out, prefix=PREFIX) == "real line"


def test_no_annotation_returns_output_unchanged():
    out = "Host Name: JCSP\nDomain: WORKGROUP"
    assert strip_self_annotations(out, prefix=PREFIX) == out


def test_empty_output_returns_empty():
    assert strip_self_annotations("", prefix=PREFIX) == ""


def test_prefix_with_regex_metacharacters_is_treated_literally():
    # "[Red Robin]" contains regex metachars ([, ]) — must match literally, not as a class.
    out = "[Red Robin] junk\nkeep me"
    assert strip_self_annotations(out, prefix="[Red Robin]") == "keep me"


def test_blank_prefix_is_a_noop_not_strip_everything():
    # An empty/whitespace prefix must NOT match every line (silent data loss);
    # it means "no self-annotation marker configured" → return output unchanged.
    assert strip_self_annotations("a\nb", prefix="") == "a\nb"
    assert strip_self_annotations("a\nb", prefix="   ") == "a\nb"
