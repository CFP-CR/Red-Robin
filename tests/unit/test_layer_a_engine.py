"""Layer A deterministic rule engine — spec §1.2.1 Stage 2."""

from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.domain.opsec import VerdictAction
from redrobin.domain.rules import Rule, RuleMatch, RuleSeverity


def test_layer_a_returns_escalate_when_no_rule_matches_and_not_passthrough():
    engine = LayerAEngine(rules=[], passthrough=set())
    v = engine.evaluate(command="random_cmd", args="anything")
    assert v.action == VerdictAction.ESCALATE


def test_layer_a_returns_pass_for_passthrough():
    engine = LayerAEngine(rules=[], passthrough={"pwd", "cd", "ls"})
    v = engine.evaluate(command="pwd", args="")
    assert v.action == VerdictAction.PASS
    assert v.reason == "passthrough"


def test_layer_a_returns_block_on_danger_match():
    rule = Rule(
        id="mimikatz_logonpasswords",
        match=RuleMatch(command="mimikatz", arg_pattern="logonpasswords"),
        severity=RuleSeverity.DANGER,
        reason="lsass touch",
    )
    engine = LayerAEngine(rules=[rule], passthrough=set())
    v = engine.evaluate(command="mimikatz", args="sekurlsa::logonpasswords")
    assert v.action == VerdictAction.BLOCK
    assert v.rule_id == "mimikatz_logonpasswords"
    assert "lsass" in v.reason


def test_layer_a_returns_warn_on_warn_match():
    rule = Rule(
        id="screenshot_warn",
        match=RuleMatch(command="screenshot", arg_pattern=None),
        severity=RuleSeverity.WARN,
        reason="screenshot is medium-risk",
    )
    engine = LayerAEngine(rules=[rule], passthrough=set())
    v = engine.evaluate(command="screenshot", args="")
    assert v.action == VerdictAction.WARN
    assert v.rule_id == "screenshot_warn"


def test_layer_a_returns_pass_on_info_match():
    rule = Rule(
        id="ad_recon_info",
        match=RuleMatch(command="net", arg_pattern="group"),
        severity=RuleSeverity.INFO,
        reason="AD recon — low-noise enum",
    )
    engine = LayerAEngine(rules=[rule], passthrough=set())
    v = engine.evaluate(command="net", args="group Domain Admins")
    assert v.action == VerdictAction.PASS
    assert v.rule_id == "ad_recon_info"


def test_layer_a_first_match_wins():
    rule_a = Rule(
        id="generic_powershell",
        match=RuleMatch(command="powershell", arg_pattern=None),
        severity=RuleSeverity.WARN,
        reason="powershell warn (AMSI)",
    )
    rule_b = Rule(
        id="invoke_mimikatz",
        match=RuleMatch(command="powershell", arg_pattern="Invoke-Mimikatz"),
        severity=RuleSeverity.DANGER,
        reason="Empire-era cmdlet",
    )
    # rules ordered; first match wins
    engine = LayerAEngine(rules=[rule_a, rule_b], passthrough=set())
    v = engine.evaluate(command="powershell", args="Invoke-Mimikatz")
    # First rule matched (rule_a generic) → WARN, not DANGER
    assert v.action == VerdictAction.WARN
    assert v.rule_id == "generic_powershell"


def test_layer_a_passthrough_overrides_rules():
    rule = Rule(
        id="pwd_danger",
        match=RuleMatch(command="pwd", arg_pattern=None),
        severity=RuleSeverity.DANGER,
        reason="for test only",
    )
    engine = LayerAEngine(rules=[rule], passthrough={"pwd"})
    v = engine.evaluate(command="pwd", args="")
    # passthrough wins
    assert v.action == VerdictAction.PASS


def test_layer_a_update_rules_hot_reload():
    engine = LayerAEngine(rules=[], passthrough=set())
    new_rule = Rule(
        id="hot_rule",
        match=RuleMatch(command="mimikatz", arg_pattern=None),
        severity=RuleSeverity.DANGER,
        reason="injected",
    )
    engine.update_rules([new_rule])
    v = engine.evaluate(command="mimikatz", args="")
    assert v.action == VerdictAction.BLOCK
    assert v.rule_id == "hot_rule"


def test_layer_a_carries_suggestions():
    rule = Rule(
        id="dcsync_replication",
        match=RuleMatch(command="dcsync", arg_pattern=None),
        severity=RuleSeverity.DANGER,
        reason="T1003.006",
        suggestions=("Use nanodump", "Or Rubeus"),
    )
    engine = LayerAEngine(rules=[rule], passthrough=set())
    v = engine.evaluate(command="dcsync", args="corp.local")
    assert v.suggestions == ["Use nanodump", "Or Rubeus"]
