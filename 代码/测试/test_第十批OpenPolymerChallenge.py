from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十批计算_OpenPolymerChallenge"
)
SCRIPT = ROOT / "代码" / "审计" / "第十批OpenPolymerChallenge.py"


def _load_auditor():
    spec = importlib.util.spec_from_file_location("open_polymer_challenge_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_frozen_download_license_and_member_hashes() -> None:
    module = _load_auditor()
    files = module.verify_frozen_files(DATA)
    provenance = module.verify_provenance(DATA)

    assert len(files) == 8
    assert all(row["integrity"] == "pass" for row in files)
    assert provenance["dataset_id"] == 8_954_694
    assert provenance["dataset_version"] == 1
    assert provenance["license"] == "MIT"
    assert provenance["license_evidence"] == {
        "path": "Kaggle官方API元数据.json",
        "json_field": "licenseName",
        "license_text_file_in_archive": False,
    }
    assert provenance["archive"]["bytes"] == 66_197
    assert provenance["archive"]["sha256"] == (
        "0f048769eb8330ab4ae36784a3f1329e4677768805f9f4531b58d1fa6ec95336"
    )
    assert {
        (row["member"], row["bytes"], row["sha256"])
        for row in provenance["archive"]["members"]
    } == {
        (
            "public.csv",
            19_513,
            "0b5b59f9464fb30da82252cc9e4f56552ff641284c627f11aa93fa4d7031c514",
        ),
        (
            "private.csv",
            208_864,
            "55cac160c1c139240968a96ba070b583e93902dd1901be2e54d722c4d590a952",
        ),
    }


def test_real_md_labels_are_separated_from_missing_cells() -> None:
    summary = _load_auditor().audit_dataset(DATA)

    assert summary["row_count"] == 3_502
    assert summary["unique_raw_smiles_count"] == 3_502
    assert summary["unique_canonical_smiles_count"] == 3_502
    assert summary["rdkit_valid_smiles_count"] == 3_502
    assert summary["rdkit_invalid_row_ids"] == []
    assert summary["rows_with_any_observed_md_label"] == 1_649
    assert summary["rows_with_all_labels_missing"] == 1_853
    assert summary["observed_md_label_cell_count"] == 4_524
    assert summary["missing_not_a_label_cell_count"] == 12_986

    expected = {
        "Tg": (261, 3_241, "degC", "md_group_tg_ffv"),
        "FFV": (223, 3_279, "dimensionless", "md_group_tg_ffv"),
        "Tc": (1_404, 2_098, "W/(m*K)", "md_group_tc_density_rg"),
        "Density": (1_526, 1_976, "g/cm^3", "md_group_tc_density_rg"),
        "Rg": (1_110, 2_392, "angstrom", "md_group_tc_density_rg"),
    }
    for property_name, (observed, missing, unit, group) in expected.items():
        record = summary["properties"][property_name]
        assert record["target_origin"] == "molecular_dynamics"
        assert record["observed_md_label_count"] == observed
        assert record["missing_not_a_label_count"] == missing
        assert record["unit"] == unit
        assert record["simulation_group"] == group

    assert summary["label_state_rule"] == {
        "non_missing_numeric_cell": "observed_md_label",
        "empty_csv_cell": "missing_not_a_label",
    }
    assert summary["training_split_materialized"] is False
    assert summary["training_weight_materialized"] is False
    assert summary["training_weight"] == ""


def test_public_private_source_groups_and_label_counts_are_frozen() -> None:
    summary = _load_auditor().audit_dataset(DATA)

    public = summary["source_groups"]["public"]
    private = summary["source_groups"]["private"]
    assert public == {
        "row_count": 295,
        "unique_raw_smiles_count": 295,
        "unique_canonical_smiles_count": 295,
        "rows_with_any_observed_md_label": 295,
        "rows_with_all_labels_missing": 0,
        "observed_md_label_cell_count": 718,
        "missing_label_cell_count": 757,
        "carbamate_structure_count": 18,
        "property_observed_counts": {
            "Tg": 95,
            "FFV": 86,
            "Tc": 239,
            "Density": 244,
            "Rg": 54,
        },
    }
    assert private == {
        "row_count": 3_207,
        "unique_raw_smiles_count": 3_207,
        "unique_canonical_smiles_count": 3_207,
        "rows_with_any_observed_md_label": 1_354,
        "rows_with_all_labels_missing": 1_853,
        "observed_md_label_cell_count": 3_806,
        "missing_label_cell_count": 12_229,
        "carbamate_structure_count": 193,
        "property_observed_counts": {
            "Tg": 166,
            "FFV": 137,
            "Tc": 1_165,
            "Density": 1_282,
            "Rg": 1_056,
        },
    }
    source_pool = summary["candidate_structure_source_pool"]
    assert source_pool["row_level_mapping_in_csv"] is False
    assert len(source_pool["paper_reported_sources"]) == 3


def test_carbamate_smarts_coverage_is_frozen() -> None:
    carbamate = _load_auditor().audit_dataset(DATA)["carbamate_audit"]

    assert carbamate["smarts"] == "[NX3][CX3](=[OX1])[OX2]"
    assert carbamate["structure_count"] == 211
    assert carbamate["rows_with_any_observed_md_label"] == 131
    assert carbamate["rows_with_all_labels_missing"] == 80
    assert carbamate["property_observed_counts"] == {
        "Tg": 21,
        "FFV": 20,
        "Tc": 117,
        "Density": 125,
        "Rg": 106,
    }
    assert carbamate["source_group_structure_counts"] == {
        "private": 193,
        "public": 18,
    }
    assert carbamate["source_group_property_observed_counts"] == {
        "private": {"Tg": 13, "FFV": 12, "Tc": 104, "Density": 111, "Rg": 101},
        "public": {"Tg": 8, "FFV": 8, "Tc": 13, "Density": 14, "Rg": 5},
    }


def test_public_private_duplicate_leakage_groups_are_zero_but_train_is_unchecked() -> None:
    duplicate = _load_auditor().audit_dataset(DATA)["duplicate_leakage_audit"]

    assert duplicate["scope"] == "public_vs_private_within_this_release"
    assert duplicate["exact_raw_smiles_duplicate_group_count"] == 0
    assert duplicate["canonical_smiles_duplicate_group_count"] == 0
    assert duplicate["cross_source_exact_duplicate_group_count"] == 0
    assert duplicate["cross_source_canonical_duplicate_group_count"] == 0
    assert duplicate["exact_groups"] == []
    assert duplicate["canonical_groups"] == []
    assert duplicate["training_set_overlap_audited"] is False


def test_auditor_is_read_only_for_data_directory() -> None:
    module = _load_auditor()
    before = {
        path.relative_to(DATA).as_posix(): (path.stat().st_size, module._sha256(path))
        for path in DATA.rglob("*")
        if path.is_file()
    }

    first = module.audit_dataset(DATA)
    second = module.audit_dataset(DATA)

    after = {
        path.relative_to(DATA).as_posix(): (path.stat().st_size, module._sha256(path))
        for path in DATA.rglob("*")
        if path.is_file()
    }
    assert first == second
    assert before == after


def test_materialized_metadata_and_frozen_summary_match_live_audit() -> None:
    module = _load_auditor()
    live = module.audit_dataset(DATA)
    metadata = json.loads(
        (DATA / "下载许可与哈希元数据.json").read_text(encoding="utf-8")
    )
    frozen = json.loads((DATA / "冻结审计结果.json").read_text(encoding="utf-8"))

    assert metadata["dataset_id"] == live["provenance"]["dataset_id"]
    assert metadata["dataset_ref"] == live["dataset_ref"]
    assert metadata["dataset_version"] == live["dataset_version"]
    assert metadata["license"] == live["provenance"]["license"]
    assert metadata["archive"] == live["provenance"]["archive"]
    assert {
        row["path"]: (row["bytes"], row["sha256"])
        for row in metadata["frozen_file_inventory"]
    } == {
        row["path"]: (row["bytes"], row["sha256"])
        for row in live["frozen_files"]
    }

    for key in (
        "row_count",
        "unique_raw_smiles_count",
        "rdkit_valid_smiles_count",
        "unique_canonical_smiles_count",
        "rows_with_any_observed_md_label",
        "rows_with_all_labels_missing",
        "observed_md_label_cell_count",
        "missing_not_a_label_cell_count",
        "candidate_structure_source_pool",
        "label_state_rule",
        "training_split_materialized",
        "training_weight_materialized",
        "training_weight",
    ):
        assert frozen[key] == live[key]
    assert frozen["rdkit_invalid_smiles_count"] == len(live["rdkit_invalid_row_ids"])
    assert frozen["duplicate_leakage_audit"] == {
        key: value
        for key, value in live["duplicate_leakage_audit"].items()
        if key not in {"exact_groups", "canonical_groups"}
    }
    for property_name, record in frozen["properties"].items():
        assert record == {
            key: value
            for key, value in live["properties"][property_name].items()
            if key != "source_group_observed_counts"
        }
