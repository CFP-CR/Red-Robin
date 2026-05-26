"""redrobin/infrastructure/rules_loader.py — YAML rules loader with hot-reload.

Reads rules from `~/.redrobin/rules/{shared,personal}/*.yaml` recursively.
Hot-reload via watchdog observer; callbacks are invoked when YAML/YML/JSON
files change. ``load_all`` refreshes the cached snapshot exposed by
``list_rules``, so callers can read the current rule set without re-walking
the filesystem.

The `passthrough.yaml` file is handled separately (whitelist of commands that
skip Layer B escalation). RulesLoader skips it.

Sigma support — Slice 11 (v0.1.x hardening) extends the original minimal
subset to cover SigmaHQ's ``windows/process_creation/`` rule set:

* Fields supported: ``CommandLine`` (alias ``cmd``) → ``args``; ``Image`` →
  ``command`` (basename after stripping path and ``.exe``);
  ``IntegrityLevel``, ``OriginalFileName``, ``User``, ``CurrentDirectory``,
  ``Description`` → identically named :class:`EventContext` fields.
* Modifiers: ``|contains``/``|startswith``/``|endswith`` keep Sigma's
  default case-insensitive comparison; ``|re`` compiles to a ``regex``
  predicate evaluated via :func:`re.search` (substring, case-sensitive
  per Sigma spec); ``|base64offset|contains`` expands at parse time into
  three OR-combined ``contains`` predicates using pySigma's verbatim
  algorithm (audit pass 2 #E — SPACE padding, ``start=(0,2,3)``,
  ``end=(None,-3,-2)``).
* Unsupported fields (``ParentImage`` — deferred to v0.2 since Aggressor
  ``&binfo`` does not canonically expose a parent process image —
  ``EventID``, ``ProcessId``, etc.) and unsupported modifier chains
  (``|cidr``, ``|wide``, ``|fieldref``, ``|re|i``) emit a WARN log at
  load time and produce a :class:`_NeverMatchPredicate`. The rule still
  loads as a valid :class:`Rule` so ``list_rules()`` reflects its
  presence, but it can never match — better than a silent false-PASS.
* List values are OR within a single predicate (Sigma's documented
  semantics). Multiple selection fields AND together (also Sigma's
  default). Detection blocks beyond ``selection`` (named selections,
  ``condition`` expressions) are ignored — only ``selection`` is
  interpreted.

Full pySigma can land in a later slice when CIDR / named-selection
condition expressions become a requirement.
"""

from __future__ import annotations

import logging
from base64 import b64encode
from pathlib import Path
from typing import Any, Callable

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from redrobin.domain.rules import (
    MatchPredicate,
    Rule,
    RuleMatch,
    RuleSeverity,
    _NeverMatchPredicate,
)

logger = logging.getLogger(__name__)


class RulesLoadError(Exception):
    """Raised when a YAML rule file is malformed."""


_RULE_EXTENSIONS = (".yaml", ".yml", ".json")
_SIGMA_LEVEL_TO_SEVERITY = {
    "informational": RuleSeverity.INFO,
    "low": RuleSeverity.INFO,
    "medium": RuleSeverity.WARN,
    "high": RuleSeverity.DANGER,
    "critical": RuleSeverity.DANGER,
}

# Sigma field name (lowercased) → predicate field on the rule. ``Image`` is
# the full process image path on Sysmon/Windows; the predicate treats it as
# ``command`` (basename) since Red Robin's verdict path receives
# operator-typed commands, not full image paths.
_SIGMA_FIELD_TO_PREDICATE: dict[str, str] = {
    "commandline": "args",
    "cmd": "args",
    "image": "command",
    "integritylevel": "integrity_level",
    "originalfilename": "original_filename",
    "user": "user",
    "currentdirectory": "current_directory",
    "description": "description",
}

# Sigma modifier → predicate operator. A field without a modifier is "equals".
_SIGMA_MODIFIER_TO_OP: dict[str, str] = {
    "contains": "contains",
    "startswith": "startswith",
    "endswith": "endswith",
    "re": "regex",
}


def _coerce_image_value_to_basename(value: str) -> str:
    """``\\path\\to\\mimikatz.exe`` → ``mimikatz``.

    Sigma's ``Image|endswith`` typically carries a path suffix like
    ``\\mimikatz.exe`` to anchor on the executable name. Red Robin's verdict
    path doesn't see the full process image; the operator types the bare
    command name. So we extract the executable basename without extension and
    convert the ``endswith`` constraint to an ``equals`` on ``command``.
    """
    cleaned = value.replace("/", "\\").rsplit("\\", 1)[-1]
    if cleaned.lower().endswith(".exe"):
        cleaned = cleaned[:-4]
    return cleaned


