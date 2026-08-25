from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计现实MD生产参数门.py"
SPEC = importlib.util.spec_from_file_location("production_gate", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_repeating_urethane_substitution() -> None:
    row = MODULE.parse_alternate_message(
        "RadonPy debug info: Using alternate bond type c,n instead of c,ns"
    )
    assert row["parameter_class"] == "bond"
    assert row["requested_type"] == "c,ns"
    assert row["validation_family"] == "repeating_urethane_ns_substitution"


def test_parse_terminal_isocyanate_substitution() -> None:
    row = MODULE.parse_alternate_message(
        "RadonPy debug info: Using alternate angle type n2,c1,o instead of n2,cg,o"
    )
    assert row["validation_family"] == (
        "terminal_isocyanate_conjugated_type_substitution"
    )


def test_unparseable_message_fails_closed() -> None:
    with pytest.raises(ValueError, match="无法解析"):
        MODULE.parse_alternate_message("not a RadonPy parameter message")


def test_analysis_counts_and_correlation() -> None:
    audit = pd.DataFrame(
        [
            {
                "formulation_id": "a",
                "atom_count": 100,
                "assignment_status": "assigned_with_alternate_parameters",
                "alternate_parameter_line_count": 10,
                "alternate_parameter_unique_count": 2,
                "alternate_parameter_unique_messages": (
                    "Using alternate bond type c,n instead of c,ns | "
                    "Using alternate bond type c1,o instead of cg,o"
                ),
                "production_md_permission": "blocked",
            },
            {
                "formulation_id": "b",
                "atom_count": 200,
                "assignment_status": "assigned_with_alternate_parameters",
                "alternate_parameter_line_count": 20,
                "alternate_parameter_unique_count": 2,
                "alternate_parameter_unique_messages": (
                    "Using alternate bond type c,n instead of c,ns | "
                    "Using alternate bond type c1,o instead of cg,o"
                ),
                "production_md_permission": "blocked",
            },
        ]
    )
    plan = pd.DataFrame(
        [
            {"formulation_id": "a", "estimated_urethane_bond_count": 1},
            {"formulation_id": "b", "estimated_urethane_bond_count": 2},
        ]
    )
    detail, formulations, summary = MODULE.analyze_alternates(audit, plan)
    assert len(detail) == 2
    assert len(formulations) == 2
    assert summary["unique_substitution_count"] == 2
    assert summary["urethane_bond_vs_substitution_event_pearson_r"] == pytest.approx(1.0)


def test_forcefield_gate_tracks_chain_mapping_without_releasing_md() -> None:
    runtime = {
        "urethane_dft_mm_relaxed_comparison": {
            "status": "relaxed_dft_mm_comparison_completed_refit_candidate",
            "maximum_absolute_relaxed_energy_error_kcal_mol": 12.22,
        },
        "urethane_relaxed_torsion_candidate_fit": {
            "status": "relaxed_low_order_family_fit_completed_external_validation_pending"
        },
        "urethane_torsion_candidate_chain_mapping": {
            "status": "twelve_chain_torsion_candidate_mapping_completed_external_validation_pending",
            "counts": {
                "formulations": 12,
                "urethane_torsion_instances": 134,
            },
        },
    }
    assert MODULE.decide_forcefield_parameter_status(runtime) == (
        "candidate_mapped_to_twelve_chains_external_validation_pending"
    )
    note = MODULE.build_forcefield_note(runtime)
    assert "12条现实链" in note
    assert "134个" in note


def test_external_validation_failure_has_priority() -> None:
    runtime = {
        "urethane_external_torsion_validation": {
            "status": "external_fragment_validation_failed"
        },
        "urethane_torsion_candidate_chain_mapping": {
            "status": "twelve_chain_torsion_candidate_mapping_completed_external_validation_pending"
        },
    }
    assert MODULE.decide_forcefield_parameter_status(runtime) == (
        "failed_external_fragment_torsion_validation"
    )
    assert "不得提高" in MODULE.build_forcefield_note(runtime)


def test_external_partial_pass_does_not_release_forcefield() -> None:
    runtime = {
        "urethane_external_torsion_validation": {
            "status": "external_fragment_validation_partial_family_passed_other_family_pending",
            "counts": {"families_evaluated": 1},
        }
    }
    assert MODULE.decide_forcefield_parameter_status(runtime) == (
        "candidate_external_partial_other_family_pending"
    )
    assert "1/2" in MODULE.build_forcefield_note(runtime)
