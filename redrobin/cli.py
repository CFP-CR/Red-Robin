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
import time
from pathlib import Path

from redrobin.application.engagement_service import EngagementError, EngagementService
from redrobin.application.transport_auth import get_or_create_token
from redrobin.cli_helpers.daemon_pidfile import PidFile, is_pid_alive, read_pid
from redrobin.infrastructure.filesystem import REDROBIN_ROOT, ensure_layout

_PID_PATH = Path(
    os.environ.get("REDROBIN_PID_FILE", str(Path.home() / ".redrobin" / "daemon.pid"))
)


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
    with PidFile(_PID_PATH):
        uvicorn.run(
            "redrobin.api.app:create_app",
            host=host,
            port=port,
            factory=True,
            log_level="info",
        )
    return 0


def cmd_daemon_stop(_args: argparse.Namespace) -> int:
    pid = read_pid(_PID_PATH)
    if pid is None:
        print("no daemon running (no PID file)")
        return 0
    if not is_pid_alive(pid):
        print(f"PID file references dead process {pid}; removing")
        _PID_PATH.unlink(missing_ok=True)
        return 0
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            print(f"failed to open process {pid}")
            return 1
        try:
            kernel32.TerminateProcess(handle, 0)
        finally:
            kernel32.CloseHandle(handle)
        deadline = time.time() + 10
        while is_pid_alive(pid) and time.time() < deadline:
            time.sleep(0.1)
    else:
        import signal

        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 10
        while is_pid_alive(pid) and time.time() < deadline:
            time.sleep(0.1)
    _PID_PATH.unlink(missing_ok=True)
    print(f"daemon stopped (pid={pid})")
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
    import asyncio
    from typing import Any

    from redrobin.cli_helpers.kb_ingest import ingest_file
    from redrobin.infrastructure.llm.voyage_client import VoyageClient
    from redrobin.infrastructure.vector_store import VectorStore

    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"[!] {path} does not exist", file=sys.stderr)
        return 1
    if not path.is_file():
        print(f"[!] {path} is not a regular file (kb add expects a single file)", file=sys.stderr)
        return 1

    ensure_layout()
    kb_index_path = REDROBIN_ROOT / "shared" / "kb-index" / "index.npz"
    store = VectorStore.load(kb_index_path) if kb_index_path.exists() else VectorStore()

    class _VoyageAdapter:
        def __init__(self) -> None:
            self._client = VoyageClient()

        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            result = await self._client.embed_documents(texts)
            return list(result.vectors)

    class _StoreAdapter:
        def __init__(self, vs: VectorStore) -> None:
            self._vs = vs

        async def upsert(
            self,
            *,
            doc_id: str,
            chunk_id: str,
            text: str,
            vector: list[float],
            metadata: dict[str, Any],
        ) -> None:
            self._vs.add(
                ids=[chunk_id],
                texts=[text],
                vectors=[vector],
                metadatas=[metadata],
            )

    tags: list[str] = list(args.tag or [])
    print(f"[*] KB indexing {path} (tags={tags})")

    try:
        embedder = _VoyageAdapter()
    except RuntimeError as e:
        print(f"[!] {e}", file=sys.stderr)
        print("[!] VOYAGE_API_KEY must be set to index KB content", file=sys.stderr)
        return 1

    n = asyncio.run(
        ingest_file(
            path=path,
            tags=tags,
            embedder=embedder,
            store=_StoreAdapter(store),
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
    )
    store.save(kb_index_path)
    print(f"[+] Indexed {n} chunk(s) into {kb_index_path}")
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