def _expand_base64offset(value: str) -> tuple[str, str, str]:
    """Port of pySigma ``SigmaBase64OffsetModifier.modify`` (audit pass 2 #E).

    Returns three OR-combined static-middle base64 strings — one per byte
    shift — that survive being embedded into a longer base64-encoded
    command line. Constants are verbatim from pySigma:

    * ``start_offsets = (0, 2, 3)``
    * ``end_offsets   = (None, -3, -2)``
    * padding byte is **SPACE** ``b" "``, NOT NUL. Wrong padding silently
      mis-fires on real-world traffic because the surviving middle bytes
      differ.
    """
    value_bytes = value.encode()
    start_offsets = (0, 2, 3)
    end_offsets: tuple[int | None, int, int] = (None, -3, -2)
    return tuple(  # type: ignore[return-value]
        b64encode(i * b" " + value_bytes)[
            start_offsets[i] : end_offsets[(len(value_bytes) + i) % 3]
        ].decode()
        for i in range(3)
    )


def _parse_sigma_detection(
    path: Path, detection: Any
) -> tuple[MatchPredicate | _NeverMatchPredicate, ...]:
    """Translate a Sigma ``detection`` block into a tuple of predicates.

    Only the ``selection`` block is interpreted. Unknown fields and
    unknown modifier chains produce a :class:`_NeverMatchPredicate` and
    emit a WARN — the rule still loads (and is visible in ``list_rules``)
    but never matches. Better than a silent false-PASS.
    """
    if not isinstance(detection, dict):
        return ()
    selection = detection.get("selection")
    if not isinstance(selection, dict):
        return ()

    predicates: list[MatchPredicate | _NeverMatchPredicate] = []
    for sigma_key, raw_value in selection.items():
        # Split "Field|modifier|modifier..." — Sigma allows chained modifiers.
        parts = [p.strip() for p in str(sigma_key).split("|")]
        field_name_raw = parts[0]
        field_name = field_name_raw.lower()
        modifiers = [m.lower() for m in parts[1:]]

        predicate_field = _SIGMA_FIELD_TO_PREDICATE.get(field_name)
        if predicate_field is None:
            logger.warning(
                "%s: unsupported Sigma field %r — rule loaded but will never match",
                path,
                field_name_raw,
            )
            predicates.append(_NeverMatchPredicate(reason=f"unsupported field {field_name_raw}"))
            continue

        # Sigma allows scalar or list values; list = OR across values.
        if isinstance(raw_value, list):
            raw_values = [str(v) for v in raw_value]
        else:
            raw_values = [str(raw_value)]

        # Special-case the |base64offset|contains chain BEFORE the generic
        # single-modifier path. The modifier chain expands one source value
        # into THREE OR-combined `contains` predicates per pySigma.
        if modifiers == ["base64offset", "contains"]:
            expanded: list[str] = []
            for v in raw_values:
                expanded.extend(_expand_base64offset(v))
            predicates.append(
                MatchPredicate(
                    field=predicate_field,  # type: ignore[arg-type]
                    op="contains",
                    values=tuple(expanded),
                )
            )
            continue

        # Single-modifier path (the common case). Chains beyond a single
        # modifier are not supported in v0.1.x (the |all/|wide/|cidr/|re|i
        # combinations all live behind a never-match sentinel).
        if len(modifiers) == 0:
            op = "equals"
        elif len(modifiers) == 1 and modifiers[0] in _SIGMA_MODIFIER_TO_OP:
            op = _SIGMA_MODIFIER_TO_OP[modifiers[0]]
        else:
            logger.warning(
                "%s: unsupported Sigma modifier chain %r on field %r — rule "
                "loaded but will never match",
                path,
                "|".join(modifiers),
                field_name_raw,
            )
            predicates.append(
                _NeverMatchPredicate(
                    reason=f"unsupported modifier chain |{'|'.join(modifiers)} on {field_name_raw}"
                )
            )
            continue

        # Image fields contain a path; collapse to bare executable name and
        # demote the operator from endswith → equals on ``command``.
        if field_name == "image":
            raw_values = [_coerce_image_value_to_basename(v) for v in raw_values]
            op = "equals"

        predicates.append(
            MatchPredicate(
                field=predicate_field,  # type: ignore[arg-type]
                op=op,  # type: ignore[arg-type]
                values=tuple(raw_values),
            )
        )

    return tuple(predicates)


