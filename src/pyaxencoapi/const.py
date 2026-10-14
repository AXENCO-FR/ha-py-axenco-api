"""Constants for the PyAxencoAPI library."""

import warnings
from enum import Enum
from typing import TYPE_CHECKING, Final


class Preset(Enum):
    """Preset modes for Axenco devices."""

    COMFORT = ("comfort", 1)
    ECO = ("eco", 2)
    ANTIFROST = ("antifrost", 3)
    STANDBY = ("standby", 4)
    BOOST = ("boost", 6)
    SETPOINT = ("setpoint", 8)
    COMFORT_PLUS = ("comfort_plus", 20)
    ECO_1 = ("eco_1", 40)
    ECO_2 = ("eco_2", 41)
    AUTO = ("auto", 60)
    ON = ("on", 1)
    OFF = ("off", 2)
    HEATING = ("heating", 0)
    COOLING = ("cooling", 1)

    def __init__(self, key: str, code: int) -> None:
        """Initialize the preset enum with a key and numeric code."""
        self._key = key
        self._code = code

    @property
    def key(self) -> str:
        """Return the text key for this preset."""
        return self._key

    @property
    def code(self) -> int:
        """Return the numeric code for this preset."""
        return self._code

    def __str__(self) -> str:
        """Return the string representation (the key)."""
        return self._key


class ModelPresetMap(dict[str, int]):
    """Model-local codes: 1 means comfort, relay on, or UFH cooling.

    Never reverse a cross-model table: these code spaces overlap.
    """

    @property
    def reverse(self) -> dict[int, str]:
        """Return reverse mapping of preset code to preset key."""
        reverse_map: dict[int, str] = {}
        for key, code in self.items():
            reverse_map.setdefault(code, key)
        return reverse_map


def _model_presets(*presets: Preset) -> ModelPresetMap:
    """Build a model preset map from Preset values."""
    return ModelPresetMap({preset.key: preset.code for preset in presets})


PRESET_MODE_MODELS: dict[str, ModelPresetMap] = {
    "EV30": _model_presets(
        Preset.SETPOINT,
        Preset.BOOST,
        Preset.ECO,
        Preset.COMFORT,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "ECTRL": _model_presets(
        Preset.SETPOINT,
        Preset.BOOST,
        Preset.ECO,
        Preset.COMFORT,
        Preset.COMFORT_PLUS,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "ESTAT": _model_presets(
        Preset.SETPOINT,
        Preset.BOOST,
        Preset.ECO,
        Preset.COMFORT,
        Preset.COMFORT_PLUS,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "RSS-ECTRL": _model_presets(
        Preset.SETPOINT,
        Preset.BOOST,
        Preset.ECO,
        Preset.COMFORT,
        Preset.COMFORT_PLUS,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "NTD": _model_presets(
        Preset.SETPOINT,
        Preset.ECO,
        Preset.COMFORT,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "ETRV": _model_presets(
        Preset.SETPOINT,
        Preset.ECO,
        Preset.COMFORT,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "UFH": _model_presets(
        Preset.HEATING,
        Preset.COOLING,
    ),
    "EWS_RELAIS": _model_presets(
        Preset.ON,
        Preset.OFF,
        Preset.AUTO,
    ),
    "EWS_PILOTE": _model_presets(
        Preset.SETPOINT,
        Preset.BOOST,
        Preset.ECO,
        Preset.ECO_1,
        Preset.ECO_2,
        Preset.COMFORT,
        Preset.COMFORT_PLUS,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "DHW": _model_presets(
        Preset.ON,
        Preset.OFF,
        Preset.BOOST,
        Preset.AUTO,
    ),
    "RT7": _model_presets(
        Preset.SETPOINT,
        Preset.BOOST,
        Preset.ECO,
        Preset.COMFORT,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
    "SH_NTD": _model_presets(
        Preset.SETPOINT,
        Preset.BOOST,
        Preset.ECO,
        Preset.COMFORT,
        Preset.AUTO,
        Preset.ANTIFROST,
        Preset.STANDBY,
    ),
}


def _legacy_table(
    name: str, module: str
) -> dict[str, int] | dict[int, str] | dict[str, Preset]:
    """Provide deprecated legacy tables without mixing reverse code spaces."""
    legacy: dict[str, dict[str, int] | dict[int, str] | dict[str, Preset]] = {
        "PRESET_BY_KEY": {preset.key: preset for preset in Preset},
        "PRESET_MODE_MAP": _model_presets(
            Preset.COMFORT,
            Preset.ECO,
            Preset.ANTIFROST,
            Preset.STANDBY,
            Preset.BOOST,
            Preset.SETPOINT,
            Preset.COMFORT_PLUS,
            Preset.AUTO,
        ),
        "PRESET_MODE_SELECT_EXTRAS": _model_presets(Preset.ECO_1, Preset.ECO_2),
        "PRESET_MODE_MAP_RELAIS": PRESET_MODE_MODELS["EWS_RELAIS"].copy(),
        "REVERSE_PRESET_MODE_MAP_RELAIS": PRESET_MODE_MODELS["EWS_RELAIS"].reverse,
        "PRESET_MODE_MAP_UFH": PRESET_MODE_MODELS["UFH"].copy(),
        "REVERSE_PRESET_MODE_MAP_UFH": PRESET_MODE_MODELS["UFH"].reverse,
    }
    if name not in legacy:
        raise AttributeError(f"module {module!r} has no attribute {name!r}")
    warnings.warn(
        f"{name} is deprecated; use Preset or PRESET_MODE_MODELS[model]",
        DeprecationWarning,
        stacklevel=3,
    )
    return legacy[name]


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
