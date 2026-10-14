"""Public model conversions and model-local mode contracts."""

import copy
import json
from dataclasses import FrozenInstanceError, fields

import pytest

import pyaxencoapi
from pyaxencoapi import (
    PRESET_MODE_MODELS,
    UNSET,
    AxencoDevice,
    AxencoError,
    AxencoResponseError,
    AxencoState,
    AxencoStatePatch,
    ConnectionChanged,
    DeviceAdded,
    DeviceRemoved,
    DeviceUpdated,
)

STATE_FIELDS = [
    ("currentTemp", "current_temperature", 21.5),
    ("targetTemp", "target_temperature", 22),
    ("overrideTemp", "override_temperature", 23),
    ("comfLimitMin", "min_temperature", 7),
    ("comfLimitMax", "max_temperature", 30),
    ("comfTemp", "comfort_temperature", 20),
    ("ecoTemp", "eco_temperature", 18),
    ("targetMode", "target_mode", 60),
    ("changeOverUser", "change_over_user", 1),
    ("deviceType", "device_type", 0),
]


@pytest.mark.parametrize(("key", "field", "value"), STATE_FIELDS)
def test_state_fields(key: str, field: str, value: float) -> None:
    state = AxencoState.from_json({key: value})
    assert getattr(state, field) == value
    assert getattr(AxencoState.from_json({key: None}), field) is None
    assert AxencoStatePatch.from_json({key: value}).apply(AxencoState()) == state
    patch = AxencoStatePatch.from_json({key: None})
    assert getattr(patch, field) is None
    assert getattr(patch.apply(state), field) is None
    assert AxencoStatePatch.from_json({}).apply(state) == state


def test_partial_patch() -> None:
    state = AxencoState(current_temperature=21, target_mode=1)
    patch = AxencoStatePatch.from_json({"currentTemp": None, "newField": "ignored"})
    assert patch.target_mode is UNSET
    assert patch.apply(state) == AxencoState(target_mode=1)
    assert state.current_temperature == 21
    assert copy.deepcopy(patch).target_mode is UNSET
    assert {field.name for field in fields(AxencoState)} == {
        field.name for field in fields(AxencoStatePatch)
    }


@pytest.mark.parametrize("value", [None, [], "state", 2, {1: 2}])
def test_invalid_state_object(value: object) -> None:
    with pytest.raises(AxencoResponseError):
        AxencoState.from_json(value)
    with pytest.raises(AxencoResponseError):
        AxencoStatePatch.from_json(value)


@pytest.mark.parametrize("key", [entry[0] for entry in STATE_FIELDS])
@pytest.mark.parametrize(
    "value", [True, False, "21", [], {}, float("nan"), float("inf")]
)
def test_invalid_state_fields(key: str, value: object) -> None:
    with pytest.raises(AxencoResponseError):
        AxencoState.from_json({key: value})
    with pytest.raises(AxencoResponseError):
        AxencoStatePatch.from_json({key: value})


@pytest.mark.parametrize("key", ["targetMode", "changeOverUser", "deviceType"])
def test_integer_codes(key: str) -> None:
    with pytest.raises(AxencoResponseError):
        AxencoState.from_json({key: 1.5})
    assert AxencoState.from_json({key: 999}) is not None


def test_overflow_cause() -> None:
    with pytest.raises(AxencoResponseError) as error:
        AxencoState.from_json({"currentTemp": 10**1000})
    assert isinstance(error.value.__cause__, OverflowError)
    assert isinstance(error.value, AxencoError)


def test_device_conversion() -> None:
    payload = json.loads("""{
        "_id": "device-1", "name": "Thermostat", "model": "NTD",
        "connected": true, "parents": ",gateway, parent,", "rfid": "tag",
        "state": {"currentTemp": 21.5, "targetMode": 2}, "program": {"unknown": []}
    }""")
    device = AxencoDevice.from_json(payload)
    assert device == AxencoDevice(
        id="device-1",
        name="Thermostat",
        model="NTD",
        connected=True,
        parents=("gateway", "parent"),
        rfid="tag",
        state=AxencoState(current_temperature=21.5, target_mode=2),
    )
    payload["state"]["currentTemp"] = 0
    assert device.state.current_temperature == 21.5
    with pytest.raises(FrozenInstanceError):
        device.name = "changed"


