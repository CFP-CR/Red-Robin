"""redrobin/infrastructure/cs_adapter_loader.py — adapter YAML config discovery + client builder.

Scans a directory for ``*.yaml`` adapter configurations and constructs a
logged-in ``CSRestClient`` per config. Loaded by the daemon at startup
(Slice 9, closes gap #8) so ALLOW verdicts can dispatch to a real CS team
server's REST API.

YAML schema (see ``~/.redrobin/adapters/cs-main.yaml``)::

    name: cs-main
    kind: cobaltstrike
    teamserver_url: https://teamserver.local:50443
    operator_user: op
    operator_password: secret
    verify_ssl: false    # CS team servers usually carry self-signed certs

The application layer re-exports this module via
``redrobin.application.cs_adapter_lifecycle`` so the api/tui layers can call
it without violating the importlinter ``no-direct-infra-from-presentation``
contract.
"""

from __future__ import annotations

import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import yaml

# The CS adapter lives outside the redrobin package (it's a delivered
# artifact under adapters/cobaltstrike/). Add the package root to sys.path
# on import so the daemon can find it via ``from adapter import …``.
_ADAPTER_ROOT = Path(__file__).resolve().parents[2] / "adapters" / "cobaltstrike"
if str(_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_ROOT))

from adapter import CSConfig, CSRestClient  # noqa: E402


@dataclass(frozen=True)
class AdapterConfig:
    """Parsed adapter YAML — one per discovered file."""

    name: str
    kind: str
    teamserver_url: str
    operator_user: str
    operator_password: str
    verify_ssl: bool


def load_adapter_configs(adapters_dir: Path) -> list[AdapterConfig]:
    """Return every ``kind: cobaltstrike`` YAML in ``adapters_dir``.

    Silently skips files with other ``kind`` values (future Sliver / Havoc
    adapters live alongside) and non-``.yaml`` files. Returns ``[]`` when the
    directory does not exist — the daemon starts without adapters in that case.
    """
    if not adapters_dir.is_dir():
        return []
    configs: list[AdapterConfig] = []
    for p in sorted(adapters_dir.glob("*.yaml")):
        _warn_loose_perms(p)
        data = yaml.safe_load(p.read_text()) or {}
        if data.get("kind") != "cobaltstrike":
            continue
        configs.append(
            AdapterConfig(
                name=data["name"],
                kind=data["kind"],
                teamserver_url=data["teamserver_url"],
                operator_user=data["operator_user"],
                operator_password=data["operator_password"],
                verify_ssl=_truthy(data.get("verify_ssl", True)),
            )
        )
    return configs


def _truthy(v: object) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _warn_loose_perms(path: Path) -> None:
    """POSIX-only: warn if adapter YAML is group/world readable or writable.

    Windows operators get NO warning (audit pass 1 #16 documented this):
    Windows ACLs differ — protecting adapter YAMLs on Windows requires
    running daemon under an account where NTFS ACLs already restrict
    access, or manually using ``icacls``. Documented in
    ``docs/secrets-handling.md`` (Slice 13).

    The warning prints to stderr deliberately (not via ``logger``) so the
    no-password-in-logs contract (``test_no_password_in_logs``) stays
    pristine — operators still see the WARN on the terminal at boot.
    """
    if sys.platform == "win32":
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
        perms = oct(mode & 0o777)[2:]
        print(
            f"WARN: {path} has loose permissions ({perms}); "
            f"recommended 600 (chmod 600 {path})",
            file=sys.stderr,
        )


def _default_transport() -> Optional[httpx.AsyncBaseTransport]:
    """Production default: let httpx build its own transport.

    Overridden in tests via ``monkeypatch.setattr`` so the daemon's lifespan
    can be exercised against an ``httpx.MockTransport`` without ever opening
    a TLS socket.
    """
    return None


async def build_adapter(
    cfg: AdapterConfig,
    *,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> CSRestClient:
    """Construct a logged-in ``CSRestClient`` from a parsed config.

    The ``transport`` kwarg is the seam tests use to inject ``MockTransport``;
    production callers pass ``None`` and pick up ``_default_transport()`` so
    monkeypatching that single function flips the daemon onto a fake network.
    """
    if transport is None:
        transport = _default_transport()
    http = httpx.AsyncClient(
        verify=cfg.verify_ssl,
        timeout=httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0),
        transport=transport,
    )
    cs_config = CSConfig(
        teamserver_url=cfg.teamserver_url,
        operator_user=cfg.operator_user,
        operator_password=cfg.operator_password,
        verify_ssl=cfg.verify_ssl,
    )
    client = CSRestClient(cs_config, client=http)
    try:
        await client.login()
    except Exception:
        await http.aclose()
        raise
    return client
