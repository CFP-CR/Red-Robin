"""Shared test fixtures.

Deliberately holds NO autouse fixtures: nothing here changes how a test behaves
unless that test (or its module) asks for it. Suite-wide defaults would mask the
very boot/env behaviour several modules exist to pin.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def restore_environ():
    """Snapshot ``os.environ`` and restore it after the test.

    ``load_dotenv`` writes straight into ``os.environ`` and there is no undo:
    ``monkeypatch`` can only restore keys it set or deleted itself, so a key that
    a ``.env`` introduced survives to the end of the session and silently
    configures every later test (issue #51).

    Any module whose tests reach ``redrobin.cli._load_env_for_cli`` — directly or
    through ``cli.main`` — should opt in with::

        pytestmark = pytest.mark.usefixtures("restore_environ")

    Requested that way it is set up before the test's own fixtures and therefore
    torn down after them, so the snapshot predates anything ``monkeypatch`` did
    and this restore is the final word.
    """
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)
