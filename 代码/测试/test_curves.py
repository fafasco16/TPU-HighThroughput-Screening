import numpy as np
import pytest

from curves import ALGORITHM_VERSION, cycle_metrics, tensile_metrics, trapezoid_integral


def test_toughness_units_mpa_times_fraction_equals_mj_per_m3():
    result = tensile_metrics(np.array([0.0, 1.0]), np.array([0.0, 10.0]))
    assert result["toughness_mj_m3"] == pytest.approx(5.0)
    assert result["tensile_strength_mpa"] == pytest.approx(10.0)
    assert result["elongation_at_break"] == pytest.approx(1.0)
    assert result["units"]["toughness_mj_m3"] == "MJ/m^3"


def test_tensile_metrics_include_audit_metadata():
    result = tensile_metrics([0.0, 0.5, 1.0], [0.0, 4.0, 8.0])
    assert result["algorithm_version"] == ALGORITHM_VERSION
    assert result["integration_points"] == 3
    assert result["warnings"] == []
    assert result["units"] == {
        "toughness_mj_m3": "MJ/m^3",
        "tensile_strength_mpa": "MPa",
        "elongation_at_break": "1",
    }


def test_cycle_hysteresis_is_loading_minus_unloading_area():
    result = cycle_metrics(
        load_strain=np.array([0.0, 1.0]),
        load_stress=np.array([0.0, 10.0]),
        unload_strain=np.array([1.0, 0.0]),
        unload_stress=np.array([10.0, 0.0]),
    )
    assert result["loading_area_mj_m3"] == pytest.approx(5.0)
    assert result["unloading_area_mj_m3"] == pytest.approx(5.0)
    assert result["hysteresis_mj_m3"] == pytest.approx(0.0)
    assert "unloading_reversed_to_ascending" in result["warnings"]


def test_unloading_direction_does_not_change_cycle_metrics():
    descending = cycle_metrics([0.0, 1.0], [0.0, 10.0], [1.0, 0.2], [5.0, 0.0])
    ascending = cycle_metrics([0.0, 1.0], [0.0, 10.0], [0.2, 1.0], [0.0, 5.0])
    for key in (
        "unloading_area_mj_m3",
        "hysteresis_mj_m3",
        "residual_strain",
        "strain_recovery_ratio",
        "energy_return_ratio",
    ):
        assert descending[key] == pytest.approx(ascending[key])
    assert descending["hysteresis_mj_m3"] == pytest.approx(3.0)
    assert descending["residual_strain"] == pytest.approx(0.2)
    assert descending["strain_recovery_ratio"] == pytest.approx(0.8)
    assert descending["energy_return_ratio"] == pytest.approx(0.4)


def test_cycle_metrics_include_units_counts_and_version():
    result = cycle_metrics([0.0, 1.0], [0.0, 10.0], [1.0, 0.0], [5.0, 0.0])
    assert result["algorithm_version"] == ALGORITHM_VERSION
    assert result["integration_points"] == {"loading": 2, "unloading": 2}
    assert result["units"]["hysteresis_mj_m3"] == "MJ/m^3"
    assert result["units"]["residual_strain"] == "1"


@pytest.mark.parametrize(
    ("strain", "stress", "message"),
    [
        ([0.0], [0.0], "at least 2"),
        ([0.0, 1.0], [0.0], "same length"),
        ([0.0, np.nan], [0.0, 1.0], "finite"),
        ([0.0, 1.0], [0.0, np.inf], "finite"),
        ([[0.0, 1.0]], [[0.0, 1.0]], "one-dimensional"),
    ],
)
def test_tensile_input_validation(strain, stress, message):
    with pytest.raises(ValueError, match=message):
        tensile_metrics(strain, stress)


def test_cycle_validates_both_branches():
    with pytest.raises(ValueError, match="unloading.*at least 2"):
        cycle_metrics([0.0, 1.0], [0.0, 1.0], [1.0], [1.0])
    with pytest.raises(ValueError, match="loading.*finite"):
        cycle_metrics([0.0, 1.0], [0.0, np.nan], [1.0, 0.0], [1.0, 0.0])


