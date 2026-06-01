"""Slice 9: repos are single-mode (sync sqlite3 via Path).

Lock-in tests guarding the consolidation. After Slice 9, ``EpisodicRepo``,
``LongtermRepo`` and ``ContextSummaryRepo`` all expose ONE constructor
signature ``__init__(self, path)`` — no SQLAlchemy ``AsyncEngine`` branch.
"""

from __future__ import annotations

import inspect

import pytest

from redrobin.infrastructure.persistence.context_summary_repo import (
    ContextSummaryRepo,
)
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


def test_episodic_repo_constructor_takes_path_only() -> None:
    sig = inspect.signature(EpisodicRepo.__init__)
    params = list(sig.parameters.values())[1:]  # skip self
    assert len(params) == 1
    assert params[0].name == "path"


def test_longterm_repo_constructor_takes_path_only() -> None:
    sig = inspect.signature(LongtermRepo.__init__)
    params = list(sig.parameters.values())[1:]
    assert len(params) == 1


def test_context_summary_repo_constructor_takes_path_only() -> None:
    sig = inspect.signature(ContextSummaryRepo.__init__)
    params = list(sig.parameters.values())[1:]
    assert len(params) == 1


def test_no_async_engine_branch_in_episodic(tmp_path) -> None:
    """Sanity: constructing with a non-Path arg should fail loudly.

    The new sync-only constructor calls ``Path(path)`` and ``mkdir`` — passing
    a bare sentinel object must NOT silently succeed by hitting some surviving
    AsyncEngine branch.
    """

    class FakeEngine:
        pass

    with pytest.raises((TypeError, AttributeError, OSError, ValueError)):
        EpisodicRepo(FakeEngine())  # type: ignore[arg-type]
