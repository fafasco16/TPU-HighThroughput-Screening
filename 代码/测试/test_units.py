import numpy as np
import pytest

from units import (
    CANONICAL_UNITS,
    UNIT_WHITELISTS,
    frequency_to_hz,
    normalize_measurement,
    strain_to_fraction,
    stress_to_mpa,
    temperature_to_k,
    time_to_seconds,
    viscosity_to_pa_s,
)


def test_core_unit_conversions():
    assert strain_to_fraction(100.0, "%") == pytest.approx(1.0)
    assert stress_to_mpa(1_000_000.0, "Pa") == pytest.approx(1.0)
    assert temperature_to_k(25.0, "degC") == pytest.approx(298.15)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1_000.0, "kPa", 1.0),
        (2.5, "MPa", 2.5),
        (0.003, "GPa", 3.0),
    ],
)
def test_stress_whitelist(value, unit, expected):
    assert stress_to_mpa(value, unit) == pytest.approx(expected)


def test_converters_accept_arrays_without_mutating_input():
    raw = np.array([0.0, 50.0, 100.0])
    converted = strain_to_fraction(raw, "%")
    np.testing.assert_allclose(converted, [0.0, 0.5, 1.0])
    np.testing.assert_allclose(raw, [0.0, 50.0, 100.0])


def test_temperature_time_frequency_and_viscosity_whitelists():
    assert temperature_to_k(300.0, "K") == pytest.approx(300.0)
    assert temperature_to_k(0.0, "\N{DEGREE SIGN}C") == pytest.approx(273.15)
    assert time_to_seconds(2.0, "min") == pytest.approx(120.0)
    assert time_to_seconds(500.0, "ms") == pytest.approx(0.5)
    assert frequency_to_hz(2.0, "kHz") == pytest.approx(2_000.0)
    assert viscosity_to_pa_s(1_000.0, "mPa\N{MIDDLE DOT}s") == pytest.approx(1.0)
    assert viscosity_to_pa_s(1_000.0, "cP") == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("converter", "unit"),
    [
        (stress_to_mpa, "psi"),
        (strain_to_fraction, "cm/cm"),
        (temperature_to_k, "F"),
        (time_to_seconds, "day"),
        (frequency_to_hz, "rad/s"),
        (viscosity_to_pa_s, "unknown"),
    ],
)
def test_unknown_units_are_rejected(converter, unit):
    with pytest.raises(ValueError, match="Unsupported"):
        converter(1.0, unit)


def test_empty_and_non_string_units_are_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        stress_to_mpa(1.0, "")
    with pytest.raises(ValueError, match="string"):
        stress_to_mpa(1.0, None)


def test_nonnumeric_measurement_is_rejected():
    with pytest.raises(ValueError, match="must be numeric"):
        stress_to_mpa("not-a-number", "MPa")


def test_normalize_measurement_preserves_raw_value_and_unit():
    record = normalize_measurement(25.0, "degC", "temperature")
    assert record == {
        "raw_value": 25.0,
        "raw_unit": "degC",
        "normalized_value": pytest.approx(298.15),
        "normalized_unit": "K",
    }


def test_normalize_measurement_supports_array_values():
    raw = np.array([1.0, 2.0])
    record = normalize_measurement(raw, "GPa", "stress")
    assert record["raw_value"] is raw
    assert record["raw_unit"] == "GPa"
    assert record["normalized_unit"] == "MPa"
    np.testing.assert_allclose(record["normalized_value"], [1_000.0, 2_000.0])


def test_unknown_quantity_is_rejected():
    with pytest.raises(ValueError, match="Unsupported quantity"):
        normalize_measurement(1.0, "m", "length")


def test_whitelists_and_canonical_units_cover_all_required_quantities():
    expected = {"stress", "strain", "temperature", "time", "frequency", "viscosity"}
    assert set(UNIT_WHITELISTS) == expected
    assert set(CANONICAL_UNITS) == expected
    assert all(UNIT_WHITELISTS[quantity] for quantity in expected)
