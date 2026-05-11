"""redrobin/infrastructure/rules_loader.py — YAML rules loader with hot-reload.

Reads rules from `~/.redrobin/rules/{shared,personal}/*.yaml` recursively.
Hot-reload via watchdog observer; callbacks are invoked when YAML/YML/JSON
files change. ``load_all`` refreshes the cached snapshot exposed by
``list_rules``, so callers can read the current rule set without re-walking
the filesystem.

The `passthrough.yaml` file is handled separately (whitelist of commands that
skip Layer B escalation). RulesLoader skips it.

Sigma support is intentionally minimal — enough to match real-world tradecraft
detection rules without depending on the upstream pySigma engine:

* ``CommandLine`` (and the alias ``cmd``) maps to the ``args`` predicate field.
* ``Image`` maps to the ``command`` predicate field via the executable basename
  (``\\mimikatz.exe`` → ``mimikatz``).
* Modifiers ``|contains``, ``|startswith``, ``|endswith`` map to the obvious
  operators; a bare field (no modifier) is an equality check.
* List values are OR within a single predicate (Sigma's documented semantics).
* Multiple selection fields AND together (also Sigma's default).
* Detection blocks beyond ``selection`` (named selections, ``condition``
  expressions) are ignored — only the ``selection`` block is interpreted.
* Other modifiers (``|all``, ``|re``, ``|cidr``, ``|base64``, etc.) are
  ignored with a warning; the rule still loads as a degraded stub.

This covers the substring/prefix/suffix cases that operator-facing tradecraft
rules actually use today. The full pySigma engine can land in a later slice if
heavier expressivity (regex, CIDR matching, named-selection conditions)
becomes a requirement.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from redrobin.domain.rules import MatchPredicate, Rule, RuleMatch, RuleSeverity

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

# Sigma field name → predicate field on the rule.
# ``Image`` is the full process image path on Sysmon/Windows; the predicate
# treats it as ``command`` (basename) since Red Robin's verdict path receives
# operator-typed commands, not full image paths.
_SIGMA_FIELD_TO_PREDICATE: dict[str, str] = {
    "commandline": "args",
    "cmd": "args",
    "image": "command",
}

# Sigma modifier → predicate operator. A field without a modifier is "equals".
_SIGMA_MODIFIER_TO_OP: dict[str, str] = {
    "contains": "contains",
    "startswith": "startswith",
    "endswith": "endswith",
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


def _parse_sigma_detection(
    path: Path, detection: Any
) -> tuple[MatchPredicate, ...]:
    """Translate a Sigma ``detection`` block into a tuple of predicates.

    Only the ``selection`` block is interpreted. Unknown field names and
    unknown modifiers are skipped with a debug log; the resulting rule loads
    as a degraded stub (empty predicates → ``Rule.matches`` is always False)
    so the operator can still see it in ``list_rules`` without being misled.
    """
    if not isinstance(detection, dict):
        return ()
    selection = detection.get("selection")
    if not isinstance(selection, dict):
        return ()

    predicates: list[MatchPredicate] = []
    for sigma_key, raw_value in selection.items():
        # Split "Field|modifier" — Sigma allows multiple chained modifiers
        # but the common case is a single one. We support the first modifier
        # and ignore extras.
        parts = str(sigma_key).split("|")
        field_name = parts[0].strip().lower()
        modifier = parts[1].strip().lower() if len(parts) > 1 else None

        predicate_field = _SIGMA_FIELD_TO_PREDICATE.get(field_name)
        if predicate_field is None:
            logger.debug(
                "%s: sigma field %r not supported by Red Robin minimal sigma engine, "
                "skipping predicate",
                path,
                sigma_key,
            )
            continue

        if modifier is None:
            op = "equals"
        elif modifier in _SIGMA_MODIFIER_TO_OP:
            op = _SIGMA_MODIFIER_TO_OP[modifier]
        else:
            logger.debug(
                "%s: sigma modifier %r on %r not supported, skipping predicate",
                path,
                modifier,
                sigma_key,
            )
            continue

        # Sigma allows scalar or list values; list = OR across values.
        if isinstance(raw_value, list):
            raw_values = [str(v) for v in raw_value]
        else:
            raw_values = [str(raw_value)]

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
            p
            for ext in _RULE_EXTENSIONS
            for p in self.rule_dir.rglob(f"*{ext}")
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
