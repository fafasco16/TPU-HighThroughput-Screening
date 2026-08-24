import json
import math
from pathlib import Path

import pytest

import xTB输出解析 as parser


def _official_json(**changes):
    # 键名逐字对应 xTB v6.7.1 src/main/json.F90。
    value = {
        "total energy": -10.12345678,
        "HOMO-LUMO gap / eV": 3.0,
        "electronic energy": -12.0,
        "dipole / a.u.": [1.0, 2.0, 2.0],
        "partial charges": [-0.2, 0.2],
        "number of molecular orbitals": 4,
        "number of electrons": 4,
        "number of unpaired electrons": 0,
        "orbital energies / eV": [-10.0, -5.0, -2.0, 1.0],
        "fractional occupation": [2.0, 2.0, 0.0, 0.0],
        "program call": "xtb conformer.xyz --sp --gfn 2 --json",
        "method": "GFN2-xTB",
        "xtb version": "6.7.1",
    }
    value.update(changes)
    return value


def _parsed(energy=-10.0, *, status="success", alpha=20.0):
    return {
        "run_status": status,
        "total_energy_hartree": energy,
        "homo_ev": -5.0,
        "lumo_ev": -2.0,
        "homo_lumo_gap_ev": 3.0,
        "dipole_magnitude_debye": 2.0,
        "gfn2_d4_alpha0_au": alpha,
    }


def test_official_json_fields_units_and_frontier_are_parsed():
    result = parser.parse_xtbout_json(
        _official_json(), expected_total_charge=0, expected_atom_count=2
    )
    assert result["total_energy_hartree"] == pytest.approx(-10.12345678)
    assert result["homo_ev"] == -5.0
    assert result["lumo_ev"] == -2.0
    assert result["homo_lumo_gap_ev"] == 3.0
    assert result["dipole_magnitude_debye"] == pytest.approx(3 * parser.AU_DIPOLE_TO_DEBYE)
    assert result["partial_charge_sum_e"] == pytest.approx(0)


def test_json_file_hash_and_d_exponent_support(tmp_path):
    path = tmp_path / "xtbout.json"
    path.write_text(json.dumps(_official_json(**{"total energy": "-1.012345678D+01"})), encoding="utf-8")
    result = parser.parse_xtbout_json(path, expected_total_charge=0)
    assert result["total_energy_hartree"] == pytest.approx(-10.12345678)
    assert result["xtbout_json_sha256"] == parser.sha256(path)


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"method": "GFN1-xTB"}, "method mismatch"),
        ({"xtb version": "6.7.0"}, "version mismatch"),
        ({"total energy": math.nan}, "finite number"),
        ({"dipole / a.u.": [1, 2]}, "expected 3"),
        ({"partial charges": [-0.2, 0.19]}, "charge sum mismatch"),
        ({"partial charges": [0, 0, 0]}, "atom count"),
        ({"HOMO-LUMO gap / eV": 3.01}, "gap mismatch"),
        ({"fractional occupation": [2, 0, 2, 0]}, "ambiguous_frontier_occupancy"),
        ({"fractional occupation": [0.5, 0.1, 0, 0]}, "ambiguous_frontier_occupancy"),
    ],
)
def test_json_scientific_gates_fail_closed(changes, error):
    with pytest.raises(parser.XtbOutputError, match=error):
        parser.parse_xtbout_json(
            _official_json(**changes), expected_total_charge=0, expected_atom_count=2
        )


@pytest.mark.parametrize("content", ["NaN", "[]", "{broken"])
def test_invalid_json_is_rejected(tmp_path, content):
    path = tmp_path / "xtbout.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(parser.XtbOutputError, match="invalid xtbout.json|root must be"):
        parser.parse_xtbout_json(path, expected_total_charge=0)


def test_wbo_parses_one_based_bonds_and_d_exponents():
    bonds = parser.parse_wbo("1 2 1.2345\n2 3 9.0000D-01\n")
    assert bonds[(1, 2)] == pytest.approx(1.2345)
    assert bonds[(2, 3)] == pytest.approx(0.9)


@pytest.mark.parametrize(
    "content,error",
    [
        ("", "no bond"),
        ("1 2", "three fields"),
        ("A 2 1.0", "atom index"),
        ("1 1 1.0", "invalid bond"),
        ("1 2 -0.1", "invalid bond"),
        ("1 2 nan", "finite number"),
        ("1 2 1.0\n2 1 1.1", "duplicate bond"),
    ],
)
def test_wbo_malformed_records_fail_closed(content, error):
    with pytest.raises(parser.XtbOutputError, match=error):
        parser.parse_wbo(content)


def test_official_molecular_and_atomic_alpha_table_is_parsed():
    stdout = """
     #   Z          covCN         q      C6AA      α(0)
     1   6 C        3.000    -0.100    20.000     8.250
     2   8 O        1.000    -0.300    12.000     5.750

 Mol. C6AA /au·bohr⁶  :        100.000000
 Mol. C8AA /au·bohr⁸  :       1000.000000
 Mol. α(0) /au        :         14.000000
"""
    result = parser.parse_polarizability_stdout(stdout)
    assert result["gfn2_d4_alpha0_au"] == 14.0
    assert result["gfn2_d4_atomic_alpha0_au"] == [8.25, 5.75]
    assert "Mol. α(0) /au" in result["polarizability_source_line"]
    assert len(result["stdout_sha256"]) == 64


def test_alpha_requires_explicit_unique_label_and_unit():
    for stdout, error in (
        ("polarizability 14.0", "missing_polarizability_output"),
        ("Mol. α(0) /au : 14\nMol. α(0) /au : 15", "ambiguous"),
        ("Mol. α(0) /au : -1", "non-negative"),
    ):
        with pytest.raises(parser.XtbOutputError, match=error):
            parser.parse_polarizability_stdout(stdout)


