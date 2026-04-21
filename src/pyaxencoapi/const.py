"""Constants for the PyAxencoAPI library."""

from enum import Enum


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
    """Preset map object exposing a reverse code->key view."""

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
}