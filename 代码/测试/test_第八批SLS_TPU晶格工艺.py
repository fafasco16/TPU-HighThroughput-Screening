"""Figshare SLS-TPU 晶格工艺数据的定向回归测试。"""

from __future__ import annotations

import importlib.util
import math
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第八批SLS_TPU晶格工艺.py"
SPEC = importlib.util.spec_from_file_location("batch8_sls_tpu_lattice", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    if not audit.SOURCE_XLSX.is_file():
        pytest.skip("Figshare SLS-TPU 原始工作簿不在当前检出中")
    return audit.run_audit(write_outputs=False)


def test_frozen_identity_open_license_and_protocol(result: dict[str, object]) -> None:
    assert audit.DOI == "10.6084/m9.figshare.31550614.v1"
    assert audit.LICENSE == "CC BY 4.0"
    assert len(result["files"]) == 3
    protocol = result["summary"]["protocol_evidence"]
    assert protocol["l25_conditions"] == 25
    assert protocol["replicates_per_condition"] == 3
    assert protocol["fixed_layer_thickness_mm"] == 0.1
    assert protocol["additional_scan_speed_validation_specimens_without_rows"] == 14


def test_specimen_condition_and_endpoint_counts(result: dict[str, object]) -> None:
    counts = result["summary"]["counts"]
    assert counts == {
        "independent_specimens": 75,
        "process_conditions": 25,
        "replicates_per_condition": 3,
        "scalar_records": 375,
        "unit_closed_scalar_records": 300,
        "unit_unresolved_scalar_records": 75,
        "admitted_reference_scalar_records": 300,
        "conditional_reference_scalar_records": 75,
        "nonredundant_unit_closed_scalar_records": 225,
        "repeat_leakage_groups": 25,
        "material_geometry_leakage_groups": 1,
        "resolved_formulations": 0,
        "base_material_systems": 1,
        "lattice_topology_geometry_systems": 1,
    }
    assert Counter(row["observable"] for row in result["scalars"]) == {
        "compressive_load_at_25_percent_deflection": 75,
        "compressive_load_at_65_percent_deflection": 75,
        "sag_factor": 75,
        "hysteresis_loss_ratio": 75,
        "specimen_weight": 75,
    }


def test_units_admission_and_future_weight_are_not_conflated(
    result: dict[str, object],
) -> None:
    scalars = result["scalars"]
    closed = [row for row in scalars if row["unit_status"] == "closed"]
    unresolved = [row for row in scalars if row["unit_status"] == "unresolved"]
    assert len(closed) == 300
    assert {row["unit"] for row in closed} == {"N", "1"}
    assert {row["gold_admission_status"] for row in closed} == {
        "admitted_reference"
    }
    assert len(unresolved) == 75
    assert {row["observable"] for row in unresolved} == {"specimen_weight"}
    assert {row["unit"] for row in unresolved} == {""}
    assert {row["gold_admission_status"] for row in unresolved} == {
        "conditional_reference"
    }
    assert max(float(row["future_weight_ceiling"]) for row in scalars) == 0.35
    assert min(float(row["future_weight_ceiling"]) for row in scalars) == 0.10


def test_replicates_share_condition_group_and_base_system_group(
    result: dict[str, object],
) -> None:
    scalars = result["scalars"]
    assert len({row["specimen_id"] for row in scalars}) == 75
    assert len({row["condition_id"] for row in scalars}) == 25
    assert len({row["repeat_leakage_group"] for row in scalars}) == 25
    assert len({row["material_geometry_leakage_group"] for row in scalars}) == 1
    specimen_group_pairs = {
        (row["specimen_id"], row["repeat_leakage_group"]) for row in scalars
    }
    assert len(specimen_group_pairs) == 75
    groups_by_condition: dict[str, set[str]] = {}
    for row in scalars:
        groups_by_condition.setdefault(row["condition_id"], set()).add(
            row["repeat_leakage_group"]
        )
    assert all(len(groups) == 1 for groups in groups_by_condition.values())


def test_areal_and_volumetric_energy_density_are_both_explicit(
    result: dict[str, object],
) -> None:
    for row in result["scalars"]:
        power = float(row["laser_power_w"])
        speed = float(row["scan_speed_mm_s"])
        hatch = float(row["hatch_distance_mm"])
        thickness = float(row["layer_thickness_mm"])
        areal = float(row["energy_density_areal_j_mm2"])
        volumetric = float(row["energy_density_volumetric_j_mm3"])
        assert math.isclose(areal, power / (speed * hatch), abs_tol=1e-12)
        assert math.isclose(volumetric, areal / thickness, abs_tol=1e-12)
    energy = result["summary"]["energy_density_semantics"]
    assert energy["official_formula_dimension"] == "areal"
    assert energy["supplement_wording_conflict"] is True
    assert energy["derived_volumetric_values_are_observations"] is False


def test_raw_processed_reconciliation_and_deterministic_outputs(
    result: dict[str, object],
) -> None:
    checks = result["summary"]["reconciliation_checks"]
    assert checks["max_abs_raw_processed_endpoint_error"] < 1e-12
    assert checks["max_abs_processed_average_error"] < 1e-12
    # SAG 与 Load@65%/Load@25% 高度一致，但原件保留了小幅非零差；
    # 不把它强行重算成完全相等，以免改写作者数据。
    assert 0 < checks["max_abs_sag_ratio_error"] < 0.003
    assert checks["max_abs_areal_energy_density_error"] < 1e-12
    assert checks["max_abs_duplicate_load_column_error"] < 1e-12
    assert checks["exact_duplicate_four_endpoint_vectors"] == 0
    assert set(result["outputs"]) == set(audit.OUTPUT_NAMES)
    rerun = audit.run_audit(write_outputs=False)
    assert rerun["outputs"] == result["outputs"]
    for name, payload in result["outputs"].items():
        checked = audit.SOURCE_DIR / name
        if checked.is_file():
            assert checked.read_bytes() == payload
