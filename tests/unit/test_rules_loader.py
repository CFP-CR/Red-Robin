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
