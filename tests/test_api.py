import time
import pytest
import aiohttp
from aioresponses import aioresponses
from pyaxencoapi.api import PyAxencoAPI
from unittest.mock import Mock, patch

API_BASE = "https://user-ep.imhotepcreation.com"

@pytest.fixture
async def api_client():
    async with aiohttp.ClientSession() as session:
        api = PyAxencoAPI("sourceid_test", session)
        yield api

@pytest.mark.asyncio
async def test_login_success(api_client):
    with aioresponses() as m:
        m.post(f"{API_BASE}/v1/auth/login", payload={
            "token": "abc123",
            "refresh_token": "def456",
            "id": "user_001"
        })

        await api_client.login(email="test@example.com",password="securepassword")
        assert api_client.token == "abc123"
        assert api_client.refresh_token == "def456"
        assert api_client.user_id == "user_001"

@pytest.mark.asyncio
async def test_login_invalid_response(api_client):
    with aioresponses() as m:
        m.post(f"{API_BASE}/v1/auth/login", payload={})

        with pytest.raises(ValueError):
            await api_client.login(email="test@example.com",password="securepassword")

@pytest.mark.asyncio
async def test_get_devices_cache(api_client):
    api_client._devices_cache = [{"id": "1"}]
    api_client._last_fetch = time.time()
    devices = await api_client.get_devices()
    assert devices == [{"id": "1"}]

@pytest.mark.asyncio
async def test_get_devices_http(api_client):
    api_client.user_id = "user_001"
    with aioresponses() as m:
        m.get(f"{API_BASE}/v1/users/user_001/devices", payload=[{"id": "dev1"}])
        devices = await api_client.get_devices(force=True)
        assert devices == [{"id": "dev1"}]

@pytest.mark.asyncio
async def test_set_device_temperature(api_client):
    api_client.token = "abc"
    with aioresponses() as m:
        m.patch(f"{API_BASE}/v1/devices/device123/state", status=200)
        await api_client.set_device_temperature("device123", 22.5)

@pytest.mark.asyncio
async def test_get_device_state_success(api_client):
    api_client.token = "abc"
    with aioresponses() as m:
        m.get(f"{API_BASE}/v1/devices/device123", payload={"state": "on"})
        state = await api_client.get_device_state("device123")
        assert state == {"state": "on"}

@pytest.mark.asyncio
async def test_get_device_state_error(api_client):
    api_client.token = "abc"
    with aioresponses() as m:
        m.get(f"{API_BASE}/v1/devices/device123", status=500)
        result = await api_client.get_device_state("device123")
        assert result is None

@pytest.mark.asyncio
async def test_logout(api_client):
    api_client.token = "abc"
    api_client.refresh_token = "ref"
    api_client.user_id = "uid"
    with aioresponses() as m:
        m.delete(f"{API_BASE}/v1/auth/logout", status=200)
        await api_client.logout()
        assert api_client.token is None
        assert api_client.refresh_token is None
        assert api_client.user_id is None

@pytest.mark.asyncio
async def test_notify_update_with_listener(api_client):
    mock_cb = Mock()
    api_client._listeners["dev123"] = mock_cb
    with patch("pyaxencoapi.api.get_rfid_by_id", return_value="rfid123"), \
         patch("pyaxencoapi.api.find_childs", return_value=["child1"]), \
         patch.object(api_client, "get_devices", return_value=[{"id": "dev123"}]):
        api_client._listeners["child1"] = mock_cb
        await api_client.notify_update("dev123", {"temp": 21})
        assert mock_cb.call_count == 2

@pytest.mark.asyncio
async def test_refresh_token_success(api_client):
    api_client.refresh_token = "refresh123"
    with aioresponses() as m:
        m.post(f"{API_BASE}/v1/auth/token", payload={"token": "new_token"})
        await api_client.refresh_auth_token()
        assert api_client.token == "new_token"

@pytest.mark.asyncio
async def test_refresh_token_invalid_response(api_client):
    api_client.refresh_token = "refresh123"
    with aioresponses() as m:
        m.post(f"{API_BASE}/v1/auth/token", payload={})
        with pytest.raises(ValueError):
            await api_client.refresh_auth_token()

@pytest.mark.asyncio
async def test_auto_refresh_token_on_unauthorized(api_client):

    api_client.token = "old_token"
    api_client.refresh_token = "refresh123"

    with aioresponses() as m:

        m.get(
            f"{API_BASE}/v1/devices/device123",
            status=401,
            reason="Unauthorized"
        )

        m.post(
            f"{API_BASE}/v1/auth/token",
            payload={"token": "new_token"}
        )

        m.get(
            f"{API_BASE}/v1/devices/device123",
            payload={"state": "refreshed"}
        )

        result = await api_client.get_device_state("device123")

    assert result == {"state": "refreshed"}
    assert api_client.token == "new_token"