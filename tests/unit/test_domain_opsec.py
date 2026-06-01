"""Domain OPSEC verdict types (PASS/WARN/BLOCK/ESCALATE) — spec §1.2.1, §4 Componente 4."""

from redrobin.domain.opsec import Verdict, VerdictAction


def test_verdict_pass_is_not_block():
    v = Verdict(action=VerdictAction.PASS, reason="", rule_id=None)
    assert v.is_block is False
    assert v.is_warn is False
    assert v.requires_layer_b is False


def test_verdict_block_is_block():
    v = Verdict(action=VerdictAction.BLOCK, reason="lsass dump", rule_id="mimikatz_logonpasswords")
    assert v.is_block is True
    assert v.is_warn is False
    assert v.requires_layer_b is False


def test_verdict_warn_is_warn():
    v = Verdict(action=VerdictAction.WARN, reason="noisy", rule_id="kerberoast_setspn")
    assert v.is_block is False
    assert v.is_warn is True


def test_verdict_escalate_requires_layer_b():
    v = Verdict(action=VerdictAction.ESCALATE, reason="novel pattern", rule_id=None)
    assert v.requires_layer_b is True
    assert v.is_block is False


def test_verdict_action_enum_4_values():
    assert {a.value for a in VerdictAction} == {"PASS", "WARN", "BLOCK", "ESCALATE"}