@pytest.mark.parametrize("state", [None, {}])
def test_nullable_device(state: object) -> None:
    device = AxencoDevice.from_json({"_id": "1", "model": "future", "state": state})
    assert device.state == AxencoState()
    assert device.connected is None
    assert device.name is None
    assert device.parents == ()
    assert device.model == "future"
    assert device.preset_modes == {}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"_id": "1"},
        {"_id": "", "model": "NTD"},
        {"_id": 1, "model": "NTD"},
        {"_id": "1", "model": None},
        {"_id": "1", "model": " "},
    ],
)
def test_incomplete_device(payload: object) -> None:
    with pytest.raises(AxencoResponseError):
        AxencoDevice.from_json(payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("connected", 1),
        ("connected", "false"),
        ("parents", []),
        ("parents", {}),
        ("rfid", 1),
        ("name", []),
        ("state", []),
        ("state", "invalid"),
    ],
)
def test_invalid_device_fields(key: str, value: object) -> None:
    with pytest.raises(AxencoResponseError):
        AxencoDevice.from_json({"_id": "1", "model": "NTD", key: value})


@pytest.mark.parametrize(
    ("model", "device_type", "key"),
    [
        ("NTD", None, "comfort"),
        ("EWS", 0, "on"),
        ("EWS", 1, "comfort"),
        ("UFH", None, "cooling"),
        ("EWS", None, None),
        ("EWS", 99, None),
        ("unknown", 0, None),
    ],
)
def test_mode_collisions(model: str, device_type: int | None, key: str | None) -> None:
    device = AxencoDevice(
        id="1", model=model, state=AxencoState(device_type=device_type)
    )
    assert device.preset_modes.reverse.get(1) == key


@pytest.mark.parametrize("model", PRESET_MODE_MODELS)
def test_mode_roundtrip(model: str) -> None:
    modes = PRESET_MODE_MODELS[model]
    assert {modes.reverse[code]: code for code in modes.values()} == modes
    device = AxencoDevice(id="1", model=model)
    device.preset_modes.clear()
    assert device.preset_modes == modes


def test_events() -> None:
    device = AxencoDevice(id="1", model="NTD", connected=True)
    assert DeviceAdded(device=device).device == device
    assert DeviceRemoved(device_id="1").device_id == "1"
    assert DeviceUpdated(device_id="1").connected is UNSET
    assert DeviceUpdated(device_id="1", connected=None).connected is None
    assert not ConnectionChanged(connected=False).connected
    assert device.connected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "PRESET_MODE_MAP",
            {
                "comfort": 1,
                "eco": 2,
                "antifrost": 3,
                "standby": 4,
                "boost": 6,
                "setpoint": 8,
                "comfort_plus": 20,
                "auto": 60,
            },
        ),
        ("PRESET_MODE_MAP_RELAIS", {"on": 1, "off": 2, "auto": 60}),
        ("REVERSE_PRESET_MODE_MAP_RELAIS", {1: "on", 2: "off", 60: "auto"}),
        ("PRESET_MODE_MAP_UFH", {"heating": 0, "cooling": 1}),
        ("REVERSE_PRESET_MODE_MAP_UFH", {0: "heating", 1: "cooling"}),
        ("PRESET_MODE_SELECT_EXTRAS", {"eco_1": 40, "eco_2": 41}),
        ("PRESET_BY_KEY", {preset.key: preset for preset in pyaxencoapi.Preset}),
    ],
)
@pytest.mark.parametrize("module_name", ["pyaxencoapi", "pyaxencoapi.const"])
def test_legacy_tables(
    module_name: str, name: str, expected: dict[object, object]
) -> None:
    import importlib

    module = importlib.import_module(module_name)
    with pytest.deprecated_call(match=f"{name} is deprecated"):
        assert getattr(module, name) == expected


@pytest.mark.parametrize("module_name", ["pyaxencoapi", "pyaxencoapi.const"])
@pytest.mark.parametrize(
    "name", ["not_public", "PRESET_BY_CODE", "REVERSE_PRESET_MODE_MAP"]
)
def test_missing_exports(module_name: str, name: str) -> None:
    import importlib

    module = importlib.import_module(module_name)
    with pytest.raises(AttributeError) as exc:
        getattr(module, name)
    assert str(exc.value) == f"module {module_name!r} has no attribute {name!r}"


def test_exports() -> None:
    for name in pyaxencoapi.__all__:
        assert getattr(pyaxencoapi, name) is not None
    with pytest.raises(AttributeError):
        _ = pyaxencoapi.not_public
