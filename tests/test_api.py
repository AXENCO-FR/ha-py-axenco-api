"""Exercise the client against real aiohttp and Socket.IO servers."""

import asyncio
import gc
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import engineio
import pytest
import socketio
from aiohttp import web

from pyaxencoapi import (
    UNSET,
    AxencoAuthError,
    AxencoConnectionError,
    AxencoDevice,
    AxencoEvent,
    AxencoResponseError,
    ConnectionChanged,
    DeviceAdded,
    DeviceRemoved,
    DeviceUpdated,
    PyAxencoAPI,
)
from pyaxencoapi.utils import find_childs, get_rfid_by_id


@dataclass
class Server:
    inventory: list[dict[str, object]] = field(
        default_factory=lambda: [
            {
                "_id": "parent",
                "model": "RT7",
                "rfid": "p",
                "connected": True,
                "state": {"targetTemp": 20},
            },
            {
                "_id": "child",
                "model": "UFH",
                "parents": ",p,",
                "rfid": "c",
                "connected": True,
                "state": {"targetTemp": 18},
            },
        ]
    )
    calls: list[tuple[str, str, object, str | None]] = field(default_factory=list)
    status: int = 200
    invalid: object = None
    refresh_count: int = 0
    refresh_status: int = 200
    refresh_payload: dict[str, str] = field(
        default_factory=lambda: {"token": "new", "refresh_token": "rotated"}
    )
    socket_status: int = 200
    delay: float = 0
    login_payload: object = field(
        default_factory=lambda: {
            "token": "old",
            "refresh_token": "refresh",
            "id": "user",
        }
    )
    sio: Any = field(default_factory=lambda: socketio.AsyncServer(async_mode="aiohttp"))

    async def handler(self, request: web.Request) -> web.Response:
        payload = await request.json() if request.can_read_body else None
        self.calls.append(
            (
                request.method,
                request.path,
                payload,
                request.headers.get("Authorization"),
            )
        )
        if request.path == "/v1/auth/login":
            return web.json_response(self.login_payload)
        if request.path == "/v1/auth/token":
            self.refresh_count += 1
            await asyncio.sleep(0.02)
            return web.json_response(self.refresh_payload, status=self.refresh_status)
        if request.headers.get("Authorization") == "Bearer expired":
            return web.Response(status=401)
        await asyncio.sleep(self.delay)
        if self.status != 200:
            return web.Response(status=self.status)
        if self.invalid is not None:
            return web.json_response(self.invalid)
        if request.path.endswith("/devices"):
            return web.json_response({"devices": self.inventory})
        if request.path.endswith("/sub-devices"):
            return web.json_response({"c": {"targetTemp": 19}})
        if request.method == "GET":
            return web.json_response({"state": {"targetTemp": 21}})
        return web.Response(status=204)


@pytest.fixture
async def server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Server]:
    state = Server()

    @web.middleware
    async def handshake_status(
        request: web.Request, handler: Any
    ) -> web.StreamResponse:
        if request.path.startswith("/socket.io-v2") and state.socket_status != 200:
            return web.Response(status=state.socket_status)
        return await handler(request)

    app = web.Application(middlewares=[handshake_status])
    state.sio.attach(app, socketio_path="socket.io-v2")
    app.router.add_route("*", "/v1/{tail:.*}", state.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setattr("pyaxencoapi.api.API_BASE", f"http://127.0.0.1:{port}")
    yield state
    await state.sio.shutdown()
    await runner.cleanup()


@pytest.fixture
async def api_client(server: Server) -> AsyncIterator[PyAxencoAPI]:
    async with aiohttp.ClientSession() as session:
        api = PyAxencoAPI(session)
        await api.login("user@example.test", "secret")
        yield api
        await api.disconnect_websocket()


async def test_inventory_cache(api_client: PyAxencoAPI, server: Server) -> None:
    devices = await api_client.get_devices()
    assert isinstance(devices[0], AxencoDevice)
    devices.clear()
    assert len(await api_client.get_devices()) == 2
    assert len(server.calls) == 2
    assert find_childs(await api_client.get_devices(), "p") == ["child"]
    assert get_rfid_by_id(await api_client.get_devices(), "parent") == "p"
    await api_client.get_devices(force=True)
    assert len(server.calls) == 3


@pytest.mark.parametrize(
    "status,error",
    [
        pytest.param(403, AxencoAuthError, id="auth"),
        pytest.param(500, AxencoResponseError, id="server"),
    ],
)
async def test_http_errors(
    api_client: PyAxencoAPI, server: Server, status: int, error: type[Exception]
) -> None:
    server.status = status
    with pytest.raises(error) as raised:
        await api_client.get_devices()
    assert isinstance(raised.value.__cause__, aiohttp.ClientResponseError)
    assert api_client._last_fetch is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="not-object"),
        pytest.param({}, id="missing-list"),
        pytest.param({"devices": [{"_id": "bad"}]}, id="incomplete"),
        pytest.param(
            {"devices": [{"_id": "a", "model": "x", "state": {"targetTemp": "bad"}}]},
            id="state",
        ),
    ],
)
async def test_invalid_inventory(
    api_client: PyAxencoAPI, server: Server, payload: object
) -> None:
    server.invalid = payload
    with pytest.raises(AxencoResponseError) as raised:
        await api_client.get_devices()
    assert raised.value.__cause__ is not None


