"""API client for interacting with the Axenco API."""

from collections.abc import Callable
from typing import Any, Coroutine
import logging
import time
import functools
import aiohttp
import socketio
import async_timeout

from .utils import find_childs, get_rfid_by_id

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://user-ep.imhotepcreation.com"


def auto_refresh_token(func: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorator to automatically refresh the token if a request fails with 401."""
    @functools.wraps(func)
    async def wrapper(self: "PyAxencoAPI", *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                _LOGGER.debug("PyAxencoAPI : Token expired, attempting to refresh")
                await self.refresh_auth_token()
                return await func(self, *args, **kwargs)
            raise
    return wrapper


class PyAxencoAPI:
    """API client for interacting with the Axenco API."""

    def __init__(self, source_id: str, session: aiohttp.ClientSession) -> None:
        """Initialize the client.

        Args:
            source_id: The unique id for request.
            session: The aiohttp session for making HTTP requests.

        """
        self.session = session
        self.source_id = source_id
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None
        self.sio = socketio.AsyncClient()

        # Cache
        self._devices_cache: list[dict] = []
        self._last_fetch: float = 0

        # Websocket listeners
        self._listeners: dict[str, Callable[[dict], None]] = {}
        self._discovery_callbacks: list[Callable[[dict], None]] = []
        self._removal_callbacks: list[Callable[[str], None]] = []


    async def connect_websocket(self) -> None:
        """Connect the Axenco WebSocket and handle events."""
        @self.sio.event
        async def connect() -> None:
            _LOGGER.debug("PyAxencoAPI : WebSocket connected to Axenco")

        @self.sio.event
        async def disconnect() -> None:
            _LOGGER.debug("PyAxencoAPI : WebSocket disconnected from Axenco")

        @self.sio.on("setState")
        async def on_set_state(data: dict) -> None:
            device_id = data.get("objectId")
            _LOGGER.debug("PyAxencoAPI : WS SETSTATE received: %s", data)
            await self.notify_update(device_id, data.get("data"))

        @self.sio.on("setProgram")
        async def on_set_program(data: dict) -> None:
            device_id = data.get("objectId")
            _LOGGER.debug("PyAxencoAPI : WS SETPROGRAM received: %s", data)
            await self.notify_update(device_id, {"program": data.get("data")})

        @self.sio.event
        async def connect_error(data: dict) -> None:
            _LOGGER.error("PyAxencoAPI : WebSocket connection error: %s", data)

        @self.sio.on("update")
        async def on_update(data: dict) -> None:
            device_id = data.get("objectId")
            _LOGGER.debug("PyAxencoAPI : WS UPDATE received: %s", data)
            await self.notify_update(device_id, data.get("data"))

        @self.sio.on("discover")
        async def on_discover(data: dict) -> None:
            _LOGGER.debug("PyAxencoAPI : WS DISCOVER received: %s", data)

        @self.sio.on("updateExtDevState")
        async def on_update_ext(data: dict) -> None:
            device_id = data.get("objectId")
            _LOGGER.debug("PyAxencoAPI : WS UPDATE SUB-SERVICE received: %s", data)
            await self.notify_update(device_id, data.get("data"))

        @self.sio.on("setExtDevState")
        async def on_set_ext(data: dict) -> None:
            device_id = data.get("objectId")
            _LOGGER.debug("PyAxencoAPI : WS SETSTATE SUB-SERVICE received: %s", data)
            await self.notify_update(device_id, data.get("data"))

        @self.sio.on("link")
        async def on_link(data: dict) -> None:
            devices = data.get("data", {}).get("devices", [])
            _LOGGER.debug("PyAxencoAPI : WS LINK received: %s", data)
            await self.notify_discovery(devices)

        @self.sio.on("unlink")
        async def on_unlink(data: dict) -> None:
            device_id = data.get("objectId")
            _LOGGER.debug("PyAxencoAPI : WS UNLINK received: %s", data)
            await self.notify_removal(device_id)

        try:
            ws_url = f"{API_BASE}?userId={self.user_id}"

            await self.sio.connect(
                ws_url,
                socketio_path="socket.io-v2",
                transports=["websocket"],
                headers=self.get_auth_headers()
            )

        except (socketio.exceptions.ConnectionError, aiohttp.ClientError) as e:
            _LOGGER.error("PyAxencoAPI : WebSocket connection failed: %s", e)

    async def disconnect_websocket(self) -> None:
        """Disconnect the Axenco WebSocket connection."""
        await self.sio.disconnect()

    def register_listener(self, device_id: str, callback: Callable[[dict], None]) -> None:
        """Register a listener for WebSocket updates.

        Args:
            device_id: The ID of the device to listen for updates.
            callback: The callback function to invoke on updates.

        """
        self._listeners[device_id] = callback

    def register_discovery_callback(self, callback: Callable[[dict], None]) -> None:
        """
        Register a callback to be invoked when a new device is discovered.

        Args:
            callback (Callable[[dict], None]): A function that takes a dictionary 
            containing device information as its argument and returns None.
        """
        self._discovery_callbacks.append(callback)

    def register_removal_callback(self, callback: Callable[[str], None]) -> None:
        """
        Registers a callback function to be invoked when a device is unlinked.

        Args:
            callback (Callable[[str], None]): A function that takes a single string argument 
            (representing the device ID) and returns None. This function will be called 
            whenever a device is removed.
        """
        self._removal_callbacks.append(callback)

    async def notify_discovery(self, device: dict) -> None:
        """
        Notify registered listeners about the discovery of a new device.

        Args:
            device (dict): A dictionary containing information about the discovered device.
        """
        for cb in self._discovery_callbacks:
            cb(device)

    async def notify_removal(self, device_id: str) -> None:
        """Notify listeners that a device was removed (unlinked).
        
        Args:
            device_id (str): The ID of the device to remove.
        """
        for cb in self._removal_callbacks:
            cb(device_id)

    async def notify_update(self, device_id: str, new_state: dict) -> None:
        """Notify an entity of a WebSocket update and propagate to child devices.

        Args:
            device_id: The ID of the device to notify.
            new_state: The new state to notify.

        """
        # Notify the main device
        if cb := self._listeners.get(device_id):
            cb(new_state)

        devices = await self.get_devices()

        # Find child devices
        device_rfid = get_rfid_by_id(devices, device_id)
        if device_rfid != "":
            child_ids = find_childs(devices, device_rfid)

            # Notify child devices
            for child_id in child_ids:
                if cb := self._listeners.get(child_id):
                    cb(new_state)

    async def login(self, email: str, password: str) -> None:
        """Authenticate with the Axenco API and retrieve authentication tokens.

        Raises:
            email: The email address used for authentication.
            password: The password used for authentication.
            aiohttp.ClientError: If there is an HTTP error during the request.
            TimeoutError: If the request times out.
            ValueError: If the response is invalid.

        """
        url = f"{API_BASE}/v1/auth/login"
        data = {"email": email, "password": password}
        headers = {
            "application": "home-assistant",
            "application-version": "1.0.0",
            "source-type": "plugin",
            "source-id": self.source_id
        }

        try:
            async with async_timeout.timeout(10):
                response = await self.session.post(url, json=data, headers=headers)
                response.raise_for_status()
                result = await response.json()

                if "token" not in result or "refresh_token" not in result or "id" not in result:
                    raise ValueError("PyAxencoAPI : Unexpected response format")  # noqa: TRY301

                self.token = result["token"]
                self.refresh_token = result["refresh_token"]
                if not self.user_id:
                    self.user_id = result["id"]

        except aiohttp.ClientError as err:
            _LOGGER.error("PyAxencoAPI : HTTP error during login: %s", err)
            raise
        except TimeoutError as err:
            _LOGGER.error("PyAxencoAPI : Timeout error during login: %s", err)
            raise
        except ValueError as err:
            _LOGGER.error("PyAxencoAPI : Invalid response during login: %s", err)
            raise

    async def logout(self) -> None:
        """Log out from the Axenco API and clear session data."""
        # Disconnect WebSocket
        await self.sio.disconnect()

        # Invalidate token on server
        if self.token:
            url = f"{API_BASE}/v1/auth/logout"
            headers = self.get_auth_headers()
            try:
                async with self.session.delete(url, headers=headers) as response:
                    response.raise_for_status()
                    _LOGGER.debug("PyAxencoAPI : Logged out successfully from server")
            except aiohttp.ClientError as err:
                _LOGGER.error("PyAxencoAPI : HTTP error during logout: %s", err)
            except TimeoutError as err:
                _LOGGER.error("PyAxencoAPI : Timeout during logout: %s", err)

        # Clear local state
        self.token = None
        self.refresh_token = None
        self.user_id = None
        self._listeners.clear()

    async def refresh_auth_token(self) -> None:
        """Refresh the authentication token using the refresh token."""
        if not self.refresh_token:
            raise ValueError("PyAxencoAPI : Missing refresh token")

        url = f"{API_BASE}/v1/auth/token"
        headers = {
            "application": "home-assistant",
            "application-version": "1.0.0",
            "source-type": "plugin",
            "source-id": self.source_id,
            "Authorization": f"Bearer {self.refresh_token}"
        }

        async with async_timeout.timeout(10):
            response = await self.session.post(url, headers=headers)
            response.raise_for_status()
            result = await response.json()
            if "token" not in result:
                raise ValueError("PyAxencoAPI : Invalid refresh response")

            self.token = result["token"]
            _LOGGER.debug("PyAxencoAPI : Token successfully refreshed")

    def get_auth_headers(self) -> dict[str, str]:
        """Generate and return the authentication headers for API requests.

        Returns:
            A dictionary containing the authentication headers.

        """
        return {
            "Authorization": f"Bearer {self.token}",
            "application": "home-assistant",
            "source-type": "plugin",
            "source-id": self.source_id
        }

    @auto_refresh_token
    async def get_devices(self, force: bool = False) -> list[dict]:
        """Retrieve the list of devices associated with the authenticated user.

        Args:
            force: Whether to force a refresh of the device list.

        Returns:
            A list of devices if successful, or an empty list if an error occurs.

        """
        if not force and time.time() - self._last_fetch < 300:
            return self._devices_cache

        url = f"{API_BASE}/v1/users/{self.user_id}/devices"
        headers = self.get_auth_headers()

        try:
            async with self.session.get(url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                self._devices_cache = data
                self._last_fetch = time.time()
                return data
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                raise
            _LOGGER.error("PyAxencoAPI : HTTP error while retrieving devices: %s", e)
            return None

        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            _LOGGER.error("PyAxencoAPI : Error while retrieving devices: %s", e)
            return None

    @auto_refresh_token
    async def get_device_state(self, device_id: str) -> dict | None:
        """Retrieve the state of a specific device.

        Args:
            device_id: The unique identifier of the device.

        Returns:
            The state of the device if successful, or None if an error occurs.

        """
        url = f"{API_BASE}/v1/devices/{device_id}"
        headers = self.get_auth_headers()

        try:
            async with self.session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                raise
            _LOGGER.error("PyAxencoAPI : HTTP error while retrieving device %s: %s", device_id, e)
            return None

        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            _LOGGER.error("PyAxencoAPI : Error while retrieving device %s: %s", device_id, e)
            return None

    @auto_refresh_token
    async def get_sub_device_state(self, gateway_id: str) -> dict | None:
        """Retrieve the state of a specific sub device.

        Args:
            gateway_id: The unique identifier of the gateway device.

        Returns:
            The state of the device if successful, or None if an error occurs.

        """
        url = f"{API_BASE}/v1/devices/{gateway_id}/sub-devices"
        headers = self.get_auth_headers()

        try:
            async with self.session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                raise
            _LOGGER.error("PyAxencoAPI : HTTP error while retrieving device %s: %s", gateway_id, e)
            return None

        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            _LOGGER.error("PyAxencoAPI : Error while retrieving device %s: %s", gateway_id, e)
            return None

    @auto_refresh_token
    async def set_device_temperature(self, device_id: str, temperature: float) -> None:
        """Set the temperature of a specific device.

        Args:
            device_id: The unique identifier of the device.
            temperature: The desired temperature to set for the device.

        Raises:
            aiohttp.ClientError: If there is an HTTP error during the request.
            aiohttp.ClientResponseError: If the server returns an error response.

        """
        url = f"{API_BASE}/v1/devices/{device_id}/state"
        headers = self.get_auth_headers()
        payload = {
            "parameters": {
                "overrideTemp": temperature
            }
        }
        async with self.session.patch(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()

    @auto_refresh_token
    async def set_sub_device_temperature(self, gateway_id: str, device_rfid: str, temperature: float) -> None:
        """Set the temperature of a specific sub device.

        Args:
            gateway_id: The unique identifier of the gateway device.
            device_rfid: The RFID of the sub device.
            temperature: The desired temperature to set for the sub device.

        Raises:
            aiohttp.ClientError: If there is an HTTP error during the request.
            aiohttp.ClientResponseError: If the server returns an error response.

        """
        url = f"{API_BASE}/v1/devices/{gateway_id}/sub-devices/state"
        headers = self.get_auth_headers()
        payload = {
            "parameters": {
                device_rfid: {
                    "targetTemp": temperature
                }
            }
        }
        async with self.session.patch(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()

    @auto_refresh_token
    async def set_device_mode(self, device_id: str, mode_code: int) -> None:
        """Set the mode of a specific device.

        Args:
            device_id: The unique identifier of the device.
            mode_code: The code representing the desired mode to set for the device.

        Raises:
            aiohttp.ClientError: If there is an HTTP error during the request.
            aiohttp.ClientResponseError: If the server returns an error response.

        """
        url = f"{API_BASE}/v1/devices/{device_id}/state"
        headers = self.get_auth_headers()
        payload = {
            "parameters": {
                "targetMode": mode_code
            }
        }
        async with self.session.patch(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()

    @auto_refresh_token
    async def set_sub_device_mode(self, gateway_id: str, device_rfid: str, mode_code: int) -> None:
        """Set the mode of a specific sub device.

        Args:
            gateway_id: The unique identifier of the gateway device.
            device_rfid: The RFID of the sub device.
            mode_code: The code representing the desired mode to set for the device.

        Raises:
            aiohttp.ClientError: If there is an HTTP error during the request.
            aiohttp.ClientResponseError: If the server returns an error response.

        """
        url = f"{API_BASE}/v1/devices/{gateway_id}/sub-devices/state"
        headers = self.get_auth_headers()
        payload = {
            "parameters": {
                device_rfid: {
                    "targetMode": mode_code
                }
            }
        }
        async with self.session.patch(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()

    @auto_refresh_token
    async def set_sub_device_mode_ufh(self, gateway_id: str, device_rfid: str, mode_code: int) -> None:
        """Set the mode of a specific sub device.

        Args:
            gateway_id: The unique identifier of the gateway device.
            device_rfid: The RFID of the sub device.
            mode_code: The code representing the desired mode to set for the device.

        Raises:
            aiohttp.ClientError: If there is an HTTP error during the request.
            aiohttp.ClientResponseError: If the server returns an error response.

        """
        url = f"{API_BASE}/v1/devices/{gateway_id}/sub-devices/state"
        headers = self.get_auth_headers()
        payload = {
            "parameters": {
                device_rfid: {
                    "changeOverUser": mode_code
                }
            }
        }
        async with self.session.patch(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()

    @auto_refresh_token
    async def set_device_program(self, device_id: str, program_data: dict) -> None:
        """Set the weekly program of a specific device.

        Args:
            device_id: The unique identifier of the device.
            program_data: The program data to set for the device.

        Raises:
            aiohttp.ClientError: If there is an HTTP error during the request.
            aiohttp.ClientResponseError: If the server returns an error response.

        """
        url = f"{API_BASE}/v1/devices/{device_id}/program"
        headers = self.get_auth_headers()
        payload = {
            "data": program_data,
            "redundancy": "weekly"
        }
        async with self.session.patch(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
