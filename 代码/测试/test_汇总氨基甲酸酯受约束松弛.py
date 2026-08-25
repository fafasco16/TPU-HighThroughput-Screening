from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "汇总氨基甲酸酯受约束松弛.py"
SPEC = importlib.util.spec_from_file_location("relaxed_scan_summary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rigid_relaxed_join_and_change() -> None:
    rigid = pd.DataFrame(
        [
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "requested_angle_degrees": 0,
                "dft_relative_energy_kcal_mol": 2.0,
                "gaff2_relative_energy_kcal_mol": 3.0,
                "gaff2_minus_dft_relative_energy_kcal_mol": 1.0,
            }
        ]
    )
    relaxed = pd.DataFrame(
        [
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "requested_angle_degrees": 0,
                "relaxed_dft_relative_energy_kcal_mol": 0.5,
                "point_status": "completed",
            }
        ]
    )
    joined = MODULE.join_rigid_relaxed(rigid, relaxed)
    assert joined.iloc[0]["relaxation_change_from_rigid_dft_kcal_mol"] == pytest.approx(-1.5)


def test_missing_rigid_point_fails_closed() -> None:
    rigid = pd.DataFrame(
        columns=[
            "fragment_name",
            "validation_family",
            "requested_angle_degrees",
            "dft_relative_energy_kcal_mol",
            "gaff2_relative_energy_kcal_mol",
            "gaff2_minus_dft_relative_energy_kcal_mol",
        ]
    )
    relaxed = pd.DataFrame(
        [{"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 0, "point_status": "completed"}]
    )
    with pytest.raises(ValueError, match="未完全连接"):
        MODULE.join_rigid_relaxed(rigid, relaxed)


def _attempt(angle: int, status: str, energy: float | None, kind: str) -> dict:
    return {
        "fragment_name": "f",
        "validation_family": "aliphatic_urethane",
        "requested_angle_degrees": angle,
        "selection_reason": "test",
        "point_status": status,
        "relaxed_dft_energy_hartree": energy,
        "attempt_kind": kind,
        "attempt_release_id": f"release-{kind}-{angle}",
        "optimizer_profile": (
            "difficult_v2" if kind == "retry_v2" else "standard_v1"
        ),
        "geom_maxiter": 200 if kind == "retry_v2" else 50,
    }


def test_retry_replaces_only_failed_point_and_rezeros_family_energy() -> None:
    base = pd.DataFrame(
        [
            _attempt(0, "completed", -10.0, "base_v1"),
            _attempt(90, "failed_OptimizationConvergenceError", None, "base_v1"),
        ]
    )
    base.loc[base["requested_angle_degrees"].eq(90), "error_message"] = (
        "v1 did not converge"
    )
    retry = pd.DataFrame([_attempt(90, "completed", -9.99, "retry_v2")])
    selected, audit = MODULE.reconcile_relaxed_attempts(base, retry)
    zero = selected.loc[selected["requested_angle_degrees"].eq(0)].iloc[0]
    ninety = selected.loc[selected["requested_angle_degrees"].eq(90)].iloc[0]
    assert zero["selected_attempt"] == "base_v1"
    assert ninety["selected_attempt"] == "retry_v2"
    assert ninety["base_point_status"].startswith("failed_")
    assert ninety["retry_point_status"] == "completed"
    assert pd.isna(ninety["error_message"])
    assert float(zero["relaxed_dft_relative_energy_kcal_mol"]) == pytest.approx(0.0)
    assert float(ninety["relaxed_dft_relative_energy_kcal_mol"]) == pytest.approx(
        0.01 * 627.5094740631
    )
    assert audit["selected_for_release"].sum() == 2


def test_retry_cannot_overwrite_completed_v1_point() -> None:
    base = pd.DataFrame([_attempt(0, "completed", -10.0, "base_v1")])
    retry = pd.DataFrame([_attempt(0, "completed", -10.1, "retry_v2")])
    with pytest.raises(ValueError, match="不得覆盖"):
        MODULE.reconcile_relaxed_attempts(base, retry)


def test_retry_requires_difficult_v2_profile() -> None:
    base = pd.DataFrame(
        [_attempt(90, "failed_OptimizationConvergenceError", None, "base_v1")]
    )
    retry = pd.DataFrame([_attempt(90, "completed", -9.9, "retry_v2")])
    retry.loc[0, "optimizer_profile"] = "standard_v1"
    with pytest.raises(ValueError, match="difficult_v2"):
        MODULE.reconcile_relaxed_attempts(base, retry)