async def test_empty_inventory(api_client: PyAxencoAPI, server: Server) -> None:
    server.inventory = []
    assert await api_client.get_devices() == []
    assert await api_client.get_devices() == []
    assert len(server.calls) == 2


async def test_concurrent_refresh(api_client: PyAxencoAPI, server: Server) -> None:
    api_client.token = "expired"
    results = await asyncio.gather(
        *(api_client.get_device_state("parent") for _ in range(10))
    )
    assert all(state.target_temperature == 21 for state in results)
    assert server.refresh_count == 1
    assert api_client.token == "new"
    assert api_client.refresh_token == "rotated"


async def test_refresh_rejected(api_client: PyAxencoAPI, server: Server) -> None:
    api_client.token = "expired"
    server.refresh_status = 401
    with pytest.raises(AxencoAuthError) as raised:
        await api_client.get_device_state("parent")
    assert isinstance(raised.value.__cause__, aiohttp.ClientResponseError)
    assert server.refresh_count == 1


@pytest.mark.parametrize(
    "command,args,path,payload",
    [
        pytest.param(
            "set_device_temperature",
            ("parent", 22.5),
            "/v1/devices/parent/state",
            {"parameters": {"overrideTemp": 22.5}},
            id="temperature",
        ),
        pytest.param(
            "set_device_mode",
            ("parent", 2),
            "/v1/devices/parent/state",
            {"parameters": {"targetMode": 2}},
            id="mode",
        ),
        pytest.param(
            "set_sub_device_temperature",
            (",gateway,p,", "c", 20),
            "/v1/devices/gateway/sub-devices/state",
            {"parameters": {"c": {"targetTemp": 20}}},
            id="child-temperature",
        ),
        pytest.param(
            "set_sub_device_mode",
            (",gateway,p,", "c", 4),
            "/v1/devices/gateway/sub-devices/state",
            {"parameters": {"c": {"targetMode": 4}}},
            id="child-mode",
        ),
        pytest.param(
            "set_sub_device_mode_ufh",
            (",gateway,p,", "c", 1),
            "/v1/devices/gateway/sub-devices/state",
            {"parameters": {"c": {"changeOverUser": 1}}},
            id="ufh",
        ),
        pytest.param(
            "set_device_program",
            ("parent", {"monday": []}),
            "/v1/devices/parent/program",
            {"data": {"monday": []}, "redundancy": "weekly"},
            id="program",
        ),
    ],
)
async def test_commands(
    api_client: PyAxencoAPI,
    server: Server,
    command: str,
    args: tuple[object, ...],
    path: str,
    payload: object,
) -> None:
    await getattr(api_client, command)(*args)
    assert server.calls[-1] == ("PATCH", path, payload, "Bearer old")


async def test_child_availability_not_state(api_client: PyAxencoAPI) -> None:
    await api_client.get_devices()
    events: list[AxencoEvent] = []
    api_client.subscribe(events.append)
    await api_client.notify_update("parent", {"targetTemp": 25})
    assert len(events) == 1
    assert (await api_client.get_devices())[1].state.target_temperature == 18
    events.clear()
    await api_client.notify_update("parent", {"connected": False})
    assert events == [
        DeviceUpdated(device_id="parent", connected=False),
    ]
    assert (await api_client.get_devices())[1].connected is True
    assert not api_client.is_device_available("child")
    await api_client.notify_update("parent", {"connected": True})
    assert api_client.is_device_available("child")
    await api_client.notify_update("child", {"targetTemp": None})
    assert (await api_client.get_devices())[1].state.target_temperature is None
    assert isinstance(events[-1], DeviceUpdated)
    assert events[-1].patch.override_temperature is UNSET


