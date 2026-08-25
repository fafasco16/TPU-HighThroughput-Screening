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
