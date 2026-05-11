"""redrobin/infrastructure/rules_loader.py — YAML rules loader with hot-reload.

Reads rules from `~/.redrobin/rules/{shared,personal}/*.yaml` recursively.
Hot-reload via watchdog observer; callbacks are invoked when YAML files change.

The `passthrough.yaml` file is handled separately (whitelist of commands that
skip Layer B escalation). RulesLoader skips it.
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


class RulesLoader:
    def __init__(self, rule_dir: Path):
        self.rule_dir = Path(rule_dir)
        self._observer: Observer | None = None
        self._on_reload_callbacks: list[Callable[[list[Rule]], None]] = []

    def load_all(self) -> list[Rule]:
        """Load all YAML rules from rule_dir (recursive). Returns sorted by path."""
        rules: list[Rule] = []
        for yaml_path in sorted(self.rule_dir.rglob("*.yaml")):
            if yaml_path.name == "passthrough.yaml":
                continue  # passthrough whitelist is handled separately
            rules.append(self._load_one(yaml_path))
        return rules

    def _load_one(self, path: Path) -> Rule:
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            raise RulesLoadError(f"{path}: YAML parse error: {e}") from e

        if not isinstance(data, dict):
            raise RulesLoadError(f"{path}: expected top-level mapping")

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

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.src_path.endswith(".yaml"):
            return
        try:
            new_rules = self._loader.load_all()
        except RulesLoadError:
            logger.exception("hot-reload skipped due to malformed YAML")
            return
        for cb in self._callbacks:
            cb(new_rules)