async def test_subscriptions(api_client: PyAxencoAPI) -> None:
    await api_client.get_devices()
    first: list[AxencoEvent] = []
    second: list[AxencoEvent] = []
    unsubscribe = api_client.subscribe(first.append)
    api_client.subscribe(second.append)
    await api_client.notify_update("parent", {"targetTemp": 23})
    assert first == second
    unsubscribe()
    unsubscribe()
    await api_client.notify_update("parent", {"targetTemp": 24})
    assert len(first) == 1
    assert len(second) == 2

    class Callback:
        def __call__(self, event: AxencoEvent) -> None:
            pass

    callback = Callback()
    reference = weakref.ref(callback)
    remove = api_client.subscribe(callback)
    del callback
    remove()
    gc.collect()
    assert reference() is None


async def test_reconcile_and_repeated_removal(
    api_client: PyAxencoAPI, server: Server
) -> None:
    await api_client.get_devices()
    events: list[AxencoEvent] = []
    api_client.subscribe(events.append)
    server.inventory = [{"_id": "new", "model": "unknown"}]
    await api_client.sio.handlers["/"]["link"]({"data": {"devices": [{"_id": "new"}]}})
    assert {
        event.device_id for event in events if isinstance(event, DeviceRemoved)
    } == {"parent", "child"}
    assert events[-1] == DeviceAdded(device=AxencoDevice(id="new", model="unknown"))
    events.clear()
    await api_client.sio.handlers["/"]["discover"]({})
    assert events == []
    await api_client.notify_removal("new")
    await api_client.notify_removal("new")
    assert events == [DeviceRemoved(device_id="new")]


async def test_real_socket_lifecycle(api_client: PyAxencoAPI, server: Server) -> None:
    events: list[AxencoEvent] = []
    api_client.subscribe(events.append)
    assert api_client.sio.eio.http is api_client.session
    assert api_client.sio.eio.external_http
    await api_client.connect_websocket()
    assert events[-1] == ConnectionChanged(connected=True)
    delivered = asyncio.Event()

    def received(event: AxencoEvent) -> None:
        if isinstance(event, DeviceUpdated):
            delivered.set()

    api_client.subscribe(received)
    await server.sio.emit(
        "setState", {"objectId": "parent", "data": {"targetTemp": 26}}
    )
    await asyncio.wait_for(delivered.wait(), 2)
    assert (await api_client.get_devices())[0].state.target_temperature == 26
    await api_client.disconnect_websocket()
    assert events[-1] == ConnectionChanged(connected=False)
    assert not api_client.session.closed
    server.inventory[0]["state"] = {"targetTemp": 19}
    await api_client.connect_websocket()
    assert (await api_client.get_devices())[0].state.target_temperature == 19
    await api_client.logout()
    assert not api_client.session.closed
    assert api_client._subscribers == {}
    assert api_client.token is None


