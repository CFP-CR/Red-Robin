"""Unit tests for the LLM-output delta parser."""

from __future__ import annotations

import pytest

from redrobin.application.enrichment.parser import DeltaParseError, parse_deltas


def test_parses_plain_json_and_attaches_provenance():
    raw = '{"hosts": [{"name": "WS01", "domain": "ACME"}], "credentials": [], "findings": []}'
    deltas = parse_deltas(raw, source_beacon_id="B-1")
    assert deltas.hosts[0].name == "WS01"
    assert deltas.hosts[0].domain == "ACME"


def test_strips_json_code_fences():
    raw = '```json\n{"credentials": [{"domain": "A", "username": "u", "secret_type": "plaintext", "secret": "pw"}]}\n```'
    deltas = parse_deltas(raw, source_beacon_id="B-2")
    assert deltas.credentials[0].username == "u"
    assert deltas.credentials[0].source_beacon_id == "B-2"  # provenance system-supplied


def test_malformed_json_raises_delta_parse_error():
    with pytest.raises(DeltaParseError):
        parse_deltas("not json at all {", source_beacon_id=None)


def test_non_object_json_raises_delta_parse_error():
    with pytest.raises(DeltaParseError):
        parse_deltas("[1, 2, 3]", source_beacon_id=None)


@pytest.mark.parametrize("raw", ["", "   \n\t ", "```json\n\n```"])
def test_empty_or_blank_input_raises_delta_parse_error(raw):
    # an empty/blank LLM completion is the most likely failure — must raise, not
    # silently produce empty deltas (the enricher retries then drops).
    with pytest.raises(DeltaParseError):
        parse_deltas(raw, source_beacon_id=None)
