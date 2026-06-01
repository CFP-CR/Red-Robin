"""redrobin/application/scope_enforcer.py — Stage 1 ROE enforcement.

Per spec §1.2.1 verdict pipeline + §6 T9 threat mitigation. Extracts target
hosts/IPs from common Beacon command patterns and validates them against the
engagement.yaml-loaded scope (exact host + domain-suffix + CIDR + wildcard).

Coverage (v0.1):
  - jump <method> <target>
  - psexec <target> ...
  - wmi <target> ...
  - winrm <target> ...
  - link <target>
  - shell/run with nxc / crackmapexec / -H / --host / --target flags

Scope matching (single source of truth — shared by ``check`` and ``is_in_scope``):
  - ``"*"`` wildcard matches anything
  - exact hostname/IP match
  - domain-suffix match (``corp.local`` matches ``dc01.corp.local``)
  - CIDR membership (``10.0.0.0/24`` matches ``10.0.0.5``); CIDR overlap when
    the target is itself a network (e.g., nxc against ``192.168.1.0/24``)
  - ``out_of_scope`` wins on conflict

Out-of-scope (deliberately):
  - IPv6 target extraction (v0.2 — adds ipv6_address pattern)
  - DNS resolution (out of v0.1 scope; operator may write hostname OR IP)
  - fnmatch globs — Phase 1's ``_matches_any`` used fnmatch but the spec
    never anchored to globs; engagement.yaml uses bare hostnames + CIDRs. The
    glob matcher was dropped in Slice 6 to close gap #4 (live verdict path).
"""

from __future__ import annotations

import ipaddress
import re


# Extract target hosts/IPs from common Beacon command patterns.
# Note: patterns are intentionally loose — overmatching is fine (false positive
# scope violations are operator-recoverable; false negatives could leak ROE).
_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bjump\s+\S+\s+(?P<target>\S+)\b"),
    re.compile(r"\bpsexec\s+(?P<target>\S+)\b"),
    re.compile(r"\bwmi\s+(?P<target>\S+)\b"),
    re.compile(r"\bwinrm\s+(?P<target>\S+)\b"),
    re.compile(r"\blink\s+(?P<target>\S+)\b"),
    re.compile(r"\bnxc\s+\w+\s+(?P<target>\S+)\b"),
    re.compile(r"\bcrackmapexec\s+\w+\s+(?P<target>\S+)\b"),
    re.compile(r"(?:--target|--host|-H)[\s=](?P<target>\S+)"),
)


class ScopeEnforcer:
    def __init__(self, in_scope: list[str], out_of_scope: list[str]):
        self.in_scope = in_scope
        self.out_of_scope = out_of_scope

    def extract_targets(self, *, command: str, raw: str) -> list[str]:
        """Return list of host/IP/CIDR strings extracted from `raw`."""
        targets: list[str] = []
        for pat in _TARGET_PATTERNS:
            for m in pat.finditer(raw):
                targets.append(m.group("target"))
        return targets

    def check(self, targets: list[str]) -> str | None:
        """Return violation reason for the first target not in scope; None otherwise.

        Slice 6 (gap #4 closure): now uses :meth:`is_in_scope` semantics —
        a target is in scope iff it is not in ``out_of_scope`` AND it matches
        ``in_scope`` (or in_scope contains the ``"*"`` wildcard). Targets
        outside the positive in_scope list are rejected, not just those
        explicitly listed in out_of_scope.
        """
        for target in targets:
            if not self.is_in_scope(target):
                return f"ROE violation: target {target!r} is out-of-scope"
        return None

    def is_in_scope(self, target: str) -> bool:
        """Return True iff ``target`` is in scope per engagement.yaml semantics.

        Slice 6 — single source of truth for scope evaluation, used by both
        the live verdict pipeline (via :meth:`check`) and the engagement
        loader's lifespan wiring.

        Match rules (out_of_scope wins on conflict):
          - ``"*"`` wildcard matches anything
          - exact hostname/IP match
          - domain-suffix match (``corp.local`` matches ``dc01.corp.local``)
          - CIDR membership (``10.0.0.0/24`` matches ``10.0.0.5``); CIDR overlap
            when the target is itself a network (e.g., ``192.168.1.0/24``)
        """
        if self._matches_any(target, self.out_of_scope):
            return False
        if "*" in self.in_scope:
            return True
        return self._matches_any(target, self.in_scope)

    @staticmethod
    def _matches_any(target: str, patterns: list[str]) -> bool:
        """Canonical matcher — exact, suffix, CIDR membership, CIDR overlap.

        Single matcher shared by ``check`` and ``is_in_scope``. Replaces the
        Phase 1 fnmatch-based glob matcher (which was never part of the spec)
        and the parallel ``_is_in_scope_match`` helper (which lacked CIDR
        overlap). Closes gap #4 by ensuring the live verdict path enforces
        engagement.yaml-loaded scope per spec semantics.
        """
        for pat in patterns:
            if pat == "*":
                return True
            if pat == target:
                return True
            if "/" in pat:
                try:
                    net = ipaddress.ip_network(pat, strict=False)
                except ValueError:
                    net = None
                if net is not None:
                    # Target is a bare IP — membership.
                    try:
                        if ipaddress.ip_address(target) in net:
                            return True
                    except ValueError:
                        # Target is itself a CIDR — check overlap. Safety
                        # critical: if out_of_scope=10.0.0.0/16 and target
                        # is 10.0.99.0/24, the subnet relationship must
                        # still trigger a block.
                        try:
                            target_net = ipaddress.ip_network(target, strict=False)
                        except ValueError:
                            target_net = None
                        if isinstance(net, ipaddress.IPv4Network) and isinstance(
                            target_net, ipaddress.IPv4Network
                        ):
                            if target_net.subnet_of(net) or net.subnet_of(target_net):
                                return True
                        elif isinstance(net, ipaddress.IPv6Network) and isinstance(
                            target_net, ipaddress.IPv6Network
                        ):
                            if target_net.subnet_of(net) or net.subnet_of(target_net):
                                return True
            # Domain-suffix match (canonical engagement.yaml semantics).
            if target.endswith("." + pat):
                return True
        return False
