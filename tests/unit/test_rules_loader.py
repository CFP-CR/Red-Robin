"""YAML rules loader with hot-reload via watchdog."""

import pytest

from redrobin.infrastructure.rules_loader import RulesLoadError, RulesLoader


def test_load_single_yaml_rule(tmp_path):
    rule_file = tmp_path / "mimikatz.yaml"
    rule_file.write_text("""
id: mimikatz_logonpasswords
match:
  command: mimikatz
  arg_pattern: 'sekurlsa::logonpasswords'
severity: DANGER
reason: 'lsass touch + cred extraction (T1003.001)'
suggestions:
  - 'Use nanodump BOF'
  - 'Or Rubeus /tgtdeleg'
sources:
  - 'MITRE T1003.001'
""")
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    assert len(rules) == 1
    assert rules[0].id == "mimikatz_logonpasswords"
    assert rules[0].matches("mimikatz", "sekurlsa::logonpasswords")


def test_load_multiple_yaml_rules_sorted(tmp_path):
    (tmp_path / "b_rule.yaml").write_text("""
id: rule_b
match:
  command: shell
severity: WARN
reason: 'shell warn'
""")
    (tmp_path / "a_rule.yaml").write_text("""
id: rule_a
match:
  command: mimikatz
severity: DANGER
reason: 'mimikatz danger'
""")
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    assert len(rules) == 2
    # Sorted by path → rule_a (from a_rule.yaml) before rule_b
    assert rules[0].id == "rule_a"
    assert rules[1].id == "rule_b"


def test_load_skips_passthrough_yaml(tmp_path):
    (tmp_path / "passthrough.yaml").write_text("""
# This is the passthrough whitelist, handled separately
allowed_commands: [pwd, cd, ls]
""")
    (tmp_path / "real.yaml").write_text("""
id: real_rule
match:
  command: mimikatz
severity: DANGER
reason: 'x'
""")
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    assert len(rules) == 1
    assert rules[0].id == "real_rule"


def test_load_walks_subdirs(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "shared_rule.yaml").write_text("""
id: shared_rule
match: {command: mimikatz}
severity: DANGER
reason: 'x'
""")
    (tmp_path / "personal").mkdir()
    (tmp_path / "personal" / "personal_rule.yaml").write_text("""
id: personal_rule
match: {command: shell}
severity: WARN
reason: 'y'
""")
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    assert {r.id for r in rules} == {"shared_rule", "personal_rule"}


def test_load_rejects_unknown_severity(tmp_path):
    (tmp_path / "bad.yaml").write_text("""
id: bad
match: {command: x}
severity: WHATEVER
reason: 'invalid severity'
""")
    loader = RulesLoader(rule_dir=tmp_path)
    with pytest.raises(RulesLoadError, match="severity"):
        loader.load_all()


def test_load_rejects_missing_required_field(tmp_path):
    (tmp_path / "missing.yaml").write_text("""
id: missing_reason
match: {command: mimikatz}
severity: DANGER
""")
    loader = RulesLoader(rule_dir=tmp_path)
    with pytest.raises(RulesLoadError, match="reason"):
        loader.load_all()


# -----------------------------------------------------------------------------
# Sigma format support — Phase 2 blocker 2.
# -----------------------------------------------------------------------------


