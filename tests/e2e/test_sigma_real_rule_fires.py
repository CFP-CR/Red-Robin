"""Slice 14 E2E: a real-shape SigmaHQ rule fires through the verdict route.

Confirms the Slice 11 Sigma matcher extension (``IntegrityLevel`` +
``CommandLine|contains``) survives end-to-end:

    YAML on disk (SigmaHQ rule format)
        → RulesLoader._load_sigma() parses ``detection.selection``
        → emits MatchPredicate(IntegrityLevel='High') + (args contains 'sekurlsa::logonpasswords')
        → LayerAEngine.evaluate(EventContext) AND-checks both predicates
        → POST /v1/verdict/quick returns BLOCK on match, ESCALATE/PASS otherwise

The matching test sends ``integrity_level: "High"`` so both predicates fire;
the negative test omits that field so the IntegrityLevel predicate evaluates
to ``False`` (a missing context field never matches, per Sigma semantics).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture()
def daemon_with_sigma_rule(tmp_path, monkeypatch):
    """Boot the daemon with a SigmaHQ-style rule on disk.

    The fixture writes the YAML under ``REDROBIN_ROOT/rules/shared/sigma/``
    BEFORE the lifespan runs so the initial RulesLoader.load_all() picks
    it up — testing the on-disk loader path the same way an operator's
    rules dir would.
    """
    root = tmp_path / "rr"
    rules_sigma = root / "rules" / "shared" / "sigma"
    rules_sigma.mkdir(parents=True)
    rule = rules_sigma / "mimikatz-high-integrity.yaml"
    rule.write_text(
        "title: Mimikatz invoked at High integrity\n"
        "id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n"
        "logsource:\n"
        "  product: windows\n"
        "detection:\n"
        "  selection:\n"
        "    IntegrityLevel: High\n"
        "    CommandLine|contains: 'sekurlsa::logonpasswords'\n"
        "  condition: selection\n"
        "level: high\n"
    )

    # Pin every path-derivation env var explicitly so the test does not
    # depend on the user's real ``~/.redrobin``. Same idiom as the other
    # e2e fixtures in this directory.
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(root / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-sigma")
    # Slice 3: opt into degraded Layer B explicitly. No real LLM keys here.
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("VOYAGE_API_KEY", "k")

    app = create_app()
    # Using ``with TestClient(...) as client`` triggers the FastAPI lifespan
    # so every start_* helper runs and the Sigma rule we just wrote is
    # actually loaded into Layer A.
    with TestClient(app) as client:
        yield app, client


def _auth_headers() -> dict[str, str]:
    """Return Bearer auth header — the verdict route requires it."""
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return {"Authorization": f"Bearer {get_or_create_token()}"}


def test_sigma_rule_loaded_at_startup(daemon_with_sigma_rule):
    """The Sigma loader produced a Rule with two match_predicates.

    Sanity-check the load path before asserting on verdict behaviour.
    If this fails the verdict tests below would fail for the wrong
    reason (rule not loaded vs predicate mis-evaluated).
    """
    app, _ = daemon_with_sigma_rule
    rules = app.state.rules_loader.list_rules()
    assert len(rules) == 1, f"expected exactly one Sigma rule, got {len(rules)}"
    rule = rules[0]
    assert rule.id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    # Sigma `level: high` → DANGER → BLOCK via SEVERITY_TO_ACTION.
    from redrobin.domain.rules import RuleSeverity

    assert rule.severity == RuleSeverity.DANGER
    # Two predicates: IntegrityLevel + CommandLine|contains.
    assert len(rule.match_predicates) == 2


def test_sigma_rule_fires_on_high_integrity(daemon_with_sigma_rule):
    """High-integrity beacon + matching command → BLOCK (severity high)."""
    _, client = daemon_with_sigma_rule
    resp = client.post(
        "/v1/verdict/quick",
        json={
            "source_event_id": "c-sigma-1",
            "bid": "B-1",
            "command": "mimikatz",
            "raw": 'mimikatz "sekurlsa::logonpasswords"',
            "integrity_level": "High",
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Sigma level=high → RuleSeverity.DANGER → VerdictAction.BLOCK.
    # ESCALATE is acceptable too in case a future change demotes the
    # severity mapping, but BLOCK is the documented behaviour today.
    assert body["action"] == "BLOCK", body
    assert body["rule_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_sigma_rule_does_not_fire_without_high_integrity(daemon_with_sigma_rule):
    """Same command, missing integrity_level → Sigma rule must NOT match.

    Sigma semantics (verified in :func:`_eval_match_predicate`): a missing
    ctx field never matches. So the AND of (IntegrityLevel='High',
    CommandLine|contains='sekurlsa::logonpasswords') evaluates to False
    when ``integrity_level`` is omitted from the request.

    With no rule match Layer A returns ESCALATE (novel-pattern fail-open
    per spec §1.2.1). Layer B is degraded in this test so the retroactive
    correction never fires — the operator sees ESCALATE as the final verdict.
    """
    _, client = daemon_with_sigma_rule
    resp = client.post(
        "/v1/verdict/quick",
        json={
            "source_event_id": "c-sigma-2",
            "bid": "B-1",
            "command": "mimikatz",
            "raw": 'mimikatz "sekurlsa::logonpasswords"',
            # Deliberately no ``integrity_level`` — the rule's
            # IntegrityLevel predicate must therefore fail.
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # No rule match → ESCALATE (Layer B fail-open). PASS is also acceptable
    # for a future passthrough listing of mimikatz — but BLOCK would be a
    # false positive (the Sigma rule's IntegrityLevel constraint must hold).
    assert body["action"] in {"PASS", "ESCALATE"}, body
    assert body["rule_id"] is None
