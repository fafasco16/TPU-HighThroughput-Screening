from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "运行氨基甲酸酯受约束松弛.py"
SPEC = importlib.util.spec_from_file_location("relaxed_urethane_scan", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _plan() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"fragment_name": "a", "validation_family": "aliphatic_urethane", "angle_degrees": angle, "selection_reason": "test"}
            for angle in [-180, 0, 90, 150]
        ]
    )


def test_plan_selection_filters_fragment_and_angles() -> None:
    selected = MODULE.select_plan_rows(_plan(), "a", explicit_angles=[0, 90])
    assert selected["angle_degrees"].tolist() == [0, 90]


def test_empty_plan_selection_fails_closed() -> None:
    with pytest.raises(ValueError, match="为空"):
        MODULE.select_plan_rows(_plan(), "missing")


def test_duplicate_angles_fail_closed() -> None:
    duplicated = pd.concat([_plan(), _plan().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="重复"):
        MODULE.select_plan_rows(duplicated, "a")


def test_standard_optimizer_profile_preserves_v1_options() -> None:
    options = MODULE.build_optking_options("1 2 3 4", 50, "standard_v1")
    assert options == {
        "optking__frozen_dihedral": "1 2 3 4",
        "optking__geom_maxiter": 50,
        "optking__g_convergence": "QCHEM",
    }


def test_difficult_optimizer_profile_adds_documented_robustness_options() -> None:
    options = MODULE.build_optking_options("1 2 3 4", 200, "difficult_v2")
    assert options["optking__geom_maxiter"] == 200
    assert options["optking__dynamic_level"] == 1
    assert options["optking__opt_coordinates"] == "BOTH"
    assert options["optking__intrafrag_step_limit"] == pytest.approx(0.1)


def test_unknown_optimizer_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="未知OptKing优化策略"):
        MODULE.build_optking_options("1 2 3 4", 200, "invented")


def test_zero_wall_clock_limit_is_noop() -> None:
    with MODULE.point_wall_clock_limit(0):
        value = 2 + 2
    assert value == 4


def test_negative_wall_clock_limit_fails_closed() -> None:
    with pytest.raises(ValueError, match="不能为负"):
        with MODULE.point_wall_clock_limit(-1):
            pass


def test_checkpoint_counts_completed_failed_and_remaining() -> None:
    checkpoint = MODULE.build_checkpoint(
        {"release_id": "r"},
        [
            {"requested_angle_degrees": 0, "point_status": "completed"},
            {"requested_angle_degrees": 60, "point_status": "failed_TimeoutError"},
        ],
        6,
    )
    assert checkpoint["counts"] == {
        "planned": 6,
        "attempted": 2,
        "completed": 1,
        "failed": 1,
        "remaining": 4,
    }
    assert checkpoint["last_attempted_angle_degrees"] == 60
