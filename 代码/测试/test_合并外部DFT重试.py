from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "合并外部DFT重试.py"
SPEC = importlib.util.spec_from_file_location("merge_external_retry", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_expected_external_grid_has_six_unique_angles() -> None:
    assert MODULE.EXPECTED_ANGLES == {-180, -120, -60, 0, 60, 120}


def test_reconciliation_helper_accepts_hessian_difficult_retry() -> None:
    base = pd.DataFrame(
        [
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "requested_angle_degrees": 0,
                "point_status": "completed",
                "relaxed_dft_energy_hartree": -1.1,
                "attempt_kind": "base_v1",
                "attempt_release_id": "base",
                "optimizer_profile": "difficult_v2",
                "geom_maxiter": 200,
            },
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "requested_angle_degrees": -120,
                "point_status": "failed_OptimizationConvergenceError",
                "relaxed_dft_energy_hartree": pd.NA,
                "attempt_kind": "base_v1",
                "attempt_release_id": "base",
                "optimizer_profile": "difficult_v2",
                "geom_maxiter": 200,
            }
        ]
    )
    retry = base.loc[base["requested_angle_degrees"].eq(-120)].copy()
    retry["point_status"] = "completed"
    retry["relaxed_dft_energy_hartree"] = -1.0
    retry["attempt_kind"] = "retry_v2"
    retry["attempt_release_id"] = "retry"
    retry["optimizer_profile"] = "difficult_hessian_v3"
    from 汇总氨基甲酸酯受约束松弛 import reconcile_relaxed_attempts

    selected, audit = reconcile_relaxed_attempts(base, retry)
    retried = selected.loc[selected["requested_angle_degrees"].eq(-120)].iloc[0]
    assert retried["point_status"] == "completed"
    assert retried["selected_attempt"] == "retry_v2"
    assert len(audit) == 3
