"""Integration: load bootstrap rules from rules/shared/ + verify matching."""
from pathlib import Path

import pytest

from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.domain.opsec import VerdictAction
from redrobin.infrastructure.rules_loader import RulesLoader


REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / "rules"


@pytest.fixture
def loaded_rules():
    loader = RulesLoader(rule_dir=RULES_DIR)
    return loader.load_all()


def test_bootstrap_pack_has_minimum_12_rules(loaded_rules):
    assert len(loaded_rules) >= 12, f"got {len(loaded_rules)} rules"


def test_bootstrap_includes_canonical_rules(loaded_rules):
    rule_ids = {r.id for r in loaded_rules}
    expected = {
        "mimikatz_logonpasswords",
        "dcsync_replication",
        "vssadmin_list_shadows",
        "kerberoast_setspn",
        "domain_admin_enumeration",
        "psexec_lateral",
        "bypassuac_warn",
        "inject_lsass",
        "unmanaged_powershell",
        "winrm_enable",
        "screenshot_persistent",
        "socks_external",
        "runas_admin_creds",
        "nbtstat_arp_sweep",
    }
    missing = expected - rule_ids
    assert not missing, f"missing canonical rules: {missing}"


def test_mimikatz_logonpasswords_blocks(loaded_rules):
    engine = LayerAEngine(rules=loaded_rules, passthrough=set())
    v = engine.evaluate(command="mimikatz", args="sekurlsa::logonpasswords")
    assert v.action == VerdictAction.BLOCK
    assert v.rule_id == "mimikatz_logonpasswords"


def test_dcsync_always_blocks(loaded_rules):
    engine = LayerAEngine(rules=loaded_rules, passthrough=set())
    v = engine.evaluate(command="dcsync", args="corp.local DOMAIN\\administrator")
    assert v.action == VerdictAction.BLOCK


def test_vssadmin_in_shell_blocks(loaded_rules):
    engine = LayerAEngine(rules=loaded_rules, passthrough=set())
    v = engine.evaluate(command="shell", args="vssadmin list shadows")
    assert v.action == VerdictAction.BLOCK


def test_innocent_shell_command_does_not_match(loaded_rules):
    engine = LayerAEngine(rules=loaded_rules, passthrough=set())
    v = engine.evaluate(command="shell", args="echo hello")
    # No rule matches → ESCALATE
    assert v.action == VerdictAction.ESCALATE


def test_all_rules_load_without_error(loaded_rules):
    # Just loading is the test — confirms YAML is well-formed
    for rule in loaded_rules:
        assert rule.id
        assert rule.match.command
        assert rule.reason
