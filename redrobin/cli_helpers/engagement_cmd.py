"""``redrobin engagement *`` business logic — extracted in Slice 12.

The CLI wrappers in :mod:`redrobin.cli` are thin shells that load ``.env`` and
construct an :class:`EngagementService`; the ``run_*`` helpers here apply the
actual operations against that service so the bodies are testable without
argparse plumbing.
"""
from __future__ import annotations

import sys

from redrobin.application.engagement_service import EngagementError, EngagementService
from redrobin.infrastructure.filesystem import ensure_layout, redrobin_root


def make_service() -> EngagementService:
    """Construct an :class:`EngagementService` against the on-disk root layout."""
    ensure_layout()
    return EngagementService()


def run_new(*, svc: EngagementService, slug: str, client: str | None) -> int:
    try:
        eng = svc.new(slug, client=client or "")
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Created engagement {eng.slug}")
    print(f"[+] Edit {redrobin_root()}/engagements/{eng.slug}/engagement.yaml")
    return 0


def run_use(*, svc: EngagementService, slug: str) -> int:
    try:
        svc.use(slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Active engagement: {slug}")
    return 0


def run_list(*, svc: EngagementService) -> int:
    items = svc.list_all()
    if not items:
        print("(no engagements)")
        return 0
    for slug in items:
        marker = " (active)" if slug == svc.current() else ""
        print(f"  {slug}{marker}")
    return 0


def run_current(*, svc: EngagementService) -> int:
    active = svc.current()
    if active is None:
        print("(no active engagement)")
        return 1
    print(active)
    return 0


def run_close(*, svc: EngagementService, slug: str | None) -> int:
    try:
        svc.close(slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Closed engagement {slug or '(active)'}")
    return 0


def run_archive(*, svc: EngagementService, slug: str) -> int:
    try:
        tarball = svc.archive(slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Archived to {tarball}")
    return 0


def run_delete(*, svc: EngagementService, slug: str, confirm: bool) -> int:
    if not confirm:
        print(f"[!] Refusing to delete {slug} without --confirm")
        return 2
    try:
        svc.delete(slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Deleted engagement {slug}")
    return 0
