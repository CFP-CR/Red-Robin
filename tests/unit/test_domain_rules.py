"""Domain Rule schema — Layer A YAML rule matching (spec §4 Componente 4)."""

from redrobin.domain.rules import Rule, RuleMatch, RuleSeverity


def test_rule_matches_command_with_arg_pattern():
    rule = Rule(
        id="mimikatz_logonpasswords",
        match=RuleMatch(command="mimikatz", arg_pattern=r"sekurlsa::logonpasswords"),
        severity=RuleSeverity.DANGER,
        reason="lsass touch + cred extraction",
    )
    assert rule.matches("mimikatz", "sekurlsa::logonpasswords")
    assert not rule.matches("mimikatz", "standard::coffee")
    assert not rule.matches("shell", "sekurlsa::logonpasswords")


def test_rule_passthrough_no_arg_pattern():
    rule = Rule(
        id="screenshot_warn",
        match=RuleMatch(command="screenshot", arg_pattern=None),
        severity=RuleSeverity.WARN,
        reason="screenshot is medium-risk telemetry",
    )
    assert rule.matches("screenshot", "")
    assert rule.matches("screenshot", "any args at all")


def test_rule_arg_pattern_is_case_insensitive():
    rule = Rule(
        id="invoke_mimikatz",
        match=RuleMatch(command="powershell", arg_pattern=r"Invoke-Mimikatz"),
        severity=RuleSeverity.DANGER,
        reason="Empire-era cmdlet",
    )
    assert rule.matches("powershell", "Invoke-Mimikatz -DumpCreds")
    assert rule.matches("powershell", "invoke-mimikatz -dumpcreds")  # lowercase still matches


def test_rule_severity_enum_3_values():
    assert {s.value for s in RuleSeverity} == {"INFO", "WARN", "DANGER"}


def test_rule_with_suggestions_and_sources():
    rule = Rule(
        id="dcsync_replication",
        match=RuleMatch(command="dcsync", arg_pattern=None),
        severity=RuleSeverity.DANGER,
        reason="T1003.006 replication abuse",
        suggestions=("Use nanodump BOF if SYSTEM", "Or Rubeus /tgtdeleg for Kerb"),
        sources=("MITRE T1003.006",),
    )
    assert rule.suggestions == ("Use nanodump BOF if SYSTEM", "Or Rubeus /tgtdeleg for Kerb")
    assert rule.sources == ("MITRE T1003.006",)
