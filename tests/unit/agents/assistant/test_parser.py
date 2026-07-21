from redrobin.application.agents.assistant.parser import (
    parse_interpretation,
    parse_recommendation,
)
from redrobin.domain.assistant import OPERATOR_QUESTION_ID_PREFIX
from redrobin.domain.events import Severity


def test_parses_full_recommendation():
    raw = (
        '{"suggested_action":"Validate svc_reports against SQL01",'
        '"rationale":"CrowdStrike observed; a bind is quiet vs a dump",'
        '"opsec_risk":"WARN","confidence":"High cred is valid; unsure scope",'
        '"references":["vault/mssql.md#chunk_0"]}'
    )
    rec = parse_recommendation(raw, correlation_id="18ff")
    assert rec is not None
    assert rec.suggested_action.startswith("Validate")
    assert rec.opsec_risk is Severity.WARN
    assert rec.correlation_id == "18ff"
    assert rec.references == ["vault/mssql.md#chunk_0"]


def test_strips_markdown_fences():
    raw = '```json\n{"suggested_action":"a","rationale":"b"}\n```'
    rec = parse_recommendation(raw, correlation_id=None)
    assert rec is not None and rec.suggested_action == "a"


def test_stay_silent_sentinel_returns_none():
    assert parse_recommendation('{"speak": false}', correlation_id="x") is None


def test_missing_action_or_rationale_returns_none():
    assert parse_recommendation('{"suggested_action":"a"}', correlation_id="x") is None
    assert parse_recommendation('{"rationale":"b"}', correlation_id="x") is None


def test_null_action_or_rationale_returns_none():
    # An explicit JSON null (not a missing key) must be coalesced to "" and dropped —
    # not coerced into the truthy literal "None" that would pass the WHAT/WHY gate.
    assert (
        parse_recommendation('{"suggested_action":null,"rationale":null}', correlation_id="x")
        is None
    )


def test_null_confidence_becomes_empty_string():
    # A valid WHAT+WHY with an explicit null confidence must render as "" — never "None".
    rec = parse_recommendation(
        '{"suggested_action":"a","rationale":"b","confidence":null}', correlation_id="x"
    )
    assert rec is not None
    assert rec.confidence == ""


def test_empty_and_malformed_return_none():
    assert parse_recommendation("", correlation_id="x") is None
    assert parse_recommendation("not json at all", correlation_id="x") is None
    assert parse_recommendation("[1, 2, 3]", correlation_id="x") is None  # not an object


def test_unknown_severity_falls_back_to_info():
    rec = parse_recommendation(
        '{"suggested_action":"a","rationale":"b","opsec_risk":"NOPE"}', correlation_id=None
    )
    assert rec is not None and rec.opsec_risk is Severity.INFO


def test_non_list_numeric_references_does_not_raise():
    # A truthy non-list `references` (5) must NOT raise (never-raises contract) — it
    # is coerced to an empty list, not iterated.
    rec = parse_recommendation(
        '{"suggested_action":"a","rationale":"b","references":5}', correlation_id="x"
    )
    assert rec is not None
    assert rec.references == []


def test_string_references_does_not_char_splat():
    # A string `references` ("abc") must become [] — not char-splat into ["a","b","c"].
    rec = parse_recommendation(
        '{"suggested_action":"a","rationale":"b","references":"abc"}', correlation_id="x"
    )
    assert rec is not None
    assert rec.references == []


# ---- #28 review #1: the reserved operator-question id namespace is disjoint from milestone_id ----


def test_parse_interpretation_drops_reserved_id_no_phase_returns_none():
    # A milestone_id in the reserved operator-question namespace (opq-*) is the exact prefix
    # next_operator_message mints; parse_interpretation drops it so a minted question id can
    # never become an accepted milestone_id. With no phase left, nothing to write -> None.
    assert (
        parse_interpretation(
            '{"milestone_id": "opq-3", "phase": "", "needs_confirm": true, "prompt": "done?"}'
        )
        is None
    )


def test_parse_interpretation_reserved_id_dropped_but_phase_kept_downgrades_confirm():
    it = parse_interpretation('{"milestone_id": "opq-7", "phase": "recon"}')
    assert it is not None
    assert it.milestone_id == ""  # reserved namespace dropped
    assert it.needs_confirm is False  # dropping the id also downgrades needs_confirm


def test_parse_interpretation_semantic_milestone_id_unchanged():
    it = parse_interpretation('{"milestone_id": "obj-dc-compromise", "phase": "recon"}')
    assert it is not None and it.milestone_id == "obj-dc-compromise"


def test_reserved_prefix_constant_is_opq():
    assert OPERATOR_QUESTION_ID_PREFIX == "opq-"
