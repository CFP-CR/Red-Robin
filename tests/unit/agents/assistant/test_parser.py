from redrobin.application.agents.assistant.parser import (
    _extract_json_object,
    parse_interpretation,
    parse_recommendation,
)
from redrobin.domain.assistant import OPERATOR_QUESTION_ID_PREFIX
from redrobin.domain.events import Severity


def test_extract_json_object_plain():
    assert _extract_json_object('{"a": 1}') == '{"a": 1}'


def test_extract_json_object_strips_prose_preamble_and_postamble():
    assert _extract_json_object('Sure — here:\n{"a": 1}\nThat reflects recon.') == '{"a": 1}'


def test_extract_json_object_inside_code_fence():
    assert _extract_json_object('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_object_ignores_braces_inside_strings():
    s = '{"prompt": "mark obj {x} as done?"}'
    assert _extract_json_object("noise " + s + " trailing") == s


def test_extract_json_object_honours_escaped_quote_in_string():
    s = '{"t": "a \\" b {c}"}'
    assert _extract_json_object(s) == s


def test_extract_json_object_truncated_returns_none():
    assert _extract_json_object('{"a": 1, "b": ') is None  # never closes


def test_extract_json_object_no_brace_returns_none():
    assert _extract_json_object("no json here at all") is None


def test_parse_recommendation_recovers_from_prose_preamble():
    raw = (
        "Sure, here is the recommendation:\n"
        '{"suggested_action": "validate creds", "rationale": "quiet", '
        '"opsec_risk": "INFO", "confidence": "high", "references": ["wm:x"]}'
    )
    rec = parse_recommendation(raw, correlation_id=None)
    assert rec is not None and rec.suggested_action == "validate creds"


def test_parse_recommendation_truncated_returns_none():
    assert (
        parse_recommendation('{"suggested_action": "x", "rationale":', correlation_id=None) is None
    )


def test_parse_interpretation_recovers_from_prose_preamble():
    raw = (
        "Based on the state:\n"
        '{"phase": "recon", "milestone_id": "", "status": "", '
        '"progress_pct": 0, "needs_confirm": false, "prompt": ""}'
    )
    it = parse_interpretation(raw)
    assert it is not None and it.phase == "recon"


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


def test_non_string_action_returns_none():
    # A dict/list suggested_action must NOT be str()-coerced into a truthy repr and
    # PUBLISHED — a malformed WHAT stays silent (mirrors the citations/references
    # type guards). Same for a non-string rationale.
    assert (
        parse_recommendation(
            '{"suggested_action": {"x": 1}, "rationale": "because"}', correlation_id=None
        )
        is None
    )
    assert (
        parse_recommendation(
            '{"suggested_action": "do the thing", "rationale": [1, 2]}', correlation_id=None
        )
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


# ---- interactivity increment: a terminal/commit status ALWAYS asks (enforced in code) ----


def test_terminal_status_forces_confirm_even_if_model_said_false():
    it = parse_interpretation(
        '{"milestone_id": "obj-1", "phase": "privilege_escalation", '
        '"status": "COMPLETE", "needs_confirm": false, "prompt": ""}'
    )
    assert it is not None
    assert it.needs_confirm is True  # forced — a close ALWAYS asks, model said false
    assert it.prompt == "Mark obj-1 COMPLETE?"  # synthesized (model omitted the prompt)


def test_terminal_status_preserves_model_prompt_when_given():
    it = parse_interpretation(
        '{"milestone_id": "obj-2", "status": "DONE", "needs_confirm": false, '
        '"prompt": "Confirm obj-2 fully done?"}'
    )
    assert it is not None and it.needs_confirm is True
    assert it.prompt == "Confirm obj-2 fully done?"  # model prompt preserved, not overwritten


def test_terminal_status_is_case_insensitive():
    it = parse_interpretation('{"milestone_id": "obj-3", "status": "complete"}')
    assert it is not None and it.needs_confirm is True


def test_soft_status_stays_auto_write():
    it = parse_interpretation(
        '{"milestone_id": "obj-4", "status": "in_progress", "progress_pct": 55, '
        '"needs_confirm": false}'
    )
    assert it is not None
    assert it.needs_confirm is False  # soft write — no confirm forced


def test_terminal_status_without_milestone_id_stays_soft():
    # a terminal status with no milestone_id cannot be forced (needs an id to track/resolve)
    it = parse_interpretation('{"phase": "exfiltration", "status": "COMPLETE"}')
    assert it is not None
    assert it.milestone_id == "" and it.needs_confirm is False
