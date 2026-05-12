"""Slice 11: extended Sigma matcher."""
from __future__ import annotations

import pytest

from redrobin.infrastructure.rules_loader import RulesLoader
from redrobin.domain.rules import EventContext


@pytest.fixture
def sigma_rule_mimikatz_high_integrity(tmp_path):
    """Real-shape Sigma rule using IntegrityLevel + CommandLine|contains."""
    (tmp_path / "shared" / "sigma").mkdir(parents=True)
    r = tmp_path / "shared" / "sigma" / "mimikatz-high-integrity.yaml"
    r.write_text("""
title: Mimikatz invoked at High integrity
id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
logsource:
  product: windows
detection:
  selection:
    IntegrityLevel: High
    CommandLine|contains: 'sekurlsa::logonpasswords'
  condition: selection
level: high
""")
    return tmp_path


def test_sigma_rule_matches_high_integrity(sigma_rule_mimikatz_high_integrity):
    loader = RulesLoader(rule_dir=sigma_rule_mimikatz_high_integrity)
    loader.load_all()
    rules = loader.list_rules()
    assert len(rules) == 1
    rule = rules[0]
    ctx_match = EventContext(
        command="mimikatz",
        args='"sekurlsa::logonpasswords"',
        integrity_level="High",
    )
    assert rule.matches(ctx_match)


def test_sigma_rule_does_not_match_non_high_integrity(sigma_rule_mimikatz_high_integrity):
    """v0.1.x integrity_level is a binary High/None signal."""
    loader = RulesLoader(rule_dir=sigma_rule_mimikatz_high_integrity)
    loader.load_all()
    rule = loader.list_rules()[0]
    ctx_no_match = EventContext(
        command="mimikatz",
        args='"sekurlsa::logonpasswords"',
        integrity_level=None,  # not High -> must not match
    )
    assert not rule.matches(ctx_no_match)


def test_sigma_regex_modifier_substring_via_re_search(tmp_path):
    """Sigma |re modifier uses re.search (substring), case-sensitive."""
    (tmp_path / "shared" / "sigma").mkdir(parents=True)
    r = tmp_path / "shared" / "sigma" / "regex.yaml"
    r.write_text("""
title: regex test (substring, no anchors)
id: 22222222-2222-2222-2222-222222222222
logsource: {product: windows}
detection:
  selection:
    CommandLine|re: 'net\\s+user\\s+\\w+\\s+/add'
  condition: selection
level: medium
""")
    loader = RulesLoader(rule_dir=tmp_path)
    loader.load_all()
    rule = loader.list_rules()[0]
    assert rule.matches(EventContext(command="net", args="user attacker /add /domain"))
    assert rule.matches(EventContext(command="cmd", args="prefix net user attacker /add suffix"))
    assert not rule.matches(EventContext(command="net", args="user list"))


def test_sigma_description_field_supported(tmp_path):
    """Description field is in the supported predicate set."""
    (tmp_path / "shared" / "sigma").mkdir(parents=True)
    r = tmp_path / "shared" / "sigma" / "description.yaml"
    r.write_text("""
title: description test
id: 33333333-3333-3333-3333-333333333333
logsource: {product: windows}
detection:
  selection:
    Description|contains: '7-Zip'
    CommandLine|contains: '-p'
  condition: selection
level: medium
""")
    loader = RulesLoader(rule_dir=tmp_path)
    loader.load_all()
    rule = loader.list_rules()[0]
    assert rule.matches(EventContext(command="7z", args="-p somepass archive.7z files/", description="7-Zip Console"))


def test_unsupported_field_emits_warn_and_rule_is_unmatchable(tmp_path, caplog):
    """ParentImage is deferred to v0.2 — rules using it load + WARN + produce _NeverMatchPredicate."""
    (tmp_path / "shared" / "sigma").mkdir(parents=True)
    rule_path = tmp_path / "shared" / "sigma" / "parent.yaml"
    rule_path.write_text("""
title: parent image (unsupported in v0.1.x)
id: 44444444-4444-4444-4444-444444444444
logsource: {product: windows}
detection:
  selection:
    ParentImage|endswith: '\\\\powershell.exe'
  condition: selection
level: high
""")
    import logging
    with caplog.at_level(logging.WARNING):
        loader = RulesLoader(rule_dir=tmp_path)
        loader.load_all()
    assert any("unsupported" in rec.getMessage().lower() and "ParentImage" in rec.getMessage()
               for rec in caplog.records)
    rules = loader.list_rules()
    assert len(rules) == 1
    # _NeverMatchPredicate makes the rule unmatchable for ANY context
    assert not rules[0].matches(EventContext(command="anything", args="anything"))


def test_sigma_base64offset_three_or_predicates(tmp_path):
    """base64offset must produce three OR-combined base64 variants per pySigma.

    Verbatim port of SigmaBase64OffsetModifier.modify (audit pass 2 #E):
      start_offsets = (0, 2, 3)
      end_offsets   = (None, -3, -2)
      padding byte = SPACE b" ", NOT NUL.
    """
    import base64
    (tmp_path / "shared" / "sigma").mkdir(parents=True)
    r = tmp_path / "shared" / "sigma" / "b64.yaml"
    r.write_text("""
title: base64 encoded download
id: 55555555-5555-5555-5555-555555555555
logsource: {product: windows}
detection:
  selection:
    CommandLine|base64offset|contains: 'IEX(New-Object Net.WebClient)'
  condition: selection
level: high
""")
    loader = RulesLoader(rule_dir=tmp_path)
    loader.load_all()
    rule = loader.list_rules()[0]
    plaintext_bytes = b"IEX(New-Object Net.WebClient)"
    start_offsets = (0, 2, 3)
    end_offsets = (None, -3, -2)
    for i in (0, 1, 2):
        padded = (b" " * i) + plaintext_bytes
        full = base64.b64encode(padded).decode()
        static_middle = full[start_offsets[i] : end_offsets[(len(plaintext_bytes) + i) % 3]]
        assert rule.matches(EventContext(
            command="powershell",
            args=f"-EncodedCommand prefix{static_middle}suffix",
        )), f"shift={i} static_middle={static_middle!r} should match the rule"
