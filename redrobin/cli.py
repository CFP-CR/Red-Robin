"""redrobin/cli.py — Operator-facing CLI (argparse + dispatch only).

Commands:
    redrobin init                    — first-run setup
    redrobin doctor                  — preflight check
    redrobin daemon start            — launch daemon (uvicorn)
    redrobin daemon stop             — stop running daemon
    redrobin tui                     — launch TUI client
    redrobin engagement new <slug>   — create engagement scaffold
    redrobin engagement use <slug>   — activate engagement
    redrobin engagement list         — list engagements
    redrobin engagement current      — show active
    redrobin engagement close [<slug>] — close (preserve state)
    redrobin engagement archive <slug> — tarball
    redrobin engagement delete <slug> --confirm — delete
    redrobin kb add <path> [--watch] — register KB source dir
    redrobin memory edit <file>      — open in $EDITOR
    redrobin replay <archive>        — replay archived engagement

Slice 12 of v0.1.x hardening decomposed every ``cmd_*`` body into
``redrobin/cli_helpers/<name>.py``; this module is now argparse + dispatch
only (≤400 LOC). Each ``cmd_<name>`` wrapper:

  1. Calls :func:`_load_env_for_cli` (anchored ``.env`` load).
  2. Resolves any plain-string args from the argparse ``Namespace``.
  3. Delegates to ``run_<name>(...)`` in the matching helper module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv


def _load_env_for_cli() -> None:
    """Load the CWD .env and the root .env, with safe precedence.

    Precedence, highest to lowest:

      1. explicit shell env vars (``export FOO=bar``)
      2. ``<cwd>/.env``
      3. ``<root>/.env`` — the file ``redrobin init`` writes

    ``override=False`` on both loads is what produces it, provided they run in
    that order: the first writer of a key wins, and neither ever clobbers the
    shell.

    Loading the root .env is what makes the documented first-run flow work.
    ``redrobin init`` writes its .env into the root it creates (``~/.redrobin``
    by default), so a CWD-only load left every later command run from the
    checkout reporting ``ANTHROPIC_API_KEY: MISSING`` / ``ENGAGEMENT: (none)``
    while silently operating on the DEFAULT root.

    The root is resolved only AFTER the CWD load because ``REDROBIN_ROOT`` may
    itself be declared in ``<cwd>/.env``; with none set anywhere it falls back
    to the same default the rest of the code uses (``Settings.root``, via
    ``redrobin_root()``).

    SECURITY: both paths are pinned exactly — python-dotenv's default behavior
    walks PARENT directories looking for .env, which can silently pick up an
    operator's home-directory .env when run from a customer subdirectory
    (cross-engagement secret leak). ``<root>/.env`` is not a walk: it is the
    operator's own root, the file the tool itself wrote.
    """
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    # Deferred import (matches every cmd_* below): keeps `redrobin --help` off
    # the Settings/pydantic import path. filesystem.redrobin_root() resolves
    # Settings lazily, so it sees whatever the CWD load just put in os.environ.
    from redrobin.infrastructure.filesystem import redrobin_root

    load_dotenv(dotenv_path=redrobin_root().expanduser() / ".env", override=False)


def cmd_init(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.application.transport_auth import get_or_create_token
    from redrobin.cli_helpers.init_cmd import run_init
    from redrobin.infrastructure.config import Settings
    from redrobin.infrastructure.filesystem import ensure_layout

    settings = Settings()
    root = Path(args.root) if args.root else settings.root
    rc = run_init(
        root=root,
        anthropic_key=args.anthropic_key,
        voyage_key=args.voyage_key,
        engagement_slug=args.engagement_slug,
        interactive=not args.non_interactive,
    )
    if rc != 0:
        return rc
    # Preserve load-bearing behaviour of the previous cmd_init: ensure the
    # ~/.redrobin layout (chmod 700 dirs etc) and generate the bearer token
    # so the daemon's auth surface is ready on first boot. Running these
    # AFTER run_init is fine because both are idempotent.
    ensure_layout()
    get_or_create_token()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.doctor_cmd import run_doctor
    from redrobin.infrastructure.config import Settings

    return run_doctor(settings=Settings(), quick=bool(getattr(args, "quick", False)))


def cmd_daemon_start(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.daemon_cmd import run_daemon_start

    return run_daemon_start(
        host=args.host,
        port=args.port,
        allow_degraded=bool(getattr(args, "allow_degraded", False)),
    )


def cmd_daemon_stop(_args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.daemon_cmd import run_daemon_stop

    return run_daemon_stop()


def cmd_tui(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.tui_cmd import resolve_token, run_tui

    return run_tui(
        connect_ws=args.connect_ws,
        connect_http=args.connect_http,
        token=resolve_token(args.token),
    )


def cmd_engagement_new(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_new

    return run_new(
        svc=make_service(),
        slug=args.slug,
        client=args.client,
        name=args.name,
        in_scope=args.in_scope,
        out_of_scope=args.out_of_scope,
        summary=args.summary,
    )


def cmd_engagement_use(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_use

    return run_use(svc=make_service(), slug=args.slug)


def cmd_engagement_list(_args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_list

    return run_list(svc=make_service())


def cmd_engagement_current(_args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_current

    return run_current(svc=make_service())


def cmd_engagement_close(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_close

    return run_close(svc=make_service(), slug=args.slug)


def cmd_engagement_archive(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_archive

    return run_archive(svc=make_service(), slug=args.slug)


def cmd_engagement_delete(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.engagement_cmd import make_service, run_delete

    return run_delete(svc=make_service(), slug=args.slug, confirm=bool(args.confirm))


def cmd_kb_add(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.kb_add_cmd import run_kb_add

    return run_kb_add(
        path=Path(args.path).expanduser().resolve(),
        tags=list(args.tag or []),
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )


def cmd_memory_edit(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.memory_cmd import run_memory_edit

    return run_memory_edit(target=Path(args.file).expanduser().resolve())


def cmd_replay(args: argparse.Namespace) -> int:
    _load_env_for_cli()
    from redrobin.cli_helpers.replay_cmd import resolve_token, run_replay

    return run_replay(
        archive=Path(args.archive).expanduser().resolve(),
        base_url=args.base_url,
        token=resolve_token(args.token),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="redrobin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser("init", help="First-time setup")
    init_p.add_argument(
        "--root",
        default=None,
        help="Root path (default: $REDROBIN_ROOT or ~/.redrobin)",
    )
    init_p.add_argument(
        "--non-interactive",
        action="store_true",
        dest="non_interactive",
        help="Skip prompts; use --anthropic-key and --voyage-key",
    )
    init_p.add_argument("--anthropic-key", default=None, dest="anthropic_key")
    init_p.add_argument("--voyage-key", default=None, dest="voyage_key")
    init_p.add_argument(
        "--engagement-slug",
        default="op-default",
        dest="engagement_slug",
    )
    init_p.set_defaults(func=cmd_init)
    doctor_p = sub.add_parser("doctor", help="Check setup + connectivity")
    doctor_p.add_argument(
        "--quick",
        action="store_true",
        help="Skip network probes (env-var presence check only)",
    )
    doctor_p.set_defaults(func=cmd_doctor)

    daemon = sub.add_parser("daemon")
    daemon_sub = daemon.add_subparsers(dest="daemon_cmd", required=True)
    daemon_start = daemon_sub.add_parser("start")
    daemon_start.add_argument("--host", default=None)
    daemon_start.add_argument("--port", type=int, default=None)
    daemon_start.add_argument(
        "--allow-degraded",
        action="store_true",
        dest="allow_degraded",
        help=(
            "Allow daemon to start without LLM keys (no assistant, enrichment, "
            "or summaries; capture still works). Equivalent to REDROBIN_ALLOW_DEGRADED=1."
        ),
    )
    daemon_start.set_defaults(func=cmd_daemon_start)
    daemon_sub.add_parser("stop").set_defaults(func=cmd_daemon_stop)

    tui_parser = sub.add_parser("tui")
    tui_parser.add_argument(
        "--connect-ws",
        default="ws://127.0.0.1:7777/v1/tui",
        help="WebSocket URL for the daemon TUI stream (default: ws://127.0.0.1:7777/v1/tui)",
    )
    tui_parser.add_argument(
        "--connect-http",
        default="http://127.0.0.1:7777",
        help="HTTP base URL for the daemon API (default: http://127.0.0.1:7777)",
    )
    tui_parser.add_argument(
        "--token",
        default=None,
        help="Bearer token (defaults to ~/.redrobin/transport.token)",
    )
    tui_parser.set_defaults(func=cmd_tui)

    eng = sub.add_parser("engagement")
    eng_sub = eng.add_subparsers(dest="eng_cmd", required=True)

    new_eng = eng_sub.add_parser("new")
    new_eng.add_argument("slug")
    new_eng.add_argument("--client", default=None)
    new_eng.add_argument("--name", default=None, help="Friendly engagement name")
    new_eng.add_argument(
        "--in-scope",
        action="append",
        default=None,
        dest="in_scope",
        help="In-scope host/IP/CIDR (repeatable; comma-separated allowed)",
    )
    new_eng.add_argument(
        "--out-of-scope",
        action="append",
        default=None,
        dest="out_of_scope",
        help="Out-of-scope host/IP/CIDR (repeatable; comma-separated allowed)",
    )
    new_eng.add_argument(
        "--summary",
        default=None,
        help="Short engagement summary surfaced to the assistant",
    )
    new_eng.set_defaults(func=cmd_engagement_new)

    use_eng = eng_sub.add_parser("use")
    use_eng.add_argument("slug")
    use_eng.set_defaults(func=cmd_engagement_use)

    eng_sub.add_parser("list").set_defaults(func=cmd_engagement_list)
    eng_sub.add_parser("current").set_defaults(func=cmd_engagement_current)

    close_eng = eng_sub.add_parser("close")
    close_eng.add_argument("slug", nargs="?", default=None)
    close_eng.set_defaults(func=cmd_engagement_close)

    archive_eng = eng_sub.add_parser("archive")
    archive_eng.add_argument("slug")
    archive_eng.set_defaults(func=cmd_engagement_archive)

    delete_eng = eng_sub.add_parser("delete")
    delete_eng.add_argument("slug")
    delete_eng.add_argument("--confirm", action="store_true")
    delete_eng.set_defaults(func=cmd_engagement_delete)

    kb = sub.add_parser("kb")
    kb_sub = kb.add_subparsers(dest="kb_cmd", required=True)
    kb_add = kb_sub.add_parser("add")
    kb_add.add_argument("path")
    kb_add.add_argument("--watch", action="store_true")
    kb_add.add_argument(
        "--tag",
        action="append",
        default=None,
        help="Metadata tag (repeatable): --tag mimikatz --tag lsass",
    )
    kb_add.add_argument(
        "--chunk-size",
        type=int,
        default=1200,
        help="Chunk size in characters (default: 1200)",
    )
    kb_add.add_argument(
        "--chunk-overlap",
        type=int,
        default=200,
        help="Chunk overlap in characters (default: 200)",
    )
    kb_add.set_defaults(func=cmd_kb_add)

    memory = sub.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="memory_cmd", required=True)
    memory_edit = memory_sub.add_parser("edit")
    memory_edit.add_argument("file")
    memory_edit.set_defaults(func=cmd_memory_edit)

    replay = sub.add_parser("replay")
    replay.add_argument("archive", help="Path to a JSONL trace file")
    replay.add_argument(
        "--base-url",
        default="http://127.0.0.1:7777",
        help="Daemon HTTP base URL (default: http://127.0.0.1:7777)",
    )
    replay.add_argument(
        "--token",
        default=None,
        help="Bearer token (defaults to ~/.redrobin/transport.token)",
    )
    replay.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Reserved for future pacing control (currently unused)",
    )
    replay.set_defaults(func=cmd_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
