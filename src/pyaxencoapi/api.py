"""Typed REST and push client; the caller owns the HTTP session."""

import asyncio
import logging
import re
import sys
import time
import warnings
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

import aiohttp
import socketio

from .exceptions import (
    AxencoAuthError,
    AxencoConnectionError,
    AxencoError,
    AxencoResponseError,
)
from .models import (
    UNSET,
    AxencoDevice,
    AxencoEvent,
    AxencoState,
    AxencoStatePatch,
    ConnectionChanged,
    DeviceAdded,
    DeviceRemoved,
    DeviceUpdated,
)

_LOGGER = logging.getLogger(__name__)
API_BASE = "https://user-ep.imhotepcreation.com"
REQUEST_TIMEOUT = 10


class PyAxencoAPI:
    """Axenco client with immutable snapshots and synchronous push subscribers."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.source_id = "myneomitis"
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None
        self.sio = socketio.AsyncClient(http_session=session, request_timeout=10)
        self._devices_cache: dict[str, AxencoDevice] = {}
        self._last_fetch: float | None = None
        self._refresh_lock = asyncio.Lock()
        self._refresh_generation = 0
        self._refresh_last_error: AxencoError | None = None
        self._refresh_failure: AxencoAuthError | None = None
        self._connection_error: AxencoError | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._inventory_lock = asyncio.Lock()
        self._subscribers: dict[object, Callable[[AxencoEvent], None]] = {}
        self._connected = False
        self._closing = False
        self._handlers: set[asyncio.Task[Any]] = set()
        self._connect_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._handshake_token: str | None = None
        self._socket_refreshed = False
        self._socket_refresh_token: str | None = None
        self._register_handlers()

    def subscribe(self, callback: Callable[[AxencoEvent], None]) -> Callable[[], None]:
        """Subscribe until the idempotent returned function is called."""
        key = object()
        self._subscribers[key] = callback

        def unsubscribe() -> None:
            self._subscribers.pop(key, None)

        return unsubscribe

    def _emit(self, event: AxencoEvent) -> None:
        if self._closing and not isinstance(event, ConnectionChanged):
            return
        for key, callback in tuple(self._subscribers.items()):
            if key not in self._subscribers:
                continue
            try:
                callback(event)
            except Exception:  # noqa: BLE001
                _LOGGER.error("Axenco subscriber failed")

    def _transport(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            self._emit(ConnectionChanged(connected=connected))

    def _register_handlers(self) -> None:
        async def connect() -> None:
            self._socket_refreshed = False
            self._socket_refresh_token = None
            self._connection_error = None
            try:
                await self.get_devices(force=True)
            except AxencoError as err:
                if self._closing:
                    return
                self._connection_error = err
                self._transport(False)
                if isinstance(err, AxencoAuthError):
                    self._emit(ConnectionChanged(connected=False, error=err))
                if not isinstance(err, AxencoAuthError) and (
                    self._recovery_task is None or self._recovery_task.done()
                ):
                    self._recovery_task = asyncio.create_task(self._recover_inventory())
                self._ready.set()
                return
            if self._closing:
                return
            self._transport(True)
            self._ready.set()

        async def connect_error(data: object) -> None:
            failure = sys.exception()
            rejected = isinstance(data, Mapping) and "message" in data
            cause = failure
            while cause is not None:
                if isinstance(cause, aiohttp.ClientResponseError):
                    rejected = cause.status in (401, 403)
                    break
                cause = cause.__cause__ or cause.__context__
            if isinstance(data, str) and re.search(
                r"\bUnexpected status code (401|403)\b", data
            ):
                rejected = True
            self._connection_error = None
            if not rejected:
                self._connection_error = AxencoConnectionError(
                    "WebSocket connection failed"
                )
                self._connection_error.__cause__ = (
                    failure
                    or socketio.exceptions.ConnectionError("WebSocket transport failed")
                )
            elif self._socket_refreshed:
                self._connection_error = AxencoAuthError(
                    "WebSocket credentials rejected"
                )
                self._connection_error.__cause__ = socketio.exceptions.ConnectionError(
                    "WebSocket handshake rejected"
                )
            else:
                try:
                    await self._refresh(self._handshake_token)
                except AxencoError as err:
                    self._connection_error = err
                else:
                    self._socket_refreshed = True
                    self._socket_refresh_token = self.token
            if self._closing:
                return
            if self._connection_error is not None:
                self._transport(False)
                self._emit(
                    ConnectionChanged(connected=False, error=self._connection_error)
                )
                if isinstance(self._connection_error, AxencoAuthError):
                    self.sio.reconnection = False
                    if self.sio._reconnect_abort is not None:
                        self.sio._reconnect_abort.set()

        async def disconnect(*args: object) -> None:
            self._transport(False)

        async def update(data: object) -> None:
            envelope = self._object(data)
            device_id = envelope.get("objectId")
            if not isinstance(device_id, str) or not isinstance(
                envelope.get("data"), Mapping
            ):
                await self.get_devices(force=True)
                return
            await self.notify_update(device_id, envelope["data"])

        async def update_ext(data: object) -> None:
            envelope = self._object(data)
            reconciled = False
            device_id = envelope.get("objectId")
            payload = envelope.get("data")
            if isinstance(device_id, str) and isinstance(payload, Mapping):
                parent = self._devices_cache.get(device_id)
                known = {
                    device.rfid
                    for device in self._devices_cache.values()
                    if parent is not None and parent.rfid in device.parents
                }
                if parent is not None and not parent.parents and payload.keys() - known:
                    await self.get_devices(force=True)
                    reconciled = True
                    parent = self._devices_cache.get(device_id)
                matches = [
                    device
                    for device in self._devices_cache.values()
                    if parent is not None
                    and parent.rfid in device.parents
                    and device.rfid in payload
                ]
                if matches:
                    for child in matches:
                        await self.notify_update(child.id, payload[child.rfid])
                    return
                if parent is not None and parent.parents:
                    await self.notify_update(parent.id, payload)
                    return
            if not reconciled:
                await self.get_devices(force=True)

        async def reconcile(data: object) -> None:
            await self.get_devices(force=True)

        async def unlink(data: object) -> None:
            envelope = self._object(data)
            device_id = envelope.get("objectId")
            if isinstance(device_id, str) and device_id:
                await self.notify_removal(device_id)
            else:
                await self.get_devices(force=True)

        def register(name: str, handler: Callable[..., Awaitable[None]]) -> None:
            async def tracked(*args: object) -> None:
                if self._closing:
                    return
                task = asyncio.current_task()
                assert task is not None
                self._handlers.add(task)
                try:
                    await handler(*args)
                finally:
                    self._handlers.discard(task)

            self.sio.on(name, tracked)

        register("connect", connect)
        register("connect_error", connect_error)
        register("disconnect", disconnect)
        for name in ("setState", "update"):
            register(name, update)
        for name in ("updateExtDevState", "setExtDevState"):
            register(name, update_ext)
        for name in ("link", "discover", "setProgram"):
            register(name, reconcile)
        register("unlink", unlink)

    async def connect_websocket(self) -> None:
        """Connect with current credentials, failing explicitly on setup errors."""
        if self._connect_task is not None and not self._connect_task.done():
            await asyncio.shield(self._connect_task)
            return
        if self._connected and not self._closing:
            return
        self._closing = False
        self._ready.clear()
        self._connection_error = None
        self.sio.reconnection = True
        self._connect_task = asyncio.create_task(self._connect_socket())
        try:
            await self._connect_task
        except asyncio.CancelledError as err:
            if self._closing:
                raise AxencoConnectionError("WebSocket connection closed") from err
            await self.disconnect_websocket()
            raise
        except AxencoError:
            await self.disconnect_websocket()
            raise
        except (
            socketio.exceptions.ConnectionError,
            aiohttp.ClientError,
            TimeoutError,
        ) as err:
            failure = self._connection_error
            await self.disconnect_websocket()
            if failure is not None:
                raise failure from err
            raise AxencoConnectionError("WebSocket connection failed") from err

    def _socket_headers(self) -> dict[str, str]:
        self._handshake_token = self.token
        if self.token != self._socket_refresh_token:
            self._socket_refreshed = False
        return self.get_auth_headers()

    async def _connect_socket(self) -> None:
        async with asyncio.timeout(45):
            for attempt in range(2):
                try:
                    await self.sio.connect(
                        lambda: f"{API_BASE}?{urlencode({'userId': self.user_id})}",
                        socketio_path="socket.io-v2",
                        transports=["websocket"],
                        headers=self._socket_headers,
                        wait_timeout=10,
                    )
                except socketio.exceptions.ConnectionError:
                    if (
                        attempt == 0
                        and self._socket_refreshed
                        and self._connection_error is None
                        and not self._closing
                    ):
                        continue
                    raise
                break
            await self._ready.wait()
            if self._closing:
                raise AxencoConnectionError("WebSocket connection closed")
            if self._connection_error is not None:
                raise self._connection_error

    async def disconnect_websocket(self) -> None:
        """Stop reconnection tasks without closing the caller's HTTP session."""
        self._closing = True
        self.sio.reconnection = False
        current = asyncio.current_task()
        tasks = set(self._handlers)
        if self._connect_task is not None:
            tasks.add(self._connect_task)
        tasks.discard(current)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            await asyncio.gather(self._recovery_task, return_exceptions=True)
            self._recovery_task = None
        await self.sio.disconnect()
        await self.sio.shutdown()
        # Namespace handlers can finish after Socket.IO's first shutdown check.
        while self.sio.connected or self.sio._reconnect_task is not None:
            await self.sio.disconnect()
            await self.sio.shutdown()
            if self.sio.connected and self.sio.eio.state == "disconnected":
                await self.sio._handle_eio_disconnect(self.sio.reason.CLIENT_DISCONNECT)
            if self.sio._reconnect_task is not None and self.sio._reconnect_task.done():
                self.sio._reconnect_task = None
        self._transport(False)

    async def _recover_inventory(self) -> None:
        """Retry only failed reconnect reconciliation, never periodic polling."""
        delay = 1
        while not self._closing:
            await asyncio.sleep(delay)
            if self._closing or not self.sio.connected:
                return
            try:
                await self.get_devices(force=True)
            except AxencoAuthError as err:
                if not self._closing:
                    self._connection_error = err
                    self._emit(ConnectionChanged(connected=False, error=err))
                return
            except AxencoConnectionError, AxencoResponseError:
                delay = min(delay * 2, 60)
                continue
            if self._closing or not self.sio.connected:
                return
            self._connection_error = None
            self._transport(True)
            return

    @staticmethod
    def _object(value: object) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            err = TypeError("Expected JSON object")
            raise AxencoResponseError("Invalid response object") from err
        return value

    @staticmethod
    def _identifier(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            err = ValueError("Expected nonempty identifier")
            raise AxencoResponseError("Invalid identifier") from err
        return value

    def get_auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "application": "home-assistant",
            "application-version": "1.0.0",
            "source-type": "plugin",
            "source-id": self.source_id,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: object = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
        read_json: bool = True,
    ) -> object:
        token = self.token
        for attempt in range(2):
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with self.session.request(
                        method,
                        f"{API_BASE}{path}",
                        json=payload,
                        headers=headers
                        if headers is not None
                        else self.get_auth_headers(),
                    ) as response:
                        response.raise_for_status()
                        return await response.json() if read_json else None
            except aiohttp.ClientResponseError as err:
                if err.status == 401 and authenticated and attempt == 0:
                    await self._refresh(token)
                    continue
                if err.status in (401, 403):
                    raise AxencoAuthError("Authentication rejected") from err
                raise AxencoResponseError("HTTP request rejected") from err
            except (aiohttp.ClientError, TimeoutError) as err:
                raise AxencoConnectionError("Network request failed") from err
            except (ValueError, TypeError) as err:
                raise AxencoResponseError("Invalid JSON response") from err
        raise AssertionError("Unreachable retry state")

    async def login(self, email: str, password: str) -> None:
        headers = self.get_auth_headers()
        del headers["Authorization"]
        data = self._object(
            await self._request(
                "POST",
                "/v1/auth/login",
                payload={"email": email, "password": password},
                headers=headers,
                authenticated=False,
            )
        )
        token = self._identifier(data.get("token"))
        refresh = self._identifier(data.get("refresh_token"))
        user_id = self._identifier(data.get("id"))
        self.token, self.refresh_token, self.user_id = token, refresh, user_id
        self._refresh_failure = None
        self._refresh_last_error = None
        self._refresh_generation += 1

    async def _refresh(self, expired_token: str | None) -> None:
        generation = self._refresh_generation
        async with self._refresh_lock:
            if self.token != expired_token:
                return
            if (
                generation != self._refresh_generation
                and self._refresh_last_error is not None
            ):
                raise self._refresh_last_error
            if self._refresh_failure is not None:
                raise self._refresh_failure
            if not self.refresh_token:
                raise AxencoAuthError("Missing refresh token")
            # The endpoint expects the expired access token plus the refresh token.
            headers = self.get_auth_headers()
            headers["refresh-token"] = self.refresh_token
            try:
                response = await self._request(
                    "GET",
                    "/v1/auth/token",
                    headers=headers,
                    authenticated=False,
                )
                data = self._object(response)
                token = self._identifier(data.get("token"))
                refresh = (
                    self._identifier(data["refresh_token"])
                    if "refresh_token" in data
                    else self.refresh_token
                )
            except AxencoError as err:
                self._refresh_generation += 1
                self._refresh_last_error = err
                if isinstance(err, AxencoAuthError):
                    self._refresh_failure = err
                raise
            self.token, self.refresh_token = token, refresh
            self._refresh_generation += 1
            self._refresh_last_error = None

    async def refresh_auth_token(self) -> None:
        await self._refresh(self.token)

    async def logout(self) -> None:
        try:
            await self.disconnect_websocket()
            if self.token:
                await self._request("POST", "/v1/auth/logout", read_json=False)
        finally:
            self.token = self.refresh_token = self.user_id = None
            self._subscribers.clear()
            self._devices_cache.clear()
            self._last_fetch = None
            self._refresh_failure = None
            self._refresh_last_error = None
            self._connection_error = None

    async def get_devices(self, force: bool = False) -> list[AxencoDevice]:
        requested = time.monotonic()
        async with self._inventory_lock:
            if force and self._last_fetch is not None and self._last_fetch >= requested:
                return list(self._devices_cache.values())
            if (
                not force
                and self._last_fetch is not None
                and time.monotonic() - self._last_fetch < 300
            ):
                return list(self._devices_cache.values())
            data = self._object(
                await self._request("GET", f"/v1/users/{self.user_id}/devices")
            )
            raw = data.get("devices")
            if not isinstance(raw, list):
                raise AxencoResponseError("Invalid inventory") from TypeError(
                    "Expected devices list"
                )
            devices = [AxencoDevice.from_json(item) for item in raw]
            inventory = {device.id: device for device in devices}
            if len(inventory) != len(devices):
                raise AxencoResponseError(
                    "Duplicate device identifiers"
                ) from ValueError("Duplicate identifier")
            previous = self._devices_cache
            self._devices_cache = inventory
            self._last_fetch = time.monotonic()
            for device_id in previous.keys() - inventory.keys():
                self._emit(DeviceRemoved(device_id=device_id))
            for device in devices:
                old = previous.get(device.id)
                if old is None or (old.model, old.name, old.parents, old.rfid) != (
                    device.model,
                    device.name,
                    device.parents,
                    device.rfid,
                ):
                    self._emit(DeviceAdded(device=device))
                elif old != device:
                    patch = AxencoStatePatch(
                        **{
                            field: getattr(device.state, field)
                            for field in AxencoState.__dataclass_fields__
                        }
                    )
                    self._emit(
                        DeviceUpdated(
                            device_id=device.id, patch=patch, connected=device.connected
                        )
                    )
            return devices

    async def get_device_state(self, device_id: str) -> AxencoState:
        data = self._object(await self._request("GET", f"/v1/devices/{device_id}"))
        return AxencoState.from_json(data.get("state", data))

    @staticmethod
    def _extract_gateway_id(device_parents: str) -> str:
        parts = [part.strip() for part in device_parents.split(",") if part.strip()]
        if not parts:
            raise AxencoResponseError("Invalid parent chain") from ValueError(
                "Empty parent chain"
            )
        return parts[0]

    async def get_sub_device_state(self, device_parents: str) -> Mapping[str, object]:
        """Return a validated object; the endpoint's inner schema is not yet fixed."""
        gateway = self._extract_gateway_id(device_parents)
        return self._object(
            await self._request("GET", f"/v1/devices/{gateway}/sub-devices")
        )

    async def notify_update(self, device_id: str, new_state: object) -> None:
        data = self._object(new_state)
        if device_id not in self._devices_cache:
            await self.get_devices(force=True)
        async with self._inventory_lock:
            self._apply_update(device_id, data)

    def _apply_update(self, device_id: str, data: Mapping[str, Any]) -> None:
        if device_id not in self._devices_cache:
            return
        connected = data.get("connected", UNSET)
        if (
            connected is not UNSET
            and connected is not None
            and type(connected) is not bool
        ):
            raise AxencoResponseError("Invalid connected") from TypeError(
                "Expected boolean"
            )
        patch = AxencoStatePatch.from_json(data.get("state", data))
        device = self._devices_cache[device_id]
        self._devices_cache[device_id] = replace(
            device,
            state=patch.apply(device.state),
            connected=device.connected if connected is UNSET else connected,
        )
        self._emit(DeviceUpdated(device_id=device_id, patch=patch, connected=connected))

    def is_device_available(self, device_id: str) -> bool:
        """Derive physical availability without replacing child connection facts."""
        device = self._devices_cache.get(device_id)
        if device is None or device.connected is not True:
            return False
        parents = set(device.parents)
        visited = {device.id}
        while parents:
            rfid = parents.pop()
            parent = next(
                (item for item in self._devices_cache.values() if item.rfid == rfid),
                None,
            )
            if parent is None or parent.connected is not True:
                return False
            if parent.id not in visited:
                visited.add(parent.id)
                parents.update(parent.parents)
        return True

    async def notify_discovery(self, device: object) -> None:
        await self.get_devices(force=True)

    async def notify_removal(self, device_id: str) -> None:
        async with self._inventory_lock:
            if self._devices_cache.pop(device_id, None) is not None:
                self._emit(DeviceRemoved(device_id=device_id))

    def register_listener(
        self, device_id: str, callback: Callable[[dict[str, object]], None]
    ) -> Callable[[], None]:
        """Deprecated protocol-state adapter, without ambiguous child-state routing."""
        warnings.warn("Use subscribe()", DeprecationWarning, stacklevel=2)
        from .models import _STATE_KEYS

        def listener(event: AxencoEvent) -> None:
            if isinstance(event, DeviceUpdated) and event.device_id == device_id:
                data = {
                    key: getattr(event.patch, field)
                    for field, key in _STATE_KEYS.items()
                    if getattr(event.patch, field) is not UNSET
                }
                if event.connected is not UNSET:
                    data["connected"] = event.connected
                callback(data)

        return self.subscribe(listener)

    def register_discovery_callback(
        self, callback: Callable[[dict[str, object]], None]
    ) -> Callable[[], None]:
        warnings.warn("Use subscribe()", DeprecationWarning, stacklevel=2)
        from .models import _STATE_KEYS

        def listener(event: AxencoEvent) -> None:
            if isinstance(event, DeviceAdded):
                device = event.device
                callback(
                    {
                        "_id": device.id,
                        "model": device.model,
                        "name": device.name,
                        "connected": device.connected,
                        "rfid": device.rfid,
                        "parents": "," + ",".join(device.parents) + ","
                        if device.parents
                        else "",
                        "state": {
                            key: getattr(device.state, field)
                            for field, key in _STATE_KEYS.items()
                        },
                    }
                )

        return self.subscribe(listener)

    def register_removal_callback(
        self, callback: Callable[[str], None]
    ) -> Callable[[], None]:
        warnings.warn("Use subscribe()", DeprecationWarning, stacklevel=2)

        def listener(event: AxencoEvent) -> None:
            if isinstance(event, DeviceRemoved):
                callback(event.device_id)

        return self.subscribe(listener)

    async def _command(
        self, device_id: str, parameters: object, *, sub: bool = False
    ) -> None:
        suffix = "/sub-devices/state" if sub else "/state"
        await self._request(
            "PATCH",
            f"/v1/devices/{device_id}{suffix}",
            payload={"parameters": parameters},
            read_json=False,
        )

    async def set_device_temperature(self, device_id: str, temperature: float) -> None:
        await self._command(device_id, {"overrideTemp": temperature})

    async def set_device_mode(self, device_id: str, mode_code: int) -> None:
        await self._command(device_id, {"targetMode": mode_code})

    async def set_sub_device_temperature(
        self, device_parents: str, device_rfid: str, temperature: float
    ) -> None:
        await self._command(
            self._extract_gateway_id(device_parents),
            {device_rfid: {"targetTemp": temperature}},
            sub=True,
        )

    async def set_sub_device_mode(
        self, device_parents: str, device_rfid: str, mode_code: int
    ) -> None:
        await self._command(
            self._extract_gateway_id(device_parents),
            {device_rfid: {"targetMode": mode_code}},
            sub=True,
        )

    async def set_sub_device_mode_ufh(
        self, device_parents: str, device_rfid: str, mode_code: int
    ) -> None:
        await self._command(
            self._extract_gateway_id(device_parents),
            {device_rfid: {"changeOverUser": mode_code}},
            sub=True,
        )

    async def set_device_program(
        self, device_id: str, program_data: Mapping[str, object]
    ) -> None:
        await self._request(
            "PATCH",
            f"/v1/devices/{device_id}/program",
            payload={"data": dict(program_data), "redundancy": "weekly"},
            read_json=False,
        )