class RulesLoader:
    def __init__(self, rule_dir: Path):
        self.rule_dir = Path(rule_dir)
        self._observer: Observer | None = None
        self._on_reload_callbacks: list[Callable[[list[Rule]], None]] = []
        self._rules: list[Rule] = []

    @property
    def hot_reload_active(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def load_all(self) -> list[Rule]:
        """Load all YAML rules from rule_dir (recursive), refresh the cached
        snapshot, and return the new list sorted by path."""
        rules: list[Rule] = []
        for rule_path in sorted(
            p for ext in _RULE_EXTENSIONS for p in self.rule_dir.rglob(f"*{ext}")
        ):
            if rule_path.name == "passthrough.yaml":
                continue  # passthrough whitelist is handled separately
            rules.append(self._load_one(rule_path))
        self._rules = rules
        return rules

    def list_rules(self) -> list[Rule]:
        """Return the current cached snapshot of rules.

        The snapshot is populated by ``load_all`` and refreshed by the
        watchdog-driven hot-reload handler. Returns a defensive copy so
        callers cannot mutate the internal list.
        """
        return list(self._rules)

    def _load_one(self, path: Path) -> Rule:
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            raise RulesLoadError(f"{path}: YAML parse error: {e}") from e

        if not isinstance(data, dict):
            raise RulesLoadError(f"{path}: expected top-level mapping")

        # Sigma-format rule (shared/sigma/*.yaml): has `detection` + `level`
        # rather than the Red Robin native `match` + `severity`. Convert into
        # a stub Rule so list_rules() reflects its presence; full Sigma engine
        # support lands in a later slice.
        if "detection" in data and "match" not in data:
            return self._load_sigma(path, data)

        try:
            severity = RuleSeverity[data["severity"]]
        except KeyError as e:
            raise RulesLoadError(
                f"{path}: missing or unknown severity {data.get('severity')!r}"
            ) from e

        try:
            return Rule(
                id=data["id"],
                match=RuleMatch(
                    command=data["match"]["command"],
                    arg_pattern=data["match"].get("arg_pattern"),
                ),
                severity=severity,
                reason=data["reason"],
                suggestions=tuple(data.get("suggestions", [])),
                sources=tuple(data.get("sources", [])),
            )
        except KeyError as e:
            raise RulesLoadError(f"{path}: missing required field {e}") from e

    @staticmethod
    def _load_sigma(path: Path, data: dict[str, object]) -> Rule:
        try:
            rule_id = str(data["id"])
        except KeyError as e:
            raise RulesLoadError(f"{path}: sigma rule missing required field {e}") from e

        level = str(data.get("level", "medium")).lower()
        severity = _SIGMA_LEVEL_TO_SEVERITY.get(level, RuleSeverity.WARN)

        title = str(data.get("title", path.stem))

        # The native ``RuleMatch.command`` is unused when ``match_predicates``
        # is populated, but the dataclass field is required. Use the file stem
        # as a stable placeholder so the legacy-path equality check (which
        # never runs on a Sigma rule) is at least well-defined.
        legacy_match = RuleMatch(command=path.stem, arg_pattern=None)

        predicates = _parse_sigma_detection(path, data.get("detection"))

        return Rule(
            id=rule_id,
            match=legacy_match,
            severity=severity,
            reason=title,
            suggestions=(),
            sources=(f"sigma:{path.name}",),
            match_predicates=predicates,
        )

    def start_hot_reload(self, callback: Callable[[list[Rule]], None]) -> None:
        """Register a callback invoked whenever a YAML file under rule_dir changes."""
        self._on_reload_callbacks.append(callback)
        if self._observer is None:
            handler = _ReloadHandler(self._on_reload_callbacks, self)
            self._observer = Observer()
            self._observer.schedule(handler, str(self.rule_dir), recursive=True)
            self._observer.start()
            logger.info("rules hot-reload started: dir=%s", self.rule_dir)

    def stop_hot_reload(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None


class _ReloadHandler(FileSystemEventHandler):
    def __init__(
        self,
        callbacks: list[Callable[[list[Rule]], None]],
        loader: RulesLoader,
    ):
        self._callbacks = callbacks
        self._loader = loader

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if not str(event.src_path).endswith(_RULE_EXTENSIONS):
            return
        try:
            new_rules = self._loader.load_all()
        except RulesLoadError:
            logger.exception("hot-reload skipped due to malformed YAML")
            return
        for cb in self._callbacks:
            cb(new_rules)
