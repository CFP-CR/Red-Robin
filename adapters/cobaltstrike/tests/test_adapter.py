"""CS adapter — Python-side REST client tests."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADAPTER_ROOT))

from adapter import CSConfig, CSRestClient  # noqa: E402


@pytest.fixture
def config():
    return CSConfig(
        teamserver_url="https://teamserver.test:50443",
        operator_user="juan",
        operator_password="testpass",
    )


@pytest.mark.asyncio
async def test_login_exchanges_creds_for_jwt(config):
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value={"access_token": "jwt-xyz"})
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    client = CSRestClient(config, client=mock_client)
    await client.login()

    assert client._token == "jwt-xyz"
    call = mock_client.post.await_args
    assert call.args[0] == "https://teamserver.test:50443/api/auth/login"
    assert call.kwargs["json"]["username"] == "juan"


@pytest.mark.asyncio
async def test_dispatch_console_command_uses_bearer(config):
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value={"task_id": "task_001"})
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    client = CSRestClient(config, client=mock_client)
    client._token = "jwt-test"
    result = await client.dispatch_console_command("b1", "pwd")

    assert result == {"task_id": "task_001"}
    call = mock_client.post.await_args
    assert call.kwargs["headers"]["Authorization"] == "Bearer jwt-test"
    assert call.kwargs["json"] == {"command": "pwd"}


@pytest.mark.asyncio
async def test_list_beacons_returns_list(config):
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=[{"bid": "b1"}, {"bid": "b2"}])
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    client = CSRestClient(config, client=mock_client)
    client._token = "jwt-test"
    beacons = await client.list_beacons()
    assert len(beacons) == 2


@pytest.mark.asyncio
async def test_clear_command_queue_returns_response(config):
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value={"cleared": 3})
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    client = CSRestClient(config, client=mock_client)
    client._token = "jwt-test"
    result = await client.clear_command_queue("b1")
    assert result == {"cleared": 3}


def test_auth_headers_raise_when_not_logged_in(config):
    client = CSRestClient(config)
    with pytest.raises(RuntimeError, match="not logged in"):
        client._auth_headers()


@pytest.mark.asyncio
async def test_aclose_closes_underlying_client(config):
    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()
    client = CSRestClient(config, client=mock_client)
    await client.aclose()
    mock_client.aclose.assert_awaited_once()