def test_sigma_contains_matches_substring(tmp_path):
    """Sigma ``CommandLine|contains`` should match a substring of args, not
    the filename or exact equality. Previously the stub set
    ``match.command = path.stem`` and the rule never matched in practice."""
    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "mimikatz_creds.yaml").write_text(
        "title: Mimikatz LSASS dump\n"
        "id: 00000000-0000-0000-0000-000000000010\n"
        "logsource: {product: windows}\n"
        "detection:\n"
        "  selection:\n"
        "    CommandLine|contains: 'logonpasswords'\n"
        "  condition: selection\n"
        "level: high\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    assert len(rules) == 1
    rule = rules[0]
    # The arg string ``sekurlsa::logonpasswords`` contains ``logonpasswords``.
    assert rule.matches("mimikatz", "sekurlsa::logonpasswords")
    # The arg string ``whoami /priv`` does not contain ``logonpasswords``.
    assert not rule.matches("whoami", "whoami /priv")


def test_sigma_contains_list_is_or(tmp_path):
    """``CommandLine|contains: [a, b]`` is OR — match if any item matches."""
    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "mimikatz_creds.yaml").write_text(
        "title: Mimikatz cred theft variants\n"
        "id: 00000000-0000-0000-0000-000000000011\n"
        "logsource: {product: windows}\n"
        "detection:\n"
        "  selection:\n"
        "    CommandLine|contains:\n"
        "      - 'sekurlsa::logonpasswords'\n"
        "      - 'lsadump::lsa'\n"
        "  condition: selection\n"
        "level: high\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    rule = rules[0]
    assert rule.matches("mimikatz", "sekurlsa::logonpasswords /patch")
    assert rule.matches("mimikatz", "lsadump::lsa /domain")
    assert not rule.matches("mimikatz", "standard::coffee")


def test_sigma_startswith_and_endswith(tmp_path):
    """``startswith`` and ``endswith`` map to args prefix/suffix matching."""
    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "ps_invoke.yaml").write_text(
        "title: PowerShell Invoke-Suspicious\n"
        "id: 00000000-0000-0000-0000-000000000012\n"
        "logsource: {product: windows}\n"
        "detection:\n"
        "  selection:\n"
        "    CommandLine|startswith: 'Invoke-'\n"
        "    CommandLine|endswith: '.ps1'\n"
        "  condition: selection\n"
        "level: medium\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    rule = rules[0]
    # Both predicates must hold.
    assert rule.matches("powershell", "Invoke-Mimikatz dump.ps1")
    assert not rule.matches("powershell", "Get-Process dump.ps1")  # startswith fails
    assert not rule.matches("powershell", "Invoke-Mimikatz dump.exe")  # endswith fails


def test_sigma_exact_command_equality(tmp_path):
    """``CommandLine: x`` (no modifier) is an exact equality check on args."""
    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "exact_match.yaml").write_text(
        "title: Exact match\n"
        "id: 00000000-0000-0000-0000-000000000013\n"
        "logsource: {product: windows}\n"
        "detection:\n"
        "  selection:\n"
        "    CommandLine: 'whoami /all'\n"
        "  condition: selection\n"
        "level: low\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    rule = rules[0]
    assert rule.matches("whoami", "whoami /all")
    assert not rule.matches("whoami", "whoami /priv")


def test_sigma_image_endswith_filters_command(tmp_path):
    """Sigma ``Image|endswith: '\\\\mimikatz.exe'`` should constrain the
    command name (mapped from process image basename)."""
    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "by_image.yaml").write_text(
        "title: Mimikatz by image\n"
        "id: 00000000-0000-0000-0000-000000000014\n"
        "logsource: {product: windows}\n"
        "detection:\n"
        "  selection:\n"
        "    Image|endswith: '\\\\mimikatz.exe'\n"
        "    CommandLine|contains: 'logonpasswords'\n"
        "  condition: selection\n"
        "level: high\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    rule = rules[0]
    assert rule.matches("mimikatz", "sekurlsa::logonpasswords")
    # An ``Image|endswith`` constraint that fails should block the match
    # even if the CommandLine contains check passes.
    assert not rule.matches("nanodump", "sekurlsa::logonpasswords")


def test_sigma_level_high_maps_to_danger(tmp_path):
    """``level: high`` maps to ``RuleSeverity.DANGER`` (was previously DANGER too,
    but the test guards against an accidental downgrade during refactor)."""
    from redrobin.domain.rules import RuleSeverity

    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "level_high.yaml").write_text(
        "title: High severity\n"
        "id: 00000000-0000-0000-0000-000000000015\n"
        "logsource: {product: windows}\n"
        "detection:\n"
        "  selection:\n"
        "    CommandLine|contains: 'whatever'\n"
        "  condition: selection\n"
        "level: high\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    assert rules[0].severity == RuleSeverity.DANGER


def test_sigma_title_becomes_reason(tmp_path):
    """The Sigma ``title`` should surface as the rule's ``reason`` so the
    verdict line shows something operator-meaningful."""
    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "title_test.yaml").write_text(
        "title: Mimikatz LSASS Credential Theft\n"
        "id: 00000000-0000-0000-0000-000000000016\n"
        "logsource: {product: windows}\n"
        "detection:\n"
        "  selection:\n"
        "    CommandLine|contains: 'sekurlsa'\n"
        "  condition: selection\n"
        "level: high\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rule = loader.load_all()[0]
    assert "Mimikatz LSASS" in rule.reason


def test_sigma_missing_detection_block_falls_back_to_stub(tmp_path):
    """A Sigma rule without a detection block (malformed) should NOT crash
    the loader; it should be loaded as a degraded entry so list_rules still
    reflects its presence but matches return False (no predicates)."""
    sigma_dir = tmp_path / "shared" / "sigma"
    sigma_dir.mkdir(parents=True)
    (sigma_dir / "no_detection.yaml").write_text(
        "title: Bare Sigma rule\n"
        "id: 00000000-0000-0000-0000-000000000017\n"
        "logsource: {product: windows}\n"
        "detection: {}\n"
        "level: low\n"
    )
    loader = RulesLoader(rule_dir=tmp_path)
    rules = loader.load_all()
    assert len(rules) == 1
    rule = rules[0]
    # No predicates → no match for any command.
    assert not rule.matches("anything", "any args")
    assert not rule.matches("", "")
