from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "汇总氨基甲酸酯刚性扫描.py"
SPEC = importlib.util.spec_from_file_location("rigid_scan_summary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rigid_screen_failure_gate() -> None:
    assert MODULE.classify_rigid_scan(
        {"curve_rmse_kcal_mol": 9.0, "barrier_difference_gaff2_minus_dft_kcal_mol": 1.0, "curve_pearson_r": 0.95}
    ).startswith("failed")
    assert MODULE.classify_rigid_scan(
        {"curve_rmse_kcal_mol": 3.0, "barrier_difference_gaff2_minus_dft_kcal_mol": 1.0, "curve_pearson_r": 0.9}
    ).startswith("conditional")


def test_relaxed_candidate_selection_keeps_complementary_anchors() -> None:
    rows = []
    for angle, dft, mm in [(-180, 5.0, 20.0), (-90, 3.0, 4.0), (0, 0.0, 0.0), (90, 4.0, 12.0), (150, 2.0, 1.0)]:
        rows.append({"fragment_name": "f", "validation_family": "aliphatic_urethane", "requested_angle_degrees": angle, "dft_relative_energy_kcal_mol": dft, "gaff2_relative_energy_kcal_mol": mm})
    selected = MODULE.select_relaxed_angles(pd.DataFrame(rows))
    assert set(selected["angle_degrees"]) == {-180, 0, 90, 150}
