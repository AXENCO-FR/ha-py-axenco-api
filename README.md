# PyAxencoAPI

An asynchronous Python client for MyNeomitis connected devices using the REST API and WebSocket.

## Installation

```bash
pip install pyaxencoapi
```

Python **3.14 or later** is required. Python 3.11, 3.12, and 3.13 are no
longer supported.

## Public objects and migration

This release breaks the public contract: device dictionaries are replaced with
`AxencoDevice` objects, and network, authentication, and response errors become
explicit exceptions. These breaking changes are versioned as `2.0.0`.
Availability on PyPI depends on a maintainer publishing this version.

`get_devices(force=False)` returns a list of `AxencoDevice` objects and uses a
five-minute cache based on a monotonic clock; an empty list means the inventory
is genuinely empty. `get_device_state()` returns `AxencoState`.
`subscribe(callback)` returns an idempotent unsubscribe function, supports
multiple callbacks, and isolates callback failures. Patches apply only to the
affected device. Each child's physical connection remains independent;
`is_device_available(id)` combines its connection with those of its known
parents without modifying snapshots. Consumers combine this result with the
transport status and reevaluate children when a parent event arrives.

`link`, `discover`, and incomplete events trigger REST inventory reconciliation.
Every connection or reconnection reconciles states before announcing that the
transport is available. Socket.IO headers are rebuilt using the current tokens.
Transient reconciliation failures after reconnection are retried with backoff
until success, shutdown, or authentication rejection.
REST requests have a ten-second timeout. Socket.IO setup has a forty-five-second
timeout and waits for the initial reconciliation. Only an explicit namespace
rejection or an HTTP 401/403 during the handshake triggers at most one shared
refresh per rejected token before retrying. Successfully establishing a namespace
with that token resets the counter, allowing another refresh after a later
expiration. Network failures and HTTP 5xx responses do not refresh the token:
they report `AxencoConnectionError` and allow automatic reconnection after an
interruption. A permanent rejection stops reconnection attempts and emits
`ConnectionChanged(connected=False, error=AxencoAuthError(...))`.
An HTTP 401 triggers at most one shared refresh followed by a single retry;
a rejected refresh requires reauthentication.

The supplied aiohttp session belongs to the caller and is never closed by the
library. `disconnect_websocket()` also stops active handlers, inventory retrieval,
and reconnection tasks. `logout()` unsubscribes and clears the cache even if the
server rejects the logout request.
Socket.IO >= 5.12 is required for its reconnection task shutdown API.
With Socket.IO 5.12, each namespace rejection can take ten seconds;
a persistent rejection during setup can therefore take twenty seconds.
Action methods raise public exceptions rather than returning booleans.
Programs and the inner response of `get_sub_device_state()` remain mutable JSON
objects; they must not be used as domain snapshots.

`register_listener()` and `register_removal_callback()` are deprecated adapters
that support unsubscribing. The listener receives a partial state dictionary,
but no longer receives states arbitrarily copied from the parent.
`register_discovery_callback()` is deprecated and provides a projected dictionary
for each device, without raw JSON or an ambiguous list; migrate to `DeviceAdded`.

```python
from pyaxencoapi import AxencoDevice, AxencoStatePatch

device = AxencoDevice.from_json({
    "_id": "device-1",
    "model": "NTD",
    "connected": True,
    "parents": ",gateway-1,",
    "state": {"currentTemp": 21.5, "targetTemp": 22, "targetMode": 2},
})
patch = AxencoStatePatch.from_json({"currentTemp": None})
updated_state = patch.apply(device.state)
assert updated_state.current_temperature is None
assert updated_state.target_temperature == 22
assert device.preset_modes.reverse[2] == "eco"
```

Snapshots are immutable. `_id` becomes `id`, and state field names become
explicit (`current_temperature`, `target_temperature`,
`override_temperature`, `min_temperature`, `max_temperature`,
`comfort_temperature`, `eco_temperature`, `target_mode`, `change_over_user`,
`device_type`). `parents` is an ordered tuple parsed from the protocol's
comma-delimited string; `rfid` remains an optional string.
Nonempty IDs and models are required; unknown models remain strings without
invented capabilities.

