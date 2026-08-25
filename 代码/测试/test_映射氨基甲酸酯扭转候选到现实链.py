from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "映射氨基甲酸酯扭转候选到现实链.py"
SPEC = importlib.util.spec_from_file_location("chain_torsion_mapping", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _coefficients() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "validation_family": "aliphatic_urethane",
                "periodicity": periodicity,
                "coefficient_for_cos_nphi_minus_one_kcal_mol": value,
                "amber_candidate_magnitude_kcal_mol": abs(value),
                "amber_candidate_phase_degrees": 0.0,
                "fourier_order": 2,
            }
            for periodicity, value in [(1, 1.0), (2, 2.0)]
        ]
        + [
            {
                "validation_family": "aromatic_urethane",
                "periodicity": 1,
                "coefficient_for_cos_nphi_minus_one_kcal_mol": 0.5,
                "amber_candidate_magnitude_kcal_mol": 0.5,
                "amber_candidate_phase_degrees": 0.0,
                "fourier_order": 1,
            }
        ]
    )


def test_mixed_chain_maps_one_aliphatic_and_one_aromatic_torsion() -> None:
    mapping, summary = MODULE.map_chain_torsions(
        "f", "COC(=O)NCCOC(=O)Nc1ccccc1", _coefficients()
    )
    assert summary["urethane_occurrence_count"] == 2
    assert summary["aliphatic_urethane_occurrences"] == 1
    assert summary["aromatic_urethane_occurrences"] == 1
    assert summary["coefficient_term_assignment_count"] == 3
    assert len(mapping) == 3
    assert set(mapping["torsion_elements"]) == {"O-C-N-C"}


def test_coefficients_must_contain_both_families() -> None:
    only_aliphatic = _coefficients().query(
        "validation_family == 'aliphatic_urethane'"
    )
    with pytest.raises(ValueError, match="同时包含"):
        MODULE.validate_coefficients(only_aliphatic)


def test_coefficients_cannot_exceed_second_order() -> None:
    coefficients = _coefficients()
    extra = coefficients.iloc[[0]].copy()
    extra["periodicity"] = 3
    extra["fourier_order"] = 3
    coefficients.loc[
        coefficients.validation_family.eq("aliphatic_urethane"), "fourier_order"
    ] = 3
    with pytest.raises(ValueError, match="超过二阶"):
        MODULE.validate_coefficients(pd.concat([coefficients, extra], ignore_index=True))
