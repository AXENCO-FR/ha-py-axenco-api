"""Strict consumer of the typed client, independent of Home Assistant."""

from collections.abc import Callable, Mapping
from typing import assert_type

import aiohttp

from pyaxencoapi import AxencoDevice, AxencoEvent, AxencoState, PyAxencoAPI


async def consume(session: aiohttp.ClientSession) -> None:
    client = PyAxencoAPI(session)

    def event_received(event: AxencoEvent) -> None:
        assert_type(event, AxencoEvent)

    unsubscribe = client.subscribe(event_received)
    assert_type(unsubscribe, Callable[[], None])
    devices = await client.get_devices()
    assert_type(devices, list[AxencoDevice])
    assert_type(await client.get_device_state("id"), AxencoState)
    assert_type(await client.get_sub_device_state(",gateway,"), Mapping[str, object])
    await client.set_device_temperature("id", 20.5)
    await client.set_sub_device_mode_ufh(",gateway,", "rfid", 1)
    await client.set_device_program("id", {"monday": []})
    unsubscribe()
