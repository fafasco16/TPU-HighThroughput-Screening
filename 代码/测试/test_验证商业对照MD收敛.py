from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "验证商业对照MD收敛.py"
SPEC = importlib.util.spec_from_file_location("commercial_md_convergence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _trajectory(
    formulation_id: str = "f",
    replica_index: int = 1,
    density_drift_per_ns: float = 0.0,
) -> pd.DataFrame:
    time_ps = np.linspace(0.0, 12_000.0, 1201)
    time_ns = time_ps / 1000.0
    phase = 2 * np.pi * time_ps / 500.0
    density = 1.10 + density_drift_per_ns * time_ns + 0.001 * np.sin(phase)
    return pd.DataFrame(
        {
            "formulation_id": formulation_id,
            "replica_index": replica_index,
            "time_ps": time_ps,
            "density_g_cm3": density,
            "potential_energy_kcal_mol": -1000.0 + np.sin(phase),
            "volume_a3": 100_000.0 / density,
            "radius_of_gyration_a": 20.0 + 0.01 * np.sin(phase),
            "end_to_end_distance_a": 40.0 + 0.02 * np.cos(phase),
            "temperature_k": 300.0 + 0.1 * np.sin(phase),
            "pressure_atm": 1.0 + 0.05 * np.cos(phase),
        }
    )


def test_stable_replica_passes_all_gates() -> None:
    metrics = MODULE.analyze_replica(_trajectory())
    assert metrics["replica_convergence_pass"]
    assert metrics["duration_ps"] == pytest.approx(12_000.0)
    assert metrics["density_slope_fraction_per_ns"] < 0.001


def test_density_drift_fails_slope_gate() -> None:
    metrics = MODULE.analyze_replica(_trajectory(density_drift_per_ns=0.002))
    assert not metrics["density_slope_gate"]
    assert not metrics["replica_convergence_pass"]


def test_three_stable_replicates_pass_system_gate() -> None:
    metrics = pd.DataFrame(
        [
            MODULE.analyze_replica(_trajectory("f", replica))
            for replica in [1, 2, 3]
        ]
    )
    systems = MODULE.summarize_systems(metrics)
    assert systems.iloc[0]["system_convergence_pass"]
    assert systems.iloc[0]["replicates_passed"] == 3


def test_system_gate_requires_exactly_three_replicates() -> None:
    metrics = pd.DataFrame(
        [MODULE.analyze_replica(_trajectory("f", replica)) for replica in [1, 2]]
    )
    with pytest.raises(ValueError, match="重复数不闭合"):
        MODULE.summarize_systems(metrics)
