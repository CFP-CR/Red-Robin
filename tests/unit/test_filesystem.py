"""Filesystem layout enforcement — ~/.redrobin/ tree creation + chmod 700."""

import os
import stat

import pytest


@pytest.fixture(autouse=True)
def _isolated_redrobin_root(tmp_path, monkeypatch):
    """Point REDROBIN_ROOT at a temp dir per test for isolation."""
    monkeypatch.setattr(
        "redrobin.infrastructure.filesystem.REDROBIN_ROOT",
        tmp_path / ".redrobin",
    )
    yield tmp_path / ".redrobin"


def test_ensure_layout_creates_all_dirs(_isolated_redrobin_root):
    from redrobin.infrastructure.filesystem import ensure_layout

    ensure_layout()
    root = _isolated_redrobin_root
    assert (root / "shared").is_dir()
    assert (root / "engagements").is_dir()
    assert (root / "engagements" / "archived").is_dir()
    assert (root / "kb-source").is_dir()
    assert (root / "rules" / "shared").is_dir()
    assert (root / "rules" / "personal").is_dir()


def test_ensure_layout_is_idempotent(_isolated_redrobin_root):
    from redrobin.infrastructure.filesystem import ensure_layout

    ensure_layout()
    ensure_layout()  # second call must not error
    assert _isolated_redrobin_root.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="Windows has different permission model")
def test_ensure_layout_enforces_chmod_700_on_root(_isolated_redrobin_root):
    from redrobin.infrastructure.filesystem import ensure_layout

    ensure_layout()
    mode = stat.S_IMODE(_isolated_redrobin_root.stat().st_mode)
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="Windows has different permission model")
def test_ensure_layout_enforces_chmod_700_on_subdirs(_isolated_redrobin_root):
    from redrobin.infrastructure.filesystem import ensure_layout

    ensure_layout()
    for sub in ["shared", "engagements", "kb-source", "rules", "rules/shared", "rules/personal"]:
        mode = stat.S_IMODE((_isolated_redrobin_root / sub).stat().st_mode)
        assert mode == 0o700, f"{sub}: expected 0o700, got {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="Windows has different permission model")
def test_secure_file_sets_chmod_600(_isolated_redrobin_root):
    from redrobin.infrastructure.filesystem import ensure_layout, secure_file

    ensure_layout()
    test_file = _isolated_redrobin_root / "transport.token"
    test_file.write_text("test-token")
    secure_file(test_file)
    mode = stat.S_IMODE(test_file.stat().st_mode)
    assert mode == 0o600
