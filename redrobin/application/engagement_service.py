"""redrobin/application/engagement_service.py — Engagement lifecycle operations.

Per spec §5.3:
  new <slug>     — create engagements/<slug>/ + engagement.yaml template
  use <slug>     — set active engagement (daemon state)
  list           — list known engagements
  current        — show active engagement
  close [<slug>] — preserve state (resumable)
  archive <slug> — tarball + remove active
  delete <slug>  — rm -rf engagements/<slug>/ (operator confirms)
  import <tarball> — restore archive

Active engagement tracked in `~/.redrobin/.active_engagement` file.
"""

from __future__ import annotations

import shutil
import tarfile
import time
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from redrobin.domain.engagement import Engagement, validate_slug
from redrobin.domain.memory import WorkingMemory
from redrobin.infrastructure.filesystem import redrobin_root
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

ACTIVE_FILE = ".active_engagement"
ENGAGEMENT_YAML = "engagement.yaml"


class EngagementError(Exception):
    """Engagement lifecycle errors (not-found, already-exists, invalid slug)."""


class EngagementService:
    def __init__(self, root: Path | None = None):
        self._root = root or redrobin_root()

    def _engagements_dir(self) -> Path:
        return self._root / "engagements"

    def _path_for(self, slug: str) -> Path:
        validate_slug(slug)
        return self._engagements_dir() / slug

    def new(self, slug: str, *, client: str = "") -> Engagement:
        validate_slug(slug)
        path = self._path_for(slug)
        if path.exists():
            raise EngagementError(f"engagement {slug!r} already exists at {path}")
        path.mkdir(parents=True, exist_ok=True)

        template = {
            "id": slug,
            "client": client,
            "scope": {
                "in_scope": [],
                "out_of_scope": [],
            },
            "objectives": [],
            "current_phase": "not_started",
            "kill_chain_status": {},
            "rules_of_engagement": [],
        }
        (path / ENGAGEMENT_YAML).write_text(yaml.safe_dump(template, sort_keys=False))

        # Initialize empty working memory
        WorkingMemoryRepo(path / "working_memory.json").save(
            WorkingMemory(engagement_id=slug, client=client)
        )

        return Engagement(id=slug, client=client)

    def use(self, slug: str) -> None:
        path = self._path_for(slug)
        if not path.exists():
            raise EngagementError(f"engagement {slug!r} not found")
        (self._root / ACTIVE_FILE).write_text(slug)

    def current(self) -> str | None:
        active = self._root / ACTIVE_FILE
        if not active.exists():
            return None
        return active.read_text().strip() or None

    def list_all(self) -> list[str]:
        d = self._engagements_dir()
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir() and p.name != "archived")

    def close(self, slug: str | None = None) -> None:
        """Close engagement (preserve files; clear active marker)."""
        if slug is None:
            slug = self.current()
            if slug is None:
                raise EngagementError("no active engagement to close")
        path = self._path_for(slug)
        if not path.exists():
            raise EngagementError(f"engagement {slug!r} not found")
        active = self._root / ACTIVE_FILE
        if active.exists() and active.read_text().strip() == slug:
            active.unlink()

    def archive(self, slug: str) -> Path:
        path = self._path_for(slug)
        if not path.exists():
            raise EngagementError(f"engagement {slug!r} not found")
        archive_dir = self._engagements_dir() / "archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        tarball_path = archive_dir / f"{slug}-{timestamp}.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(path, arcname=slug)
        return tarball_path

    def delete(self, slug: str) -> None:
        path = self._path_for(slug)
        if not path.exists():
            raise EngagementError(f"engagement {slug!r} not found")
        # Don't allow deleting active engagement without close first
        if self.current() == slug:
            raise EngagementError(f"cannot delete active engagement {slug!r}; close first")
        shutil.rmtree(path)