def _write_run(directory: Path, *, with_alpha=True):
    directory.mkdir()
    (directory / ".xtbok").write_text("", encoding="utf-8")
    (directory / "xtbout.json").write_text(json.dumps(_official_json()), encoding="utf-8")
    (directory / "wbo").write_text("1 2 1.1\n", encoding="utf-8")
    stdout = "Mol. α(0) /au : 14.0\n" if with_alpha else "normal termination\n"
    (directory / "xtb.out").write_text(stdout, encoding="utf-8")


def test_directory_gate_and_optional_alpha_degradation(tmp_path):
    complete = tmp_path / "complete"
    partial = tmp_path / "partial"
    _write_run(complete)
    _write_run(partial, with_alpha=False)
    assert parser.parse_conformer_directory(complete, expected_total_charge=0)["run_status"] == "success"
    degraded = parser.parse_conformer_directory(partial, expected_total_charge=0)
    assert degraded["run_status"] == "partial_property"
    assert degraded["gfn2_d4_alpha0_au"] is None
    assert degraded["warning_codes"] == ["missing_polarizability_output"]


def test_directory_requires_success_and_scc_convergence(tmp_path):
    missing_marker = tmp_path / "missing"
    _write_run(missing_marker)
    (missing_marker / ".xtbok").unlink()
    with pytest.raises(parser.XtbOutputError, match=".xtbok"):
        parser.parse_conformer_directory(missing_marker, expected_total_charge=0)
    not_converged = tmp_path / "not-converged"
    _write_run(not_converged)
    (not_converged / ".sccnotconverged").write_text("", encoding="utf-8")
    with pytest.raises(parser.XtbOutputError, match="did not converge"):
        parser.parse_conformer_directory(not_converged, expected_total_charge=0)


def test_proxy_weights_and_scalar_statistics_are_closed():
    unit = 1 / parser.HARTREE_TO_KCAL_MOL
    relative, weights = parser.electronic_energy_proxy_weights([0, unit])
    assert relative == pytest.approx([0, 1])
    assert math.fsum(weights) == pytest.approx(1, abs=1e-15)
    summary = parser.weighted_scalar_summary([1, 3], weights)
    assert summary["min"] == 1
    assert summary["max"] == 3
    assert 1 < summary["weighted_mean"] < 3
    assert summary["weighted_sd"] > 0


def test_complete_component_publishes_weights_and_statistics_order_invariant():
    unit = 1 / parser.HARTREE_TO_KCAL_MOL
    rows = [_parsed(0, alpha=10), _parsed(unit, alpha=30)]
    first = parser.aggregate_component_ensemble(rows, expected_conformer_count=2)
    second = parser.aggregate_component_ensemble(reversed(rows), expected_conformer_count=2)
    assert first["ensemble_status"] == "complete"
    assert first["boltzmann_weight_sum"] == pytest.approx(1, abs=1e-15)
    assert first["energy_span_kcal_mol"] == pytest.approx(1)
    assert first["effective_conformer_count"] > 1
    assert first["gfn2_d4_alpha0_au_weighted_mean"] == pytest.approx(
        second["gfn2_d4_alpha0_au_weighted_mean"]
    )
    assert first["gfn2_d4_alpha0_au_min"] == 10
    assert first["gfn2_d4_alpha0_au_max"] == 30


def test_any_failed_or_missing_conformer_blocks_all_ensemble_weights():
    rows = [_parsed(-10), {"run_status": "failed", "failure_reason": "SCC"}]
    result = parser.aggregate_component_ensemble(rows, expected_conformer_count=2)
    assert result["ensemble_status"] == "incomplete"
    assert result["conformer_count_success"] == 1
    assert result["boltzmann_weight_sum"] is None
    assert result["homo_ev_weighted_mean"] is None
    assert all(row["boltzmann_proxy_weight_298K"] is None for row in result["conformers"])
    missing = parser.aggregate_component_ensemble([_parsed(-10)], expected_conformer_count=2)
    assert missing["ensemble_status"] == "incomplete"


def test_partial_alpha_keeps_core_weights_but_never_imputes_alpha():
    rows = [_parsed(-10, alpha=10), _parsed(-9.999, status="partial_property", alpha=None)]
    result = parser.aggregate_component_ensemble(rows, expected_conformer_count=2)
    assert result["ensemble_status"] == "partial_property"
    assert result["boltzmann_weight_sum"] == pytest.approx(1)
    assert result["homo_ev_weighted_mean"] == pytest.approx(-5)
    assert result["gfn2_d4_alpha0_au_weighted_mean"] is None


def test_large_generator_consumes_scalar_records_without_coordinates():
    # 聚合器仅保留每构件的标量结果，不读取/复制 3 万构象的 XYZ 坐标全集。
    records = (_parsed(-100 + index * 1e-7) for index in range(3000))
    result = parser.aggregate_component_ensemble(records, expected_conformer_count=3000)
    assert result["conformer_count_success"] == 3000
    assert result["ensemble_status"] == "complete"


def test_weight_validation_rejects_invalid_inputs():
    for energies, temperature in (([], 298.15), ([0], 0), ([math.inf], 298.15)):
        with pytest.raises(parser.XtbOutputError):
            parser.electronic_energy_proxy_weights(energies, temperature)
    for values, weights in (([], []), ([1], [0.5]), ([1, 2], [1])):
        with pytest.raises(parser.XtbOutputError):
            parser.weighted_scalar_summary(values, weights)
