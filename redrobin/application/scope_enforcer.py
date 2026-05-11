"""redrobin/application/scope_enforcer.py — Stage 1 ROE enforcement.

Per spec §1.2.1 verdict pipeline + §6 T9 threat mitigation. Extracts target
hosts/IPs from common Beacon command patterns and validates them against the
engagement's out_of_scope list (glob + CIDR).

Coverage (v0.1):
  - jump <method> <target>
  - psexec <target> ...
  - wmi <target> ...
  - winrm <target> ...
  - link <target>
  - shell/run with nxc / crackmapexec / -H / --host / --target flags

Out-of-scope:
  - IPv6 target extraction (v0.2 — adds ipv6_address pattern)
  - DNS resolution (out of v0.1 scope; operator may write hostname OR IP)
"""

from __future__ import annotations

import fnmatch
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
        """Return violation reason if any target matches out_of_scope; None otherwise."""
        for target in targets:
            if self._matches_any(target, self.out_of_scope):
                return f"ROE violation: target {target!r} is out-of-scope"
        return None

    def is_in_scope(self, target: str) -> bool:
        """Return True iff ``target`` is in scope per engagement.yaml semantics.

        Slice 6 — engagement-aware scope check used by the lifespan to validate
        operator-supplied targets against the loaded engagement. Distinct from
        :meth:`check` (which is glob-based and walks ``out_of_scope`` only):
        ``is_in_scope`` enforces the full positive+negative ROE per spec.

        Match rules (out_of_scope wins on conflict):
          - ``"*"`` wildcard matches anything
          - exact hostname match
          - domain suffix match (``corp.local`` matches ``dc01.corp.local``)
          - CIDR membership (``10.0.0.0/24`` matches ``10.0.0.5``)
        """
        if self._is_in_scope_match(target, self.out_of_scope):
            return False
        return self._is_in_scope_match(target, self.in_scope) or "*" in self.in_scope

    @staticmethod
    def _is_in_scope_match(target: str, patterns: list[str]) -> bool:
        for pat in patterns:
            if pat == "*":
                return True
            if pat == target:
                return True
            if "/" in pat:
                try:
                    net = ipaddress.ip_network(pat, strict=False)
                    try:
                        if ipaddress.ip_address(target) in net:
                            return True
                    except ValueError:
                        continue
                except ValueError:
                    pass
            if target.endswith("." + pat):
                return True
        return False

    @staticmethod
    def _matches_any(target: str, patterns: list[str]) -> bool:
        for p in patterns:
            # CIDR?
            if "/" in p:
                try:
                    net = ipaddress.ip_network(p, strict=False)
                    # If target is an IP, check membership
                    try:
                        if ipaddress.ip_address(target) in net:
                            return True
                    except ValueError:
                        # target is not a bare IP (e.g., hostname or CIDR itself);
                        # try parsing target as a network and check overlap
                        try:
                            target_net = ipaddress.ip_network(target, strict=False)
                        except ValueError:
                            target_net = None
                        # subnet_of requires matching network classes (v4 vs v6)
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
                except ValueError:
                    pass  # not a valid CIDR; fall through to glob
            # Glob match (hostname patterns)
            if fnmatch.fnmatch(target.lower(), p.lower()):
                return True
        return False
