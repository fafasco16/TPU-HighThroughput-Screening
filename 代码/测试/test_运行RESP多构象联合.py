from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "运行RESP多构象联合.py"
SPEC = importlib.util.spec_from_file_location("joint_resp", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_seed_validation_is_sorted_and_deterministic() -> None:
    assert MODULE.validate_seeds([3, 1, 2]) == [1, 2, 3]


@pytest.mark.parametrize("seeds", [[], [1], [1, 1], [0, 1]])
def test_invalid_joint_seed_sets_fail_closed(seeds: list[int]) -> None:
    with pytest.raises(ValueError):
        MODULE.validate_seeds(seeds)
