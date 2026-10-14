from typing import TYPE_CHECKING, Final

from .api import PyAxencoAPI
from .const import (
    PRESET_MODE_MODELS,
    ModelPresetMap,
    Preset,
    _legacy_table,
)
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
    Unset,
)

if TYPE_CHECKING:
    PRESET_BY_KEY: Final[dict[str, Preset]] = {}
    PRESET_MODE_MAP: Final[dict[str, int]] = {}
    PRESET_MODE_SELECT_EXTRAS: Final[dict[str, int]] = {}
    PRESET_MODE_MAP_RELAIS: Final[dict[str, int]] = {}
    REVERSE_PRESET_MODE_MAP_RELAIS: Final[dict[int, str]] = {}
    PRESET_MODE_MAP_UFH: Final[dict[str, int]] = {}
    REVERSE_PRESET_MODE_MAP_UFH: Final[dict[int, str]] = {}

if not TYPE_CHECKING:

    def __getattr__(name: str) -> dict[str, int] | dict[int, str] | dict[str, Preset]:
        return _legacy_table(name, __name__)


__all__ = [
    "PRESET_MODE_MODELS",
    "UNSET",
    "AxencoAuthError",
    "AxencoConnectionError",
    "AxencoDevice",
    "AxencoError",
    "AxencoEvent",
    "AxencoResponseError",
    "AxencoState",
    "AxencoStatePatch",
    "ConnectionChanged",
    "DeviceAdded",
    "DeviceRemoved",
    "DeviceUpdated",
    "ModelPresetMap",
    "Preset",
    "PyAxencoAPI",
    "Unset",
]
