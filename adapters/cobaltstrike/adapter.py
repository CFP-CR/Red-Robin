"""adapters/cobaltstrike/adapter.py — Python-side connector for CS adapter.

While the `.cna` Aggressor script handles Path 1 (operator-activity capture)
entirely inside the CS Client process, this Python module provides:

  - Path 2 dispatch: TUI-originated commands -> CS REST API (POST /api/v1/beacons/.../consoleCommand)
  - State recovery: GET /api/v1/beacons to enumerate connected beacons at adapter startup
  - Emergency kill switch: POST /api/v1/beacons/{bid}/clearCommandQueue

This module is loaded by the daemon when the CS adapter is configured.
Authentication to the CS REST API uses JWT bearer (CS-side; separate from
Red Robin's bearer token used by the .cna <-> daemon channel).

CS REST API spec verified against OpenAPI (see spec §4 Componente 1.3 for
endpoint inventory).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class CSConfig:
    teamserver_url: str  # e.g., "https://teamserver.acme.local:50443"
    operator_user: str
    operator_password: str
    verify_ssl: bool = False  # CS team servers use self-signed certs


class CSRestClient:
    """Thin wrapper over CS 4.12 REST API.

    Public surface:
      - login()                                       -- exchange creds for JWT
      - list_beacons()                                -- GET /api/v1/beacons
      - dispatch_console_command(bid, cmd)            -- POST /api/v1/beacons/{bid}/consoleCommand
      - clear_command_queue(bid)                      -- POST /api/v1/beacons/{bid}/clearCommandQueue
    """

    def __init__(self, config: CSConfig, *, client: httpx.AsyncClient | None = None):
        self._config = config
        self._client = client or httpx.AsyncClient(
            verify=config.verify_ssl,
            timeout=httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0),
        )
        self._token: str | None = None

    async def login(self) -> None:
        r = await self._client.post(
            f"{self._config.teamserver_url}/api/auth/login",
            json={
                "username": self._config.operator_user,
                "password": self._config.operator_password,
                "duration_ms": 3600000,  # 1 hour
            },
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]

    def _auth_headers(self) -> dict[str, str]:
        if self._token is None:
            raise RuntimeError("CSRestClient not logged in; call login() first")
        return {"Authorization": f"Bearer {self._token}"}

    async def list_beacons(self) -> list[dict]:
        r = await self._client.get(
            f"{self._config.teamserver_url}/api/v1/beacons",
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def dispatch_console_command(self, bid: str, command_line: str) -> dict:
        r = await self._client.post(
            f"{self._config.teamserver_url}/api/v1/beacons/{bid}/consoleCommand",
            headers=self._auth_headers(),
            json={"command": command_line},
        )
        r.raise_for_status()
        return r.json()

    async def clear_command_queue(self, bid: str) -> dict:
        """Emergency kill switch. Flushes ALL queued tasks for the beacon (blunt)."""
        r = await self._client.post(
            f"{self._config.teamserver_url}/api/v1/beacons/{bid}/clearCommandQueue",
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()
