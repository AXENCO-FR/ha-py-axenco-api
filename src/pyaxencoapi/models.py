"""Immutable public snapshots and validated protocol conversions.

Unknown protocol keys are ignored, not retained as unvalidated raw data.
An omitted snapshot field is unknown (None); patches retain omission as UNSET.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from enum import Enum
from math import isfinite
from typing import Any, cast

from .const import PRESET_MODE_MODELS, ModelPresetMap
from .exceptions import AxencoError, AxencoResponseError


class Unset(Enum):
    """The field was not included in a partial event."""

    UNSET = "unset"


UNSET = Unset.UNSET

_STATE_KEYS = {
    "current_temperature": "currentTemp",
    "target_temperature": "targetTemp",
    "override_temperature": "overrideTemp",
    "min_temperature": "comfLimitMin",
    "max_temperature": "comfLimitMax",
    "comfort_temperature": "comfTemp",
    "eco_temperature": "ecoTemp",
    "target_mode": "targetMode",
    "change_over_user": "changeOverUser",
    "device_type": "deviceType",
}
_INTEGER_FIELDS = {"target_mode", "change_over_user", "device_type"}


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise AxencoResponseError("Expected a JSON object") from TypeError(
            "Expected object with string keys"
        )
    return cast(Mapping[str, object], value)


def _text(value: object, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise AxencoResponseError(f"Invalid {field}") from ValueError("Expected string")
    return value


def _state_values(value: object) -> dict[str, Any]:
    data = _object(value)
    result: dict[str, Any] = {}
    for field, key in _STATE_KEYS.items():
        if key not in data:
            continue
        item = data[key]
        if item is not None:
            if field in _INTEGER_FIELDS:
                valid = type(item) is int
            else:
                valid = type(item) in (int, float)
                if valid:
                    try:
                        valid = isfinite(cast(float, item))
                    except OverflowError as err:
                        raise AxencoResponseError(f"Invalid {key}") from err
            if not valid:
                raise AxencoResponseError(f"Invalid {key}") from ValueError(
                    "Expected finite number of the correct type"
                )
        result[field] = item
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class AxencoState:
    """Normalized state; missing and explicitly null snapshot fields are unknown."""

    current_temperature: float | None = None
    target_temperature: float | None = None
    override_temperature: float | None = None
    min_temperature: float | None = None
    max_temperature: float | None = None
    comfort_temperature: float | None = None
    eco_temperature: float | None = None
    target_mode: int | None = None
    change_over_user: int | None = None
    device_type: int | None = None

    @classmethod
    def from_json(cls, value: object) -> AxencoState:
        """Validate a protocol state object without coercing strings or booleans."""
        return cls(**_state_values(value))


@dataclass(frozen=True, slots=True, kw_only=True)
class AxencoStatePatch:
    """Partial state: UNSET preserves the previous value, None clears it."""

    current_temperature: float | None | Unset = UNSET
    target_temperature: float | None | Unset = UNSET
    override_temperature: float | None | Unset = UNSET
    min_temperature: float | None | Unset = UNSET
    max_temperature: float | None | Unset = UNSET
    comfort_temperature: float | None | Unset = UNSET
    eco_temperature: float | None | Unset = UNSET
    target_mode: int | None | Unset = UNSET
    change_over_user: int | None | Unset = UNSET
    device_type: int | None | Unset = UNSET

    @classmethod
    def from_json(cls, value: object) -> AxencoStatePatch:
        """Validate a partial protocol state object."""
        return cls(**_state_values(value))

    def apply(self, state: AxencoState) -> AxencoState:
        """Return a new snapshot without mutating the previous one."""
        return replace(
            state,
            **{
                field.name: value
                for field in fields(self)
                if (value := getattr(self, field.name)) is not UNSET
            },
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AxencoDevice:
    """Device identity, physical connection and normalized state."""

    id: str
    model: str
    name: str | None = None
    connected: bool | None = None
    parents: tuple[str, ...] = ()
    rfid: str | None = None
    state: AxencoState = AxencoState()

    @classmethod
    def from_json(cls, value: object) -> AxencoDevice:
        """Validate a complete device; incomplete discovery must be reconciled."""
        data = _object(value)
        device_id = _text(data.get("_id"), "_id", required=True)
        model = _text(data.get("model"), "model", required=True)
        connected = data.get("connected")
        if connected is not None and type(connected) is not bool:
            raise AxencoResponseError("Invalid connected") from TypeError(
                "Expected boolean"
            )
        parents = _text(data.get("parents"), "parents")
        raw_state = data.get("state")
        return cls(
            id=cast(str, device_id),
            model=cast(str, model),
            name=_text(data.get("name"), "name"),
            connected=connected,
            parents=tuple(
                part.strip() for part in (parents or "").split(",") if part.strip()
            ),
            rfid=_text(data.get("rfid"), "rfid"),
            state=AxencoState()
            if raw_state is None
            else AxencoState.from_json(raw_state),
        )

    @property
    def preset_modes(self) -> ModelPresetMap:
        """Return model-specific capabilities, empty for unknown models/types."""
        model = self.model
        if model == "EWS":
            if self.state.device_type not in (0, 1):
                return ModelPresetMap()
            model = "EWS_RELAIS" if self.state.device_type == 0 else "EWS_PILOTE"
        return ModelPresetMap(PRESET_MODE_MODELS.get(model, {}))


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceUpdated:
    """A patch applies only to this device; connection is physical, not transport."""

    device_id: str
    patch: AxencoStatePatch = AxencoStatePatch()
    connected: bool | None | Unset = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceAdded:
    """A fully resolved device was added to the inventory."""

    device: AxencoDevice


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceRemoved:
    """A confirmed inventory removal, not a physical disconnection."""

    device_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectionChanged:
    """Availability of the client transport, independent of device connection."""

    connected: bool
    error: AxencoError | None = None


type AxencoEvent = DeviceUpdated | DeviceAdded | DeviceRemoved | ConnectionChanged