`from_json()` validates objects at the protocol boundary: numeric strings and
booleans are not converted to numbers, temperatures must be finite, and codes
must be integers. Unknown integer codes are preserved for extensibility.
Missing or null optional fields become `None` in a snapshot, including an unknown
physical connection. A missing or null device state produces an unknown state.
Unknown keys are ignored, and no raw JSON is retained. Typed constructors do not
replace this validation. Undocumented program and sub-device schemas are not
defined here.

In `AxencoStatePatch`, an **absent** field means `UNSET` (leave unchanged), while
**null** means `None` (clear the value). `apply()` creates a new state without
changing the previous one. A patch is a state object, not the Socket.IO envelope.
An incomplete inventory event must trigger reconciliation, not be converted into
an artificial device.

`AxencoEvent` is the union of `DeviceUpdated` (ID, patch, and optional physical
connection), `DeviceAdded` (complete device), `DeviceRemoved` (confirmed removal),
and `ConnectionChanged` (client transport).
A transport interruption does not change a device's physical connection, and a
parent patch must not be copied to its children.

`AxencoError` is the base class for `AxencoAuthError`, `AxencoConnectionError`,
and `AxencoResponseError`. Conversions raise `AxencoResponseError` without
including sensitive payloads; any underlying error is preserved as the cause
(`raise ... from ...`).

## Modes and legacy compatibility

`PRESET_MODE_MODELS[model]` and `ModelPresetMap.reverse` are the source of truth.
Code `1` means **comfort** for NTD, **on** for EWS relays, and **cooling** for UFH:
never invert a global mapping. For EWS, `device_type` set to `0` selects
`EWS_RELAIS`, and `1` selects `EWS_PILOTE`; a missing, null, or unknown value
provides no capabilities. `device.preset_modes` returns a copy.

The legacy constants `PRESET_MODE_MAP`, `PRESET_MODE_SELECT_EXTRAS`,
`PRESET_MODE_MAP_RELAIS`, `REVERSE_PRESET_MODE_MAP_RELAIS`,
`PRESET_MODE_MAP_UFH`, and `REVERSE_PRESET_MODE_MAP_UFH` are available from
`pyaxencoapi` and `pyaxencoapi.const`, with a `DeprecationWarning`.
Reverse mappings remain strictly specific to their family; no ambiguous global
reverse mapping is provided. `Preset` and its codes remain available.

Compared with version **1.0.7**, pinned by Home Assistant:
- `PRESET_MODE_MAP` retains exactly its eight thermostat keys; relay, UFH, and
  `eco_1`/`eco_2` modes remain in their dedicated mappings. For additional models,
  use `PRESET_MODE_MODELS[model]` directly rather than looking them up in the
  legacy thermostat mapping.
- `PRESET_BY_KEY` remains available from both modules with a `DeprecationWarning`;
  it maps each unique key to its `Preset`, including the new relay, UFH, and
  `eco_1`/`eco_2` modes.
- `PRESET_BY_CODE` and `REVERSE_PRESET_MODE_MAP` have been removed because their
  global reverse mappings would be ambiguous. Use the model's `reverse` property.
- `PRESET_MODE_MODELS` changes from `dict[str, list[str]]` to
  `dict[str, ModelPresetMap]`: each value now maps keys to codes.
  `list(PRESET_MODE_MODELS[model])` provides the keys, and
  `PRESET_MODE_MODELS[model][key]` provides the model-specific code.

## Development and validation

```bash
python3 --version  # 3.14+
python3 -m pip install -e '.[dev]'
python3 -m pytest
python3 -m mypy
python3 -m build
python3 scripts/check_distribution.py dist/*.whl dist/*.tar.gz
```

Type checking covers the entire library and a consumer of its exports.
The wheel and source distribution include `py.typed` (PEP 561). REST and Socket.IO
tests use real local servers and work with aiohttp 3.14 without aioresponses or
an upper version bound on the development dependency.
