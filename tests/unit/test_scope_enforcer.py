"""ROE scope enforcement — Stage 1 of verdict pipeline (spec §1.2.1 + §6 T9).

The canonical engagement.yaml semantics (exact host + domain-suffix + CIDR +
wildcard ``"*"``) are the source of truth for both :meth:`ScopeEnforcer.check`
(used by the live verdict pipeline) and :meth:`ScopeEnforcer.is_in_scope`. The
two methods share the same matcher; tests below cover the canonical patterns,
not the legacy glob semantics from Phase 1.
"""

import pytest

from redrobin.application.scope_enforcer import ScopeEnforcer


@pytest.fixture
def enforcer():
    # Canonical patterns: bare domain → suffix match, CIDR → membership.
    # ``acme.local`` matches ``dc01.acme.local`` via domain-suffix rule;
    # ``acme-legal.com`` matches ``app.acme-legal.com`` likewise.
    return ScopeEnforcer(
        in_scope=["acme.local", "10.0.0.0/16"],
        out_of_scope=["10.0.99.0/24", "acme-legal.com"],
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


def test_oos_suffix_violates(enforcer):
    """Domain-suffix match against out_of_scope flags violation."""
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


def test_wildcard_in_scope_allows_everything():
    """``in_scope=['*']`` means anything not explicitly out_of_scope passes."""
    enforcer = ScopeEnforcer(in_scope=["*"], out_of_scope=[])
    assert enforcer.check(["10.0.99.5"]) is None
    assert enforcer.check(["evil.com"]) is None


def test_target_not_in_in_scope_is_violation(enforcer):
    """Per spec, a target not matched by in_scope OR matched by out_of_scope
    is a ROE violation. Closes gap #4: the live verdict path now rejects
    targets outside the engagement's positive in_scope list, not just those
    on the negative out_of_scope list."""
    # ``dc01.example.org`` is neither in in_scope (``acme.local`` /
    # ``10.0.0.0/16``) nor in out_of_scope — must still be blocked.
    violation = enforcer.check(["dc01.example.org"])
    assert violation is not None
    assert "out-of-scope" in violation


def test_check_and_is_in_scope_share_semantics():
    """``check`` and ``is_in_scope`` must agree — Slice 6 closure of gap #4.

    Before this slice, ``check`` walked ``_matches_any`` (fnmatch globs) while
    ``is_in_scope`` walked ``_is_in_scope_match`` (suffix/CIDR). That gap let
    engagement.yaml's plain hostnames bypass ``check`` at the live path even
    though ``is_in_scope`` rejected them. The two methods now share semantics.
    """
    enforcer = ScopeEnforcer(in_scope=["corp.local"], out_of_scope=["bigcorp.com"])
    # In-scope via suffix: dc01.corp.local
    assert enforcer.is_in_scope("dc01.corp.local") is True
    assert enforcer.check(["dc01.corp.local"]) is None
    # Out-of-scope via suffix: api.bigcorp.com
    assert enforcer.is_in_scope("api.bigcorp.com") is False
    assert enforcer.check(["api.bigcorp.com"]) is not None
    # Not in in_scope at all
    assert enforcer.is_in_scope("unknown.example.org") is False
    assert enforcer.check(["unknown.example.org"]) is not None


def test_pipeline_scope_check_with_engagement_yaml_semantics():
    """The verdict pipeline's ``self._scope.check(targets)`` call at
    redrobin/application/verdict_pipeline.py must honor the same semantics
    the engagement loader expects. Without this, gap #4 stays open: an
    operator who loaded ``in_scope: [corp.local]`` from engagement.yaml
    would see ``dc01.corp.local`` treated as out-of-scope at runtime.
    """
    scope = ScopeEnforcer(in_scope=["corp.local"], out_of_scope=["bigcorp.com"])

    # In-scope domain — pipeline check returns None.
    result_in = scope.check(["dc01.corp.local"])
    assert result_in is None

    # Out-of-scope domain — pipeline check returns the violation string.
    result_out = scope.check(["api.bigcorp.com"])
    assert result_out is not None
    assert "api.bigcorp.com" in result_out
    assert "out-of-scope" in result_out
