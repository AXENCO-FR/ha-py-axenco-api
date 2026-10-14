"""A typed consumer importing only the installed public package."""

from typing import TYPE_CHECKING, assert_type

import pyaxencoapi
from pyaxencoapi import (
    AxencoAuthError,
    AxencoConnectionError,
    AxencoDevice,
    AxencoError,
    AxencoEvent,
    AxencoResponseError,
    AxencoState,
    AxencoStatePatch,
    ConnectionChanged,
    DeviceAdded,
    DeviceRemoved,
    DeviceUpdated,
    ModelPresetMap,
    Preset,
    Unset,
    const,
)

if TYPE_CHECKING:
    _ = pyaxencoapi.not_there  # type: ignore[attr-defined]
    _ = const.not_there  # type: ignore[attr-defined]
    assert_type(pyaxencoapi.PRESET_MODE_MAP, dict[str, int])
    assert_type(pyaxencoapi.PRESET_MODE_MAP["comfort"], int)
    assert_type(pyaxencoapi.PRESET_MODE_SELECT_EXTRAS, dict[str, int])
    assert_type(pyaxencoapi.PRESET_MODE_MAP_RELAIS, dict[str, int])
    assert_type(pyaxencoapi.REVERSE_PRESET_MODE_MAP_RELAIS, dict[int, str])
    assert_type(pyaxencoapi.PRESET_MODE_MAP_UFH, dict[str, int])
    assert_type(pyaxencoapi.REVERSE_PRESET_MODE_MAP_UFH, dict[int, str])
    assert_type(pyaxencoapi.REVERSE_PRESET_MODE_MAP_UFH[1], str)
    assert_type(pyaxencoapi.PRESET_BY_KEY, dict[str, Preset])
    assert_type(const.PRESET_MODE_MAP, dict[str, int])
    assert_type(const.PRESET_MODE_SELECT_EXTRAS, dict[str, int])
    assert_type(const.PRESET_MODE_MAP_RELAIS, dict[str, int])
    assert_type(const.REVERSE_PRESET_MODE_MAP_RELAIS, dict[int, str])
    assert_type(const.PRESET_MODE_MAP_UFH, dict[str, int])
    assert_type(const.REVERSE_PRESET_MODE_MAP_UFH, dict[int, str])
    assert_type(const.PRESET_BY_KEY, dict[str, Preset])


def consume(event: AxencoEvent) -> str | None:
    if isinstance(event, DeviceAdded):
        assert_type(event.device, AxencoDevice)
        assert_type(event.device.preset_modes, ModelPresetMap)
        return event.device.id
    if isinstance(event, DeviceUpdated):
        assert_type(event.patch.apply(AxencoState()), AxencoState)
        assert_type(event.patch.target_mode, int | None | Unset)
        assert_type(event.patch.current_temperature, float | None | Unset)
        return event.device_id
    if isinstance(event, DeviceRemoved):
        return event.device_id
    assert_type(event, ConnectionChanged)
    assert_type(event.connected, bool)
    return None


assert_type(AxencoDevice.from_json({"_id": "1", "model": "NTD"}), AxencoDevice)
assert_type(AxencoStatePatch.from_json({}), AxencoStatePatch)
errors: tuple[type[AxencoError], ...] = (
    AxencoAuthError,
    AxencoConnectionError,
    AxencoResponseError,
)
