from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "运行RESP敏感性批次.py"
SPEC = importlib.util.spec_from_file_location("resp_sensitivity_batch", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_plan_is_four_by_three_by_three() -> None:
    plan = MODULE.build_plan(MODULE.DEFAULT_SEEDS, MODULE.DEFAULT_DENSITIES)
    assert len(plan) == 36
    assert plan["task_id"].is_unique
    assert plan["validation_family"].nunique() == 4
    assert plan["random_seed"].nunique() == 3
    assert plan["vdw_point_density"].nunique() == 3


def test_plan_is_deterministic() -> None:
    first = MODULE.build_plan([2, 1], [2.0, 1.0])
    second = MODULE.build_plan([1, 2], [1.0, 2.0])
    assert first.to_csv(index=False) == second.to_csv(index=False)


@pytest.mark.parametrize(
    ("seeds", "densities"),
    [([], [1.0]), ([1], []), ([1, 1], [1.0]), ([1], [1.0, 1.0]), ([0], [1.0]), ([1], [0.0])],
)
def test_invalid_plan_fails_closed(seeds: list[int], densities: list[float]) -> None:
    with pytest.raises(ValueError):
        MODULE.build_plan(seeds, densities)