async def test_socket_failure(
    api_client: PyAxencoAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = socketio.exceptions.ConnectionError("secret URL")
    monkeypatch.setattr(api_client.sio, "connect", AsyncMock(side_effect=failure))
    with pytest.raises(AxencoConnectionError) as raised:
        await api_client.connect_websocket()
    assert raised.value.__cause__ is failure
    assert "secret" not in str(raised.value)
    assert not api_client.session.closed


async def test_network_failure(
    api_client: PyAxencoAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pyaxencoapi.api.API_BASE", "http://127.0.0.1:1")
    with pytest.raises(AxencoConnectionError) as raised:
        await api_client.get_devices()
    assert isinstance(raised.value.__cause__, aiohttp.ClientError)


async def test_concurrent_failed_refresh(
    api_client: PyAxencoAPI, server: Server
) -> None:
    api_client.token = "expired"
    server.refresh_status = 401
    results = await asyncio.gather(
        *(api_client.get_device_state("parent") for _ in range(5)),
        return_exceptions=True,
    )
    assert all(isinstance(result, AxencoAuthError) for result in results)
    assert server.refresh_count == 1


async def test_rfid_payload_routing(api_client: PyAxencoAPI) -> None:
    await api_client.get_devices()
    events: list[AxencoEvent] = []
    api_client.subscribe(events.append)
    await api_client.sio.handlers["/"]["setExtDevState"](
        {"objectId": "parent", "data": {"c": {"targetTemp": 24}}}
    )
    assert len(events) == 1
    assert isinstance(events[0], DeviceUpdated)
    assert events[0].device_id == "child"
    assert events[0].patch.target_temperature == 24
    assert (await api_client.get_devices())[0].state.target_temperature == 20


async def test_update_during_fetch(api_client: PyAxencoAPI, server: Server) -> None:
    await api_client.get_devices()
    server.delay = 0.05
    fetching = asyncio.create_task(api_client.get_devices(force=True))
    await asyncio.sleep(0.01)
    await api_client.notify_update("parent", {"targetTemp": 27})
    await fetching
    assert (await api_client.get_devices())[0].state.target_temperature == 27


async def test_deadline(
    api_client: PyAxencoAPI, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pyaxencoapi.api.REQUEST_TIMEOUT", 0.01)
    server.delay = 0.05
    with pytest.raises(AxencoConnectionError) as raised:
        await api_client.get_devices()
    assert isinstance(raised.value.__cause__, TimeoutError)
    assert not api_client.session.closed


async def test_failed_reconciliation_keeps_cache(
    api_client: PyAxencoAPI, server: Server
) -> None:
    initial = await api_client.get_devices()
    server.invalid = {"devices": [{"_id": "bad"}]}
    with pytest.raises(AxencoResponseError):
        await api_client.get_devices(force=True)
    assert await api_client.get_devices() == initial


async def test_socket_setup_reconciliation_failure(
    api_client: PyAxencoAPI, server: Server
) -> None:
    server.status = 403
    with pytest.raises(AxencoAuthError):
        await api_client.connect_websocket()
    assert not api_client.sio.connected
    assert not api_client.session.closed


async def test_logout_failure_cleanup(api_client: PyAxencoAPI, server: Server) -> None:
    api_client.subscribe(lambda event: None)
    await api_client.get_devices()
    server.status = 500
    with pytest.raises(AxencoResponseError):
        await api_client.logout()
    assert api_client._subscribers == {}
    assert api_client._devices_cache == {}
    assert api_client.token is None
    assert not api_client.session.closed


async def test_legacy_unsubscribe(api_client: PyAxencoAPI) -> None:
    await api_client.get_devices()
    states: list[dict[str, object]] = []
    with pytest.warns(DeprecationWarning):
        unsubscribe = api_client.register_listener("child", states.append)
    await api_client.notify_update("child", {"targetTemp": None, "connected": False})
    assert states == [{"targetTemp": None, "connected": False}]
    unsubscribe()
    await api_client.notify_update("child", {"targetTemp": 20})
    assert len(states) == 1


async def test_automatic_reconnection(api_client: PyAxencoAPI, server: Server) -> None:
    api_client.sio.reconnection_delay = 0.01
    api_client.sio.randomization_factor = 0
    await api_client.connect_websocket()
    events: list[AxencoEvent] = []
    ready = asyncio.Event()

    def received(event: AxencoEvent) -> None:
        events.append(event)
        if event == ConnectionChanged(connected=True):
            ready.set()

    api_client.subscribe(received)
    server.inventory = [{"_id": "after", "model": "NTD", "state": {"targetTemp": 19}}]
    await api_client.sio.eio.ws.close()
    await asyncio.wait_for(ready.wait(), 3)
    assert events[0] == ConnectionChanged(connected=False)
    assert events[-1] == ConnectionChanged(connected=True)
    assert {
        event.device_id for event in events if isinstance(event, DeviceRemoved)
    } == {"parent", "child"}
    assert (await api_client.get_devices())[0].id == "after"
    assert not api_client.session.closed


async def test_unknown_ext_reconciles(api_client: PyAxencoAPI, server: Server) -> None:
    await api_client.get_devices()
    before = len(server.calls)
    server.inventory[1]["state"] = {"targetTemp": 22}
    await api_client.sio.handlers["/"]["updateExtDevState"](
        {"objectId": "parent", "data": {"unknown-rfid": {"targetTemp": 10}}}
    )
    assert (await api_client.get_devices())[1].state.target_temperature == 22
    assert len(server.calls) == before + 1


async def test_subdevice_response(api_client: PyAxencoAPI, server: Server) -> None:
    assert await api_client.get_sub_device_state(",gateway,p,") == {
        "c": {"targetTemp": 19}
    }
    assert server.calls[-1][1] == "/v1/devices/gateway/sub-devices"


async def test_login_contract(api_client: PyAxencoAPI, server: Server) -> None:
    assert server.calls[0] == (
        "POST",
        "/v1/auth/login",
        {"email": "user@example.test", "password": "secret"},
        None,
    )
    server.login_payload = {"token": "new", "refresh_token": "rotated", "id": "other"}
    await api_client.login("other@example.test", "password")
    assert api_client.user_id == "other"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-fields"),
        pytest.param(
            {"token": 42, "refresh_token": "ref", "id": "user"}, id="wrong-type"
        ),
        pytest.param(
            {"token": "new", "refresh_token": "ref", "id": ""}, id="empty-user"
        ),
    ],
)
async def test_login_invalid_keeps_identity(
    api_client: PyAxencoAPI, server: Server, payload: object
) -> None:
    server.login_payload = payload
    with pytest.raises(AxencoResponseError) as raised:
        await api_client.login("user@example.test", "secret")
    assert raised.value.__cause__ is not None
    assert api_client.token == "old"
    assert api_client.user_id == "user"


async def test_second_401_is_auth_error(
    api_client: PyAxencoAPI, server: Server
) -> None:
    api_client.token = "expired"
    server.status = 401
    with pytest.raises(AxencoAuthError):
        await api_client.set_device_mode("parent", 2)
    assert server.refresh_count == 1


async def test_bad_subdevice_parents(api_client: PyAxencoAPI) -> None:
    with pytest.raises(AxencoResponseError) as raised:
        await api_client.set_sub_device_mode(",,", "c", 2)
    assert isinstance(raised.value.__cause__, ValueError)


async def test_legacy_inventory_callbacks(
    api_client: PyAxencoAPI, server: Server
) -> None:
    added: list[dict[str, object]] = []
    removed: list[str] = []
    with pytest.warns(DeprecationWarning):
        unsubscribe_added = api_client.register_discovery_callback(added.append)
    with pytest.warns(DeprecationWarning):
        unsubscribe_removed = api_client.register_removal_callback(removed.append)
    await api_client.get_devices()
    assert added[0]["_id"] == "parent"
    assert added[1]["parents"] == ",p,"
    server.inventory = []
    await api_client.get_devices(force=True)
    assert set(removed) == {"parent", "child"}
    unsubscribe_added()
    unsubscribe_removed()
    assert api_client._subscribers == {}


async def test_subscriber_errors_isolated(
    api_client: PyAxencoAPI, caplog: pytest.LogCaptureFixture
) -> None:
    await api_client.get_devices()

    def broken(event: AxencoEvent) -> None:
        raise RuntimeError("secret")

    events: list[AxencoEvent] = []
    api_client.subscribe(broken)
    api_client.subscribe(events.append)
    await api_client.notify_update("child", {"targetTemp": 21})
    assert len(events) == 1
    assert "secret" not in caplog.text


async def test_incomplete_update_reconciles(
    api_client: PyAxencoAPI, server: Server
) -> None:
    await api_client.get_devices()
    server.inventory[0]["state"] = {"targetTemp": 23}
    await api_client.sio.handlers["/"]["update"]({"objectId": "parent"})
    assert (await api_client.get_devices())[0].state.target_temperature == 23


async def test_concurrent_transient_refresh_failure(
    api_client: PyAxencoAPI, server: Server
) -> None:
    api_client.token = "expired"
    server.refresh_status = 500
    results = await asyncio.gather(
        *(api_client.get_device_state("parent") for _ in range(5)),
        return_exceptions=True,
    )
    assert all(isinstance(result, AxencoResponseError) for result in results)
    assert server.refresh_count == 1
    server.refresh_status = 200
    assert (await api_client.get_device_state("parent")).target_temperature == 21
    assert server.refresh_count == 2


async def test_reconnect_reconciliation_recovery(
    api_client: PyAxencoAPI, server: Server
) -> None:
    await api_client.connect_websocket()
    events: list[AxencoEvent] = []
    api_client.subscribe(events.append)
    server.status = 500
    await api_client.sio.handlers["/"]["connect"]()
    assert events[-1] == ConnectionChanged(connected=False)
    assert api_client._recovery_task is not None
    server.status = 200
    server.inventory[0]["state"] = {"targetTemp": 24}
    await asyncio.wait_for(api_client._recovery_task, 3)
    assert events[-1] == ConnectionChanged(connected=True)
    assert (await api_client.get_devices())[0].state.target_temperature == 24


async def test_disconnect_cancels_reconciliation(
    api_client: PyAxencoAPI, server: Server
) -> None:
    await api_client.connect_websocket()
    server.status = 500
    await api_client.sio.handlers["/"]["connect"]()
    recovery = api_client._recovery_task
    assert recovery is not None
    await api_client.disconnect_websocket()
    assert recovery.done()
    assert api_client._recovery_task is None
    assert not api_client.session.closed


@pytest.mark.parametrize("reconnect", [False, True])
@pytest.mark.parametrize("status", [200, 500])
async def test_shutdown_during_inventory_sync(
    api_client: PyAxencoAPI,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    reconnect: bool,
    status: int,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    original = api_client.get_devices

    async def blocked(force: bool = False) -> list[AxencoDevice]:
        entered.set()
        await release.wait()
        return await original(force=force)

    if reconnect:
        await api_client.connect_websocket()
    monkeypatch.setattr(api_client, "get_devices", blocked)
    server.status = status
    api_client.sio.reconnection_delay = 0.01
    api_client.sio.randomization_factor = 0
    connecting = None
    if reconnect:
        await api_client.sio.eio.ws.close()
    else:
        connecting = asyncio.create_task(api_client.connect_websocket())
    await asyncio.wait_for(entered.wait(), 2)
    owned_tasks = {
        *api_client._handlers,
        api_client.sio.eio.read_loop_task,
        api_client.sio.eio.write_loop_task,
    }
    await api_client.disconnect_websocket()
    release.set()
    if connecting is not None:
        with pytest.raises(AxencoConnectionError):
            await connecting
    await asyncio.sleep(0.05)
    assert not api_client.sio.connected
    assert api_client.sio._reconnect_task is None
    assert api_client._recovery_task is None
    assert not api_client._handlers
    assert all(task.done() for task in owned_tasks)
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_coro().__qualname__.startswith("PyAxencoAPI.")
    ]


async def test_parent_reconnect_preserves_child_fact(
    api_client: PyAxencoAPI,
    server: Server,
) -> None:
    server.inventory[1]["connected"] = False
    await api_client.get_devices()
    await api_client.notify_update("parent", {"connected": False})
    await api_client.notify_update("parent", {"connected": True})
    assert (await api_client.get_devices())[1].connected is False
    assert not api_client.is_device_available("child")


@pytest.mark.parametrize("refresh_status", [200, 401])
async def test_expired_socket_handshake(
    api_client: PyAxencoAPI,
    server: Server,
    refresh_status: int,
) -> None:
    async def connect(sid: str, environ: dict[str, Any], auth: object) -> None:
        if environ.get("HTTP_AUTHORIZATION") == "Bearer expired":
            raise socketio.exceptions.ConnectionRefusedError("unauthorized")

    server.sio.on("connect", connect)
    api_client.sio.reconnection_delay = 0.01
    api_client.sio.randomization_factor = 0
    await api_client.connect_websocket()
    events: list[AxencoEvent] = []
    ready = asyncio.Event()

    def received(event: AxencoEvent) -> None:
        events.append(event)
        if isinstance(event, ConnectionChanged) and (
            event.connected or getattr(event, "error", None) is not None
        ):
            ready.set()

    api_client.subscribe(received)
    server.refresh_status = refresh_status
    api_client.token = "expired"
    await api_client.sio.eio.ws.close()
    await asyncio.wait_for(ready.wait(), 3)
    assert server.refresh_count == 1
    assert api_client._connected is (refresh_status == 200)
    assert isinstance(events[-1], ConnectionChanged)
    assert (events[-1].error is None) is (refresh_status == 200)
    await asyncio.sleep(0.05)
    assert server.refresh_count == 1
    await api_client.disconnect_websocket()


async def test_second_socket_token_expiry_refreshes(
    api_client: PyAxencoAPI,
    server: Server,
) -> None:
    expired: set[str] = set()

    async def connect(sid: str, environ: dict[str, Any], auth: object) -> None:
        token = (environ.get("HTTP_AUTHORIZATION") or "").removeprefix("Bearer ")
        if token in expired:
            raise socketio.exceptions.ConnectionRefusedError("unauthorized")

    server.sio.on("connect", connect)
    api_client.sio.reconnection_delay = 0.01
    api_client.sio.randomization_factor = 0
    await api_client.connect_websocket()
    events: list[AxencoEvent] = []
    ready = asyncio.Event()

    def received(event: AxencoEvent) -> None:
        events.append(event)
        if isinstance(event, ConnectionChanged) and (
            event.connected or event.error is not None
        ):
            ready.set()

    api_client.subscribe(received)
    expired.add("old")
    await api_client.sio.eio.ws.close()
    await asyncio.wait_for(ready.wait(), 3)
    assert server.refresh_count == 1
    assert api_client._connected
    assert api_client.token == "new"
    assert events[-1] == ConnectionChanged(connected=True)

    ready.clear()
    server.refresh_payload = {"token": "newer", "refresh_token": "rotated-2"}
    expired.add("new")
    await api_client.sio.eio.ws.close()
    await asyncio.wait_for(ready.wait(), 3)
    assert server.refresh_count == 2
    assert api_client._connected
    assert api_client.token == "newer"
    assert events[-1] == ConnectionChanged(connected=True)
    assert api_client.sio.reconnection
    await asyncio.sleep(0.05)
    assert server.refresh_count == 2
    await api_client.disconnect_websocket()


async def test_second_expiry_invalid_refresh_token(
    api_client: PyAxencoAPI,
    server: Server,
) -> None:
    expired: set[str] = set()

    async def connect(sid: str, environ: dict[str, Any], auth: object) -> None:
        token = (environ.get("HTTP_AUTHORIZATION") or "").removeprefix("Bearer ")
        if token in expired:
            raise socketio.exceptions.ConnectionRefusedError("unauthorized")

    server.sio.on("connect", connect)
    api_client.sio.reconnection_delay = 0.01
    api_client.sio.randomization_factor = 0
    await api_client.connect_websocket()
    events: list[AxencoEvent] = []
    ready = asyncio.Event()

    def received(event: AxencoEvent) -> None:
        events.append(event)
        if isinstance(event, ConnectionChanged) and (
            event.connected or event.error is not None
        ):
            ready.set()

    api_client.subscribe(received)
    expired.add("old")
    await api_client.sio.eio.ws.close()
    await asyncio.wait_for(ready.wait(), 3)
    assert server.refresh_count == 1
    assert api_client._connected
    assert api_client.token == "new"

    ready.clear()
    server.refresh_status = 401
    expired.add("new")
    await api_client.sio.eio.ws.close()
    await asyncio.wait_for(ready.wait(), 3)
    assert server.refresh_count == 2
    assert isinstance(events[-1], ConnectionChanged)
    assert isinstance(events[-1].error, AxencoAuthError)
    assert not api_client.sio.reconnection
    await api_client.disconnect_websocket()


async def test_double_connect(api_client: PyAxencoAPI) -> None:
    await api_client.connect_websocket()
    await api_client.connect_websocket()
    assert api_client.sio.connected


async def test_partial_unknown_rfid(api_client: PyAxencoAPI, server: Server) -> None:
    await api_client.get_devices()
    server.inventory.append(
        {"_id": "new", "model": "UFH", "rfid": "n", "parents": ",p,"}
    )
    await api_client.sio.handlers["/"]["updateExtDevState"](
        {
            "objectId": "parent",
            "data": {"c": {"targetTemp": 24}, "n": {"targetTemp": 25}},
        }
    )
    assert (await api_client.get_devices())[-1].state.target_temperature == 25


async def test_connect_waits_for_inventory(
    api_client: PyAxencoAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    original_inventory = api_client.get_devices
    original_connect = api_client.sio.connect

    async def blocked(force: bool = False) -> list[AxencoDevice]:
        entered.set()
        await release.wait()
        return await original_inventory(force=force)

    async def short_connect(*args: Any, **kwargs: Any) -> None:
        kwargs["wait_timeout"] = 0.01
        await original_connect(*args, **kwargs)

    monkeypatch.setattr(api_client, "get_devices", blocked)
    monkeypatch.setattr(api_client.sio, "connect", short_connect)
    connecting = asyncio.create_task(api_client.connect_websocket())
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.sleep(0.05)
    assert not connecting.done()
    assert not api_client._connected
    release.set()
    await asyncio.wait_for(connecting, 2)
    assert api_client._connected


async def test_initial_expired_socket_handshake(
    api_client: PyAxencoAPI,
    server: Server,
) -> None:
    async def connect(sid: str, environ: dict[str, Any], auth: object) -> None:
        if environ.get("HTTP_AUTHORIZATION") == "Bearer expired":
            raise socketio.exceptions.ConnectionRefusedError("unauthorized")

    server.sio.on("connect", connect)
    api_client.token = "expired"
    await api_client.connect_websocket()
    assert api_client._connected
    assert server.refresh_count == 1


async def test_socket_rejects_refreshed_credentials(
    api_client: PyAxencoAPI,
    server: Server,
) -> None:
    async def connect(sid: str, environ: dict[str, Any], auth: object) -> None:
        raise socketio.exceptions.ConnectionRefusedError("unauthorized")

    server.sio.on("connect", connect)
    with pytest.raises(AxencoAuthError):
        await api_client.connect_websocket()
    assert server.refresh_count == 1
    assert not api_client.sio.connected
    assert api_client.sio._reconnect_task is None


async def test_coalesced_unknown_updates(
    api_client: PyAxencoAPI,
    server: Server,
) -> None:
    await api_client.get_devices()
    before = len(server.calls)
    server.delay = 0.02
    await asyncio.gather(
        *(api_client.notify_update("unknown", {"targetTemp": 20}) for _ in range(10))
    )
    assert len(server.calls) == before + 1


@pytest.mark.parametrize("status", [502, 503])
async def test_engineio_transport_failure(
    api_client: PyAxencoAPI,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    failure = engineio.exceptions.ConnectionError(
        f"Unexpected status code {status} in server response"
    )
    monkeypatch.setattr(api_client.sio.eio, "connect", AsyncMock(side_effect=failure))
    with pytest.raises(AxencoConnectionError):
        await api_client.connect_websocket()
    assert server.refresh_count == 0
    assert api_client.token == "old"


@pytest.mark.parametrize("status", [401, 403])
async def test_http_socket_credentials_rejected(
    api_client: PyAxencoAPI,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    failure = engineio.exceptions.ConnectionError(
        f"Unexpected status code {status} in server response"
    )
    connecting = AsyncMock(side_effect=failure)
    monkeypatch.setattr(api_client.sio.eio, "connect", connecting)
    with pytest.raises(AxencoAuthError):
        await api_client.connect_websocket()
    assert server.refresh_count == 1
    assert connecting.await_count == 2


async def test_socket_outage_recovers(
    api_client: PyAxencoAPI,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pyaxencoapi.api import API_BASE

    api_client.sio.reconnection_delay = 0.01
    api_client.sio.reconnection_delay_max = 0.02
    api_client.sio.randomization_factor = 0
    await api_client.connect_websocket()
    events: list[AxencoEvent] = []
    failed = asyncio.Event()
    recovered = asyncio.Event()

    def received(event: AxencoEvent) -> None:
        events.append(event)
        if event == ConnectionChanged(connected=True):
            recovered.set()
        if (
            len(
                [
                    item
                    for item in events
                    if isinstance(item, ConnectionChanged) and item.error is not None
                ]
            )
            >= 3
        ):
            failed.set()

    api_client.subscribe(received)
    monkeypatch.setattr("pyaxencoapi.api.API_BASE", "http://127.0.0.1:1")
    await api_client.sio.eio.ws.close()
    await asyncio.wait_for(failed.wait(), 3)
    assert api_client.sio.reconnection
    assert server.refresh_count == 0
    assert all(
        isinstance(item.error, AxencoConnectionError)
        for item in events
        if isinstance(item, ConnectionChanged) and item.error is not None
    )
    monkeypatch.setattr("pyaxencoapi.api.API_BASE", API_BASE)
    server.inventory[0]["state"] = {"targetTemp": 29}
    await asyncio.wait_for(recovered.wait(), 3)
    assert (await api_client.get_devices())[0].state.target_temperature == 29
    assert server.refresh_count == 0


@pytest.mark.parametrize(
    "status,error,refresh_count",
    [
        pytest.param(401, AxencoAuthError, 1, id="unauthorized"),
        pytest.param(403, AxencoAuthError, 1, id="forbidden"),
        pytest.param(502, AxencoConnectionError, 0, id="bad-gateway"),
        pytest.param(503, AxencoConnectionError, 0, id="unavailable"),
    ],
)
async def test_real_http_handshake_failure(
    api_client: PyAxencoAPI,
    server: Server,
    status: int,
    error: type[Exception],
    refresh_count: int,
) -> None:
    server.socket_status = status
    with pytest.raises(error):
        await api_client.connect_websocket()
    assert server.refresh_count == refresh_count
