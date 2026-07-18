"""TPU 数据库使用的显式单位白名单与规范化转换。

该模块刻意不做模糊匹配或单位猜测。调用者必须提供白名单中的原始
单位；不认识的写法会立即失败，避免在数据管道中静默产生量纲错误。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np


# 转换参数为 ``规范值 = 原始值 * factor + offset``。
_STRESS_TO_MPA = {
    "Pa": (1e-6, 0.0),
    "kPa": (1e-3, 0.0),
    "MPa": (1.0, 0.0),
    "GPa": (1e3, 0.0),
}

_STRAIN_TO_FRACTION = {
    "1": (1.0, 0.0),
    "fraction": (1.0, 0.0),
    "m/m": (1.0, 0.0),
    "mm/mm": (1.0, 0.0),
    "%": (1e-2, 0.0),
    "percent": (1e-2, 0.0),
}

_TEMPERATURE_TO_K = {
    "K": (1.0, 0.0),
    "degC": (1.0, 273.15),
    "\N{DEGREE SIGN}C": (1.0, 273.15),
    "C": (1.0, 273.15),
}

_TIME_TO_SECONDS = {
    "us": (1e-6, 0.0),
    "\N{MICRO SIGN}s": (1e-6, 0.0),
    "ms": (1e-3, 0.0),
    "s": (1.0, 0.0),
    "sec": (1.0, 0.0),
    "min": (60.0, 0.0),
    "h": (3_600.0, 0.0),
}

_FREQUENCY_TO_HZ = {
    "mHz": (1e-3, 0.0),
    "Hz": (1.0, 0.0),
    "kHz": (1e3, 0.0),
    "MHz": (1e6, 0.0),
}

_VISCOSITY_TO_PA_S = {
    "Pa\N{MIDDLE DOT}s": (1.0, 0.0),
    "Pa*s": (1.0, 0.0),
    "Pa s": (1.0, 0.0),
    "Pa.s": (1.0, 0.0),
    "mPa\N{MIDDLE DOT}s": (1e-3, 0.0),
    "mPa*s": (1e-3, 0.0),
    "mPa s": (1e-3, 0.0),
    "mPa.s": (1e-3, 0.0),
    "cP": (1e-3, 0.0),
    "P": (1e-1, 0.0),
}


UNIT_WHITELISTS: dict[str, tuple[str, ...]] = {
    "stress": tuple(_STRESS_TO_MPA),
    "strain": tuple(_STRAIN_TO_FRACTION),
    "temperature": tuple(_TEMPERATURE_TO_K),
    "time": tuple(_TIME_TO_SECONDS),
    "frequency": tuple(_FREQUENCY_TO_HZ),
    "viscosity": tuple(_VISCOSITY_TO_PA_S),
}

CANONICAL_UNITS = {
    "stress": "MPa",
    "strain": "1",
    "temperature": "K",
    "time": "s",
    "frequency": "Hz",
    "viscosity": "Pa\N{MIDDLE DOT}s",
}


def _convert(
    value: Any,
    unit: str,
    quantity: str,
    conversions: dict[str, tuple[float, float]],
) -> float | np.ndarray:
    """Apply one exact whitelist conversion while preserving scalar shape."""

    if not isinstance(unit, str):
        raise ValueError(f"Unit for {quantity} must be a string, got {type(unit).__name__}")
    if unit not in conversions:
        allowed = ", ".join(repr(item) for item in conversions)
        raise ValueError(
            f"Unsupported {quantity} unit {unit!r}. Allowed units: {allowed}"
        )

    try:
        raw = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{quantity} value must be numeric") from exc

    factor, offset = conversions[unit]
    converted = raw * factor + offset
    if converted.ndim == 0:
        return float(converted)
    return converted


def stress_to_mpa(value: Any, unit: str) -> float | np.ndarray:
    """Convert a stress value or array to MPa."""

    return _convert(value, unit, "stress", _STRESS_TO_MPA)


def strain_to_fraction(value: Any, unit: str) -> float | np.ndarray:
    """Convert engineering strain to a dimensionless fraction."""

    return _convert(value, unit, "strain", _STRAIN_TO_FRACTION)


def temperature_to_k(value: Any, unit: str) -> float | np.ndarray:
    """Convert temperature to kelvin."""

    return _convert(value, unit, "temperature", _TEMPERATURE_TO_K)


def time_to_seconds(value: Any, unit: str) -> float | np.ndarray:
    """Convert elapsed time to seconds."""

    return _convert(value, unit, "time", _TIME_TO_SECONDS)


def frequency_to_hz(value: Any, unit: str) -> float | np.ndarray:
    """Convert cyclic frequency to hertz."""

    return _convert(value, unit, "frequency", _FREQUENCY_TO_HZ)


def viscosity_to_pa_s(value: Any, unit: str) -> float | np.ndarray:
    """Convert dynamic viscosity to Pa\N{MIDDLE DOT}s."""

    return _convert(value, unit, "viscosity", _VISCOSITY_TO_PA_S)


_CONVERTERS: dict[str, Callable[[Any, str], float | np.ndarray]] = {
    "stress": stress_to_mpa,
    "strain": strain_to_fraction,
    "temperature": temperature_to_k,
    "time": time_to_seconds,
    "frequency": frequency_to_hz,
    "viscosity": viscosity_to_pa_s,
}


def normalize_measurement(value: Any, unit: str, quantity: str) -> dict[str, Any]:
    """Return raw and normalized representations of one measurement.

    The original object is deliberately retained as ``raw_value`` so an adapter
    can write both raw and canonical columns without reconstructing provenance.
    """

    if quantity not in _CONVERTERS:
        allowed = ", ".join(_CONVERTERS)
        raise ValueError(
            f"Unsupported quantity {quantity!r}. Allowed quantities: {allowed}"
        )
    return {
        "raw_value": value,
        "raw_unit": unit,
        "normalized_value": _CONVERTERS[quantity](value, unit),
        "normalized_unit": CANONICAL_UNITS[quantity],
    }


__all__ = [
    "CANONICAL_UNITS",
    "UNIT_WHITELISTS",
    "frequency_to_hz",
    "normalize_measurement",
    "strain_to_fraction",
    "stress_to_mpa",
    "temperature_to_k",
    "time_to_seconds",
    "viscosity_to_pa_s",
]

