from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from rdkit import Chem


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第九批PolyUniverse异氰酸酯单体.py"
RAW_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第七批虚拟_PolyUniverse百万PU"
)


def _load_module():
    code_root = str(ROOT / "代码")
    if code_root not in sys.path:
        sys.path.insert(0, code_root)
    return importlib.import_module("审计.第九批PolyUniverse异氰酸酯单体")


@pytest.fixture(scope="module")
def audited():
    if not all(
        (RAW_DIR / name).is_file()
        for name in ("PubChem_diNCO.csv", "GDB-17_diNCO.csv")
    ):
        pytest.skip("PolyUniverse diNCO 官方原件未在当前检出中分发")
    module = _load_module()
    return module, module.audit_source()


def test_official_files_and_strict_functional_group_counts_are_frozen(audited):
    _, bundle = audited
    summary = bundle.summary

    assert summary["source"]["doi"] == "10.5281/zenodo.12585902"
    assert summary["source"]["license_spdx"] == "CC-BY-4.0"
    assert {
        row["name"]: (row["bytes"], row["md5"], row["verified"])
        for row in summary["source"]["files"]
    } == {
        "PubChem_diNCO.csv": (
            1_182_762,
            "ad388d4d0628156d337f035cb06861a0",
            True,
        ),
        "GDB-17_diNCO.csv": (
            157_268,
            "43bd1d039f1df7c3e13df89284fb249e",
            True,
        ),
    }
    pubchem = summary["files"]["PubChem_diNCO.csv"]
    gdb = summary["files"]["GDB-17_diNCO.csv"]
    assert pubchem["raw_rows"] == pubchem["valid"] == 17_634
    assert pubchem["class_diNCO_exact"] == 12_021
    assert pubchem["class_diNCS_exact"] == 1_682
    assert pubchem["class_mixed_NCO_NCS"] == 204
    assert pubchem["strict_candidate_rows"] == 9_288
    assert gdb["raw_rows"] == 4_083
    assert gdb["valid"] == 4_076 and gdb["invalid"] == 7
    assert gdb["class_diNCO_exact"] == 148
    assert gdb["class_diNCS_exact"] == 1
    assert gdb["class_mixed_NCO_NCS"] == 3
    assert gdb["strict_candidate_rows"] == 101


def test_merged_candidates_are_unique_tiered_gold_v_and_zero_supervision(audited):
    module, bundle = audited
    merged = bundle.summary["merged"]
    rows = list(bundle.candidate_rows)

    assert merged == {
        "raw_row_count": 21_717,
        "valid_row_count": 21_710,
        "invalid_row_count": 7,
        "exact_diNCO_unique_count": 12_072,
        "strict_candidate_unique_count": 9_332,
        "conditional_diNCO_unique_count": 2_740,
        "strict_cross_file_overlap_count": 53,
        "gold_v_reference_count": 12_072,
        "gold_v_candidate_count": 12_072,
        "single_component_synthesis_primary_count": 9_332,
        "mixture_or_salt_reference_count": 2_579,
        "not_synthesis_candidate_count": 161,
        "standard_inchikey_unique_count": 12_069,
        "standard_inchikey_overlap_group_count": 3,
        "standard_inchikey_overlap_record_count": 6,
        "tautomer_representation_overlap_group_count": 2,
        "tautomer_family_unique_count": 11_649,
        "tautomer_family_overlap_group_count": 330,
        "tautomer_family_overlap_record_count": 753,
        "direct_property_supervision_weight_ceiling": 0.0,
    }
    assert len(rows) == len({row["candidate_id"] for row in rows}) == 12_072
    assert len({row["canonical_smiles"] for row in rows}) == 12_072
    assert sum(row["gold_admission_status"] == "admitted_reference" for row in rows) == 9_332
    assert sum(row["gold_admission_status"] == "conditional_reference" for row in rows) == 2_740
    assert {row["gold_layer"] for row in rows} == {"Gold-V"}
    assert {row["direct_property_supervision_weight_ceiling"] for row in rows} == {0.0}
    assert all(set(row) == set(module.CANDIDATE_COLUMNS) for row in rows)


def test_reference_universe_is_separated_from_synthesis_primary_candidates(audited):
    module, bundle = audited
    rows = list(bundle.candidate_rows)
    scopes = {
        scope: sum(row["screening_scope"] == scope for row in rows)
        for scope in {
            "direct_tpu_building_block",
            "mixture_or_salt_reference",
            "not_synthesis_candidate",
        }
    }

    assert scopes == {
        "direct_tpu_building_block": 9_332,
        "mixture_or_salt_reference": 2_579,
        "not_synthesis_candidate": 161,
    }
    assert len(module.build_synthesis_candidate_rows()) == 9_332
    assert all(
        row["screening_scope"] == "direct_tpu_building_block"
        for row in module.build_synthesis_candidate_rows()
    )
    assert all(
        row["direct_property_supervision_weight_ceiling"] == 0.0 for row in rows
    )


def test_standard_inchikey_and_family_keys_are_split_safe(audited):
    _, bundle = audited
    mapping = list(bundle.mapping_rows)

    assert len({row["standard_inchikey"] for row in mapping}) == 12_069
    assert len({row["tautomer_family_key"] for row in mapping}) == 11_649
    assert {
        row["tautomer_family_key"] for row in mapping
    } == {row["split_family_key"] for row in mapping}
    assert all(
        row["standard_inchikey"] == row["inchikey"] for row in mapping
    )
    assert all(
        row["tautomer_family_key"]
        == row["standard_inchikey"].split("-", 1)[0]
        for row in mapping
    )


def test_candidate_structures_have_exact_two_nco_and_no_ncs(audited):
    module, bundle = audited
    nco = Chem.MolFromSmarts(module.NCO_SMARTS)
    ncs = Chem.MolFromSmarts(module.NCS_SMARTS)
    assert nco is not None and ncs is not None

    for row in bundle.candidate_rows:
        mol = Chem.MolFromSmiles(row["canonical_smiles"])
        assert mol is not None
        assert len(mol.GetSubstructMatches(nco, uniquify=True)) == 2
        assert len(mol.GetSubstructMatches(ncs, uniquify=True)) == 0


def test_existing_gold_v_overlap_is_filtered_without_changing_raw_audit(audited):
    module, bundle = audited
    first = bundle.candidate_rows[0]
    filtered = module.build_candidate_rows({first["canonical_smiles"]})
    assert len(filtered) == 12_071
    assert first["canonical_smiles"] not in {
        row["canonical_smiles"] for row in filtered
    }
    assert bundle.summary["merged"]["gold_v_candidate_count"] == 12_072
