"""redrobin/tui/i18n.py — ES/EN label packs with $LANG locale detection.

Per spec §4 Componente 5: TUI labels are locale-detected; slash commands stay
in English. Falls back to EN if locale not supported.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

LABELS_DIR = Path(__file__).parent / "labels"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = {"en", "es"}


def detect_locale() -> str:
    """Return language code from $LANG (e.g., 'es' from 'es_CR.UTF-8'). Fall back to en."""
    lang = os.environ.get("LANG") or os.environ.get("LANGUAGE") or ""
    code = lang.split("_")[0].split(".")[0].lower()
    return code if code in SUPPORTED_LOCALES else DEFAULT_LOCALE


def load_labels(locale: str | None = None) -> dict:
    """Load label pack for the given (or detected) locale. Returns nested dict."""
    locale = locale or detect_locale()
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    return tomllib.loads((LABELS_DIR / f"{locale}.toml").read_text(encoding="utf-8"))


class Labels:
    """Convenience accessor: `labels.tui.working_memory` etc."""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, key: str) -> "_Section":
        return _Section(self._data.get(key, {}))


class _Section:
    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, key: str) -> str:
        return self._data.get(key, key)
