from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "运行现实MD多链烟雾.py"
SPEC = importlib.util.spec_from_file_location("multichain_smoke", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _graphs() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "formulation_id": "large",
                "canonical_smiles": "CCO",
                "atom_count": 9,
                "chemical_graph_status": "completed",
                "performance_claim_status": "no_performance_claim",
            },
            {
                "formulation_id": "small",
                "canonical_smiles": "O",
                "atom_count": 3,
                "chemical_graph_status": "completed",
                "performance_claim_status": "no_performance_claim",
            },
        ]
    )


def test_selects_smallest_graph_deterministically() -> None:
    selected = MODULE.select_graph(_graphs(), None)
    assert selected["formulation_id"] == "small"


def test_explicit_formulation_is_honored() -> None:
    selected = MODULE.select_graph(_graphs(), "large")
    assert selected["formulation_id"] == "large"


def test_unknown_formulation_fails_closed() -> None:
    with pytest.raises(ValueError, match="未找到唯一可用配方"):
        MODULE.select_graph(_graphs(), "missing")


def test_alternate_summary_is_order_independent() -> None:
    first = MODULE.summarize_alternates("Using alternate B\nUsing alternate A\n")
    second = MODULE.summarize_alternates("Using alternate A\nUsing alternate B\n")
    assert first == second
    assert first["alternate_parameter_unique_count"] == 2


def test_safe_text_escapes_filesystem_surrogates() -> None:
    assert MODULE._safe_text("bad\udca4name") == "bad\\udca4name"


def test_lammps_log_parser_detects_max_iteration_nonconvergence(tmp_path: Path) -> None:
    log = tmp_path / "lammps.log"
    log.write_text(
        "Loop time of 1.2 on 1 procs\n"
        "Stopping criterion = max iterations\n"
        "Energy initial, next-to-last, final =\n171.0 55.0 54.9\n"
        "Force two-norm initial, final = 282.1 0.89\n"
        "Loop time of 0.2 on 1 procs\n",
        encoding="utf-8",
    )
    summary = MODULE.parse_lammps_log(log)
    assert summary["normal_loop_count"] == 2
    assert summary["minimization_converged"] is False
    assert summary["minimization_stopping_criterion"] == "max iterations"
    assert summary["minimization_energy_initial_next_final_kcal_mol"][-1] == 54.9
