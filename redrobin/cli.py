"""redrobin/cli.py — Operator-facing CLI.

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
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from redrobin.application.engagement_service import EngagementError, EngagementService
from redrobin.application.transport_auth import get_or_create_token
from redrobin.infrastructure.filesystem import REDROBIN_ROOT, ensure_layout


def cmd_init(_args: argparse.Namespace) -> int:
    ensure_layout()
    # Trigger token generation on init
    get_or_create_token()
    print(f"[+] Initialized {REDROBIN_ROOT}/")
    print(f"[+] Bearer token created (chmod 600): {REDROBIN_ROOT / 'transport.token'}")
    print("[+] Edit ~/.redrobin/config.toml to configure (optional)")
    print("[+] Next: `redrobin doctor` to verify dependencies, then `redrobin daemon start`")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    ok = True
    print("[*] Red Robin doctor (preflight check)")

    # ANTHROPIC_API_KEY
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("  [OK] ANTHROPIC_API_KEY set")
    else:
        print("  [!!] ANTHROPIC_API_KEY not set - Layer B will degrade to no-op")
        ok = False

    # VOYAGE_API_KEY
    if os.environ.get("VOYAGE_API_KEY"):
        print("  [OK] VOYAGE_API_KEY set")
    else:
        print("  [!!] VOYAGE_API_KEY not set - KB retrieval will be empty")
        ok = False

    # ~/.redrobin/ layout
    if REDROBIN_ROOT.exists():
        print(f"  [OK] {REDROBIN_ROOT} exists")
    else:
        print(f"  [!!] {REDROBIN_ROOT} not initialized - run `redrobin init`")
        ok = False

    # rules dir
    rules_dir = REDROBIN_ROOT / "rules"
    if rules_dir.exists():
        rule_count = sum(1 for _ in rules_dir.rglob("*.yaml"))
        print(f"  [OK] rules loaded: {rule_count}")
    else:
        print("  [!] no rules directory yet (will be created on first daemon start)")

    print("[+] Doctor finished:", "OK" if ok else "DEGRADED (Layer A still works)")
    return 0 if ok else 0  # never block daemon startup - degradation is documented


def cmd_daemon_start(args: argparse.Namespace) -> int:
    import uvicorn

    ensure_layout()
    get_or_create_token()
    host = args.host or "127.0.0.1"
    port = args.port or 7777
    print(f"[*] Starting Red Robin daemon on {host}:{port}")
    uvicorn.run("redrobin.api.app:create_app", host=host, port=port, factory=True, log_level="info")
    return 0


def cmd_daemon_stop(_args: argparse.Namespace) -> int:
    print("[!] `redrobin daemon stop` is a placeholder for v0.1.")
    print("    Stop the daemon with Ctrl+C in the foreground terminal, or `kill <pid>`.")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    from redrobin.tui.app import run_tui

    # Default token = the locally-stored operator token. ``redrobin tui`` with
    # no --token flag should Just Work against the local daemon.
    token = args.token
    if token is None:
        try:
            token = get_or_create_token()
        except Exception:
            token = None

    run_tui(
        connect_ws=args.connect_ws,
        connect_http=args.connect_http,
        token=token,
    )
    return 0


def _engagement_service() -> EngagementService:
    ensure_layout()
    return EngagementService()


def cmd_engagement_new(args: argparse.Namespace) -> int:
    svc = _engagement_service()
    try:
        eng = svc.new(args.slug, client=args.client or "")
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Created engagement {eng.id}")
    print(f"[+] Edit {REDROBIN_ROOT}/engagements/{eng.id}/engagement.yaml")
    return 0


def cmd_engagement_use(args: argparse.Namespace) -> int:
    svc = _engagement_service()
    try:
        svc.use(args.slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Active engagement: {args.slug}")
    return 0


def cmd_engagement_list(_args: argparse.Namespace) -> int:
    svc = _engagement_service()
    items = svc.list_all()
    if not items:
        print("(no engagements)")
        return 0
    for slug in items:
        marker = " (active)" if slug == svc.current() else ""
        print(f"  {slug}{marker}")
    return 0


def cmd_engagement_current(_args: argparse.Namespace) -> int:
    svc = _engagement_service()
    active = svc.current()
    if active is None:
        print("(no active engagement)")
        return 1
    print(active)
    return 0


def cmd_engagement_close(args: argparse.Namespace) -> int:
    svc = _engagement_service()
    try:
        svc.close(args.slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Closed engagement {args.slug or '(active)'}")
    return 0


def cmd_engagement_archive(args: argparse.Namespace) -> int:
    svc = _engagement_service()
    try:
        tarball = svc.archive(args.slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Archived to {tarball}")
    return 0


def cmd_engagement_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(f"[!] Refusing to delete {args.slug} without --confirm")
        return 2
    svc = _engagement_service()
    try:
        svc.delete(args.slug)
    except EngagementError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] Deleted engagement {args.slug}")
    return 0


def cmd_kb_add(args: argparse.Namespace) -> int:
    # Phase 1: stub - full impl is async + lives in KBService
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"[!] {path} does not exist", file=sys.stderr)
        return 1
    print(f"[*] KB indexing {path} - invoke from daemon: see docs/kb-cli.md")
    print("[*] watch=True" if args.watch else "[*] one-shot index")
    return 0


def cmd_memory_edit(args: argparse.Namespace) -> int:
    target = Path(args.file).expanduser().resolve()
    if not target.exists():
        print(f"[!] {target} does not exist", file=sys.stderr)
        return 1
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
    subprocess.call([editor, str(target)])
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    # Minimal: print intent; full impl invokes Sliver-Mock-style replayer
    archive = Path(args.archive).expanduser().resolve()
    if not archive.exists():
        print(f"[!] {archive} does not exist", file=sys.stderr)
        return 1
    print(f"[*] Replay {archive} at {args.speed}x - wire daemon-side in v0.1.1")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="redrobin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    daemon = sub.add_parser("daemon")
    daemon_sub = daemon.add_subparsers(dest="daemon_cmd", required=True)
    daemon_start = daemon_sub.add_parser("start")
    daemon_start.add_argument("--host", default=None)
    daemon_start.add_argument("--port", type=int, default=None)
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
    kb_add.set_defaults(func=cmd_kb_add)

    memory = sub.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="memory_cmd", required=True)
    memory_edit = memory_sub.add_parser("edit")
    memory_edit.add_argument("file")
    memory_edit.set_defaults(func=cmd_memory_edit)

    replay = sub.add_parser("replay")
    replay.add_argument("archive")
    replay.add_argument("--speed", type=float, default=1.0)
    replay.set_defaults(func=cmd_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
