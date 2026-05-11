"""redrobin/infrastructure/rules_loader.py — YAML rules loader with hot-reload.

Reads rules from `~/.redrobin/rules/{shared,personal}/*.yaml` recursively.
Hot-reload via watchdog observer; callbacks are invoked when YAML/YML/JSON
files change. ``load_all`` refreshes the cached snapshot exposed by
``list_rules``, so callers can read the current rule set without re-walking
the filesystem.

The `passthrough.yaml` file is handled separately (whitelist of commands that
skip Layer B escalation). RulesLoader skips it.

Sigma-format rules (``shared/sigma/*.yaml``) are loaded as stub ``Rule``
objects so they appear in ``list_rules``; the full Sigma evaluation engine
lands in a later Phase 2 slice.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from redrobin.domain.rules import Rule, RuleMatch, RuleSeverity

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
        # Stub command pulled from the file stem so the Rule is well-formed;
        # Sigma detection logic is wired in by the Layer A Sigma engine task.
        return Rule(
            id=rule_id,
            match=RuleMatch(command=path.stem, arg_pattern=None),
            severity=severity,
            reason=title,
            suggestions=(),
            sources=(f"sigma:{path.name}",),
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