def test_nonnumeric_curve_input_is_rejected():
    with pytest.raises(ValueError, match="must be numeric"):
        tensile_metrics([0.0, "bad"], [0.0, 1.0])


def test_duplicate_strain_points_are_retained_and_warned():
    result = tensile_metrics([0.0, 0.5, 0.5, 1.0], [0.0, 5.0, 6.0, 10.0])
    assert result["toughness_mj_m3"] == pytest.approx(5.25)
    assert result["integration_points"] == 4
    assert "duplicate_strain_values" in result["warnings"]


def test_duplicate_cycle_points_warn_for_the_correct_branch():
    result = cycle_metrics(
        [0.0, 0.5, 0.5, 1.0],
        [0.0, 4.0, 5.0, 8.0],
        [1.0, 0.5, 0.5, 0.0],
        [4.0, 2.0, 1.0, 0.0],
    )
    assert "duplicate_loading_strain_values" in result["warnings"]
    assert "duplicate_unloading_strain_values" in result["warnings"]


def test_tensile_curve_must_not_run_backwards():
    with pytest.raises(ValueError, match="non-decreasing"):
        tensile_metrics([0.0, 1.0, 0.5], [0.0, 10.0, 5.0])


def test_loading_curve_must_not_run_backwards():
    with pytest.raises(ValueError, match="loading strain must be non-decreasing"):
        cycle_metrics([0.0, 1.0, 0.5], [0.0, 10.0, 5.0], [1.0, 0.0], [5.0, 0.0])


def test_unloading_curve_must_have_one_direction():
    with pytest.raises(ValueError, match="single monotonic direction"):
        cycle_metrics(
            [0.0, 1.0],
            [0.0, 10.0],
            [1.0, 0.5, 0.75],
            [5.0, 1.0, 2.0],
        )


def test_warnings_report_non_origin_negative_values_and_peak_mismatch():
    tensile = tensile_metrics([0.1, 0.5], [-1.0, 5.0])
    assert tensile["warnings"] == [
        "curve_does_not_start_at_origin",
        "negative_stress_values",
    ]

    cycle = cycle_metrics([0.0, 1.0], [0.0, 10.0], [0.8, 0.0], [15.0, 0.0])
    assert "branch_peak_strain_mismatch" in cycle["warnings"]
    assert "negative_hysteresis" in cycle["warnings"]


def test_negative_branch_values_are_reported_without_point_deletion():
    tensile = tensile_metrics([-0.1, 0.0], [0.0, 1.0])
    assert "negative_strain_values" in tensile["warnings"]
    assert tensile["integration_points"] == 2

    cycle = cycle_metrics(
        [-0.1, 0.0],
        [-1.0, 0.0],
        [0.0, -0.2],
        [0.0, -1.0],
    )
    assert "negative_loading_strain_values" in cycle["warnings"]
    assert "negative_loading_stress_values" in cycle["warnings"]
    assert "negative_unloading_strain_values" in cycle["warnings"]
    assert "negative_unloading_stress_values" in cycle["warnings"]


def test_zero_loading_area_and_peak_return_none_ratios_with_warnings():
    result = cycle_metrics([0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0])
    assert result["energy_return_ratio"] is None
    assert result["strain_recovery_ratio"] is None
    assert "zero_loading_area" in result["warnings"]
    assert "zero_peak_strain" in result["warnings"]


def test_trapezoid_integral_is_signed_and_deterministic():
    forward = trapezoid_integral([0.0, 0.25, 1.0], [0.0, 4.0, 2.0])
    reverse = trapezoid_integral([1.0, 0.25, 0.0], [2.0, 4.0, 0.0])
    assert forward == pytest.approx(2.75)
    assert reverse == pytest.approx(-2.75)
    assert trapezoid_integral([0.0, 0.25, 1.0], [0.0, 4.0, 2.0]) == forward
