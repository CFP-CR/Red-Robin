"""redrobin/domain/rules.py — Layer A YAML rule schema (spec §4 Componente 4).

Each rule is loaded from `~/.redrobin/rules/{shared,personal}/*.yaml` via
`infrastructure/rules_loader.py` (Task 15) and consumed by `LayerAEngine`
(Task 16).

Two match shapes are supported:

* Native Red Robin: ``match.command`` + ``match.arg_pattern``. Backwards
  compatible with the original v0.1 rule format.
* Sigma-style predicates: ``match_predicates`` — a tuple of
  :class:`MatchPredicate` (and possibly :class:`_NeverMatchPredicate` for
  rules that referenced fields/modifiers Red Robin does not support yet)
  evaluated AND-wise against an :class:`EventContext`. This is how the Sigma
  loader expresses ``CommandLine|contains``, ``Image|endswith``, etc. while
  keeping the domain layer ignorant of Sigma syntax.

Predicate evaluation precedence:

1. If ``match_predicates`` is non-empty it is the sole criterion; the legacy
   ``RuleMatch.command`` is ignored. This lets the Sigma loader emit rules
   without binding to a single command name.
2. Otherwise the legacy ``RuleMatch`` is evaluated.

Slice 11 (v0.1.x hardening) — breaking change: ``Rule.matches`` now takes an
:class:`EventContext` instead of ``(command, args)``. The extra fields
(``integrity_level``, ``original_filename``, ``user``, ``current_directory``,
``description``) let Sigma rules constrain on Windows process metadata that
the .cna adapter surfaces. ``ParentImage`` is intentionally NOT supported in
v0.1.x — Aggressor's ``&binfo`` does not canonically expose a parent process
image; rules using it parse fine but emit a load-time WARN and produce a
:class:`_NeverMatchPredicate` so they never fire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class RuleSeverity(Enum):
    INFO = "INFO"
    WARN = "WARN"
    DANGER = "DANGER"


@dataclass(frozen=True)
class RuleMatch:
    command: str  # exact command name (e.g., "mimikatz", "shell")
    arg_pattern: str | None  # regex against arg string (case-insensitive); None = match any args


@dataclass(frozen=True)
class EventContext:
    """Context surfaced to :meth:`Rule.matches`.

    Slice 11 — extends the legacy ``(command, args)`` 2-tuple to richer
    Windows process metadata. Optional fields default to ``None``; a Sigma
    predicate that references an absent field never matches.

    ``integrity_level`` is intentionally a binary signal in v0.1.x: the .cna
    adapter derives ``"High"`` from ``binfo($bid, "user")``'s trailing ``" *"``
    suffix (canonical CS elevated indicator). Distinguishing Medium/Low/System
    would require a beacon-side BOF and is deferred to v0.2.
    """

    command: str
    args: str = ""
    integrity_level: str | None = None
    original_filename: str | None = None
    user: str | None = None
    current_directory: str | None = None
    description: str | None = None


PredicateField = Literal[
    "command",
    "args",
    "integrity_level",
    "original_filename",
    "user",
    "current_directory",
    "description",
]
PredicateOp = Literal["equals", "contains", "startswith", "endswith", "regex"]


@dataclass(frozen=True)
class MatchPredicate:
    """One Sigma-style match clause.

    ``field`` identifies one of the :class:`EventContext` attributes. The
    legacy ``command``/``args`` pair plus the Slice-11 metadata fields are
    all valid.

    ``op`` is one of ``equals``/``contains``/``startswith``/``endswith`` —
    case-insensitive per Sigma's default — or ``regex`` which is
    case-sensitive substring match via :func:`re.search` (the Sigma ``|re``
    convention; the rules engine does NOT anchor regexes).

    ``values`` is a tuple — when more than one value is provided the
    predicate is OR over the list (matches if any value matches). This
    mirrors Sigma's list-valued field semantics.
    """

    field: PredicateField
    op: PredicateOp
    values: tuple[str, ...]

    def matches(self, ctx: EventContext) -> bool:  # noqa: D401 — name matches dispatch contract
        return _eval_match_predicate(self, ctx)


@dataclass(frozen=True)
class _NeverMatchPredicate:
    """Sentinel predicate produced when a Sigma rule references something
    Red Robin can't evaluate yet (e.g. ``ParentImage`` — deferred to v0.2 —
    or modifiers like ``|cidr``/``|wide``/``|fieldref``/``|re|i``).

    Audit pass 2 #2.4 + #11 — better to load the rule with a never-match
    predicate (and emit a load-time WARN) than to silently degrade matching
    on the supported subset, which would surface as a false-PASS verdict on
    real-world tradecraft.
    """

    reason: str  # surfaced via debug logging only

    def matches(self, ctx: EventContext) -> bool:  # noqa: D401 — dispatch contract
        return False


def _eval_match_predicate(p: MatchPredicate, ctx: EventContext) -> bool:
    """Evaluate one :class:`MatchPredicate` against an :class:`EventContext`.

    A missing (``None``) ctx field never matches, regardless of operator —
    this matches Sigma's "field not present" semantics.
    """
    field_value = getattr(ctx, p.field, None)
    if field_value is None:
        return False

    if p.op == "regex":
        # Audit pass 2: Sigma's |re modifier is substring via re.search,
        # case-sensitive by default. Anchors and case-insensitivity must be
        # opted into inside the pattern itself.
        #
        # When the predicate targets ``args`` we evaluate against the
        # synthesized full command line (``command + " " + args``) — Sigma
        # writers use ``CommandLine|re`` against patterns that span the
        # binary name and its arguments, and after the verdict pipeline
        # strips the command prefix, ``args`` alone would never match such a
        # rule. Other fields (integrity_level, user, …) are scalar so the
        # synthesis only applies to the ``args`` case.
        search_target = f"{ctx.command} {field_value}" if p.field == "args" else field_value
        for pattern in p.values:
            if re.search(pattern, search_target):
                return True
        return False

    # Sigma's contains/startswith/endswith/equals default to case-insensitive.
    field_lower = field_value.lower()
    for v in p.values:
        v_lower = v.lower()
        if p.op == "equals" and field_lower == v_lower:
            return True
        if p.op == "contains" and v_lower in field_lower:
            return True
        if p.op == "startswith" and field_lower.startswith(v_lower):
            return True
        if p.op == "endswith" and field_lower.endswith(v_lower):
            return True
    return False


@dataclass(frozen=True)
class Rule:
    id: str
    match: RuleMatch
    severity: RuleSeverity
    reason: str
    suggestions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    # Sigma-style predicates evaluated AND-wise. When non-empty, the legacy
    # ``RuleMatch`` is bypassed entirely. An empty tuple here preserves the
    # original v0.1 single-command matching path.
    match_predicates: tuple[MatchPredicate | _NeverMatchPredicate, ...] = field(
        default_factory=tuple
    )

    def matches(self, ctx: EventContext) -> bool:
        if self.match_predicates:
            # Audit pass 3 #G: dispatch polymorphically. _NeverMatchPredicate
            # does not have an ``op`` field, so a switch-on-op would
            # AttributeError. The isinstance short-circuit also lets us bail
            # the whole rule the moment we encounter a sentinel — there is
            # no point evaluating the remaining predicates AND-wise.
            for p in self.match_predicates:
                if isinstance(p, _NeverMatchPredicate):
                    return False
                if not p.matches(ctx):
                    return False
            return True

        if ctx.command != self.match.command:
            return False
        if self.match.arg_pattern is None:
            return True
        return re.search(self.match.arg_pattern, ctx.args, re.IGNORECASE) is not None
