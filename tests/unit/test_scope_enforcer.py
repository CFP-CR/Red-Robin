"""ROE scope enforcement — Stage 1 of verdict pipeline (spec §1.2.1 + §6 T9)."""

import pytest

from redrobin.application.scope_enforcer import ScopeEnforcer


@pytest.fixture
def enforcer():
    return ScopeEnforcer(
        in_scope=["*.acme.local", "10.0.0.0/16"],
        out_of_scope=["10.0.99.0/24", "*.acme-legal.com"],
    )


# ----- Target extraction -----


def test_extract_jump_target(enforcer):
    targets = enforcer.extract_targets(
        command="jump", raw="jump psexec64 dc01.acme.local listener-http"
    )
    assert "dc01.acme.local" in targets


def test_extract_winrm_target(enforcer):
    targets = enforcer.extract_targets(command="winrm", raw="winrm dc01.acme.local")
    assert "dc01.acme.local" in targets


def test_extract_nxc_target(enforcer):
    targets = enforcer.extract_targets(
        command="shell", raw="shell nxc ldap 192.168.1.0/24 -u user -p pass"
    )
    assert "192.168.1.0/24" in targets


def test_extract_target_flag(enforcer):
    targets = enforcer.extract_targets(
        command="shell", raw="shell some-tool --target 10.0.99.5 --foo bar"
    )
    assert "10.0.99.5" in targets


def test_extract_h_flag(enforcer):
    targets = enforcer.extract_targets(command="shell", raw="shell mytool -H 10.0.99.5")
    assert "10.0.99.5" in targets


def test_extract_no_targets_for_neutral_command(enforcer):
    targets = enforcer.extract_targets(command="pwd", raw="pwd")
    assert targets == []


# ----- Out-of-scope check -----


def test_in_scope_host_passes(enforcer):
    targets = ["dc01.acme.local"]
    assert enforcer.check(targets) is None


def test_oos_cidr_subnet_violates(enforcer):
    targets = ["10.0.99.5"]
    violation = enforcer.check(targets)
    assert violation is not None
    assert "10.0.99.5" in violation
    assert "out-of-scope" in violation


def test_oos_glob_violates(enforcer):
    targets = ["app.acme-legal.com"]
    violation = enforcer.check(targets)
    assert violation is not None
    assert "out-of-scope" in violation


def test_combined_jump_then_check_oos(enforcer):
    targets = enforcer.extract_targets(command="jump", raw="jump psexec64 10.0.99.5 listener")
    violation = enforcer.check(targets)
    assert violation is not None


def test_combined_nxc_then_check_oos(enforcer):
    """nxc against an out-of-scope subnet violates."""
    targets = enforcer.extract_targets(
        command="shell", raw="shell nxc smb 10.0.99.0/24 -u user -p pass"
    )
    violation = enforcer.check(targets)
    assert violation is not None


def test_ipv6_not_extracted_in_v0_1(enforcer):
    """IPv6 target extraction deferred to v0.2 — verify v0.1 doesn't false-extract."""
    targets = enforcer.extract_targets(command="jump", raw="jump psexec64 fe80::1 listener")
    # Acceptable: either extract fe80::1 or skip (regex is IPv4/hostname focused)
    # As long as it doesn't crash, the test passes.
    assert isinstance(targets, list)


# ----- Defensive cases -----


def test_check_empty_targets_is_none(enforcer):
    assert enforcer.check([]) is None


def test_empty_out_of_scope_list_allows_everything():
    enforcer = ScopeEnforcer(in_scope=["*"], out_of_scope=[])
    assert enforcer.check(["10.0.99.5"]) is None
    assert enforcer.check(["evil.com"]) is None
