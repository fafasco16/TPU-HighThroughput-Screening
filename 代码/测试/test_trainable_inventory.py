import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = ROOT / "结果" / "数据总账.json"
LEDGER_PATH = ROOT / "结果" / "数据规模总账.csv"
MANIFEST_PATH = ROOT / "结果" / "样本清单.csv.gz"
REPORT_PATH = ROOT / "结果" / "数据总账说明.md"
CANDIDATE_PATH = ROOT / "结果" / "Gold_V_候选.csv.gz"
GOLD_C_VALUE_PATH = ROOT / "结果" / "Gold_C_计算性能.csv.gz"
GOLD_E_TABLE_PATH = ROOT / "结果" / "Gold_E_实验表格.csv.gz"
PROFILE_PATH = ROOT / "配置" / "v0.2可训练样本总账来源画像.yaml"
OUTPUT_PATHS = (
    CANDIDATE_PATH,
    GOLD_E_TABLE_PATH,
    GOLD_C_VALUE_PATH,
    LEDGER_PATH,
    MANIFEST_PATH,
    JSON_PATH,
    REPORT_PATH,
)


@pytest.fixture(scope="module")
def inventory(generated_inventory_outputs: dict[str, str]) -> dict:
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert "record_manifest" not in payload
    payload["record_manifest"] = _typed_manifest_rows(MANIFEST_PATH)
    return payload


def _csv_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _typed_manifest_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [dict(row) for row in _csv_rows(path)]
    integer_columns = (
        "specimen_count",
        "run_count",
        "curve_count",
        "scalar_count",
        "point_count",
        "numeric_value_count",
    )
    for row in rows:
        row["weight_ceiling"] = float(str(row["weight_ceiling"]))
        for column in integer_columns:
            row[column] = int(str(row[column]))
        for column in ("current_weight_materialized", "model_ready"):
            row[column] = str(row[column]).casefold() == "true"
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_and_scientific_denominator_totals_are_frozen(inventory: dict):
    summary = inventory["summary"]

    assert summary["ledger_source_scope_count"] == 77
    assert summary["v0_2_source_directory_count"] == 69
    assert summary["v0_2_independent_source_identity_count"] == 68
    assert summary["local_backlog_source_directory_count"] == 4
    assert summary["local_backlog_independent_source_identity_count"] == 4
    assert summary["v0_1_frozen_baseline_source_count"] == 4
    assert summary["total_independent_source_contribution_count"] == 76
    assert summary["strict_core_calibration_curve_count"] == 233
    assert summary["strict_core_calibration_curve_point_row_count"] == 935_097
    assert summary["strict_core_calibration_complete_point_pair_upper_bound"] == 935_095
    assert summary["strict_core_keyed_specimen_count"] == 217
    assert summary["strict_core_keyed_curve_count"] == 217
    assert summary["strict_core_keyed_curve_point_row_count"] == 913_608
    assert summary["strict_core_keyed_complete_point_pair_upper_bound"] == 913_606
    assert summary["core_source_directory_keyed_specimen_count"] == 227
    assert summary["strict_core_formulation_count"] == 30
    assert summary["core_source_directory_formulation_count"] == 34
    assert summary["strict_core_batch_count"] == 27
    assert summary["core_source_directory_batch_count"] == 31
    assert summary["conservative_tpu_tpuu_specimen_or_direct_run_lower_bound"] == 1088
    assert summary["selected_source_heterogeneous_specimen_or_run_arithmetic_pool"] == 1119
    assert summary["major_experimental_curve_history_lower_bound"] == 1112
    assert summary["major_experimental_curve_point_lower_bound"] == 12_258_315

    experimental = summary["known_origin_totals"]["experimental_only"]
    assert experimental["specimen_count"] == {
        "value": 1465,
        "known_source_scope_count": 23,
    }
    assert experimental["curve_count_observed"] == {
        "value": 2665,
        "known_source_scope_count": 41,
    }
    assert experimental["curve_count_candidate"] == {
        "value": 2485,
        "known_source_scope_count": 41,
    }
    assert experimental["point_count_observed"] == {
        "value": 9_545_156,
        "known_source_scope_count": 41,
    }

    mixed = summary["known_origin_totals"]["mixed_experiment_and_simulation"]
    assert mixed["curve_count_observed"] == {
        "value": 701,
        "known_source_scope_count": 9,
    }
    assert mixed["point_count_observed"] == {
        "value": 7_862_426,
        "known_source_scope_count": 8,
    }


def test_inventory_remains_audit_only_without_materialized_training(inventory: dict):
    summary = inventory["summary"]
    assert summary["training_enabled"] is False
    assert summary["training_split_created"] is False
    assert summary["training_weight_materialized"] is False
    assert summary["model_ready_record_count"] == 0

    manifest = inventory["record_manifest"]
    assert manifest
    assert all(row["model_ready"] is False for row in manifest)
    assert all(row["current_weight_materialized"] is False for row in manifest)
    assert all(
        float(row["weight_ceiling"]) == 0.0
        for row in manifest
        if row["gold_admission_status"] in {"blocked", "evidence_only"}
    )


def test_gold_reference_layer_is_machine_queryable_and_independent_of_weight(inventory: dict):
    ledger = inventory["source_ledger"]
    manifest = inventory["record_manifest"]
    expected = {
        "实验": "Gold-E",
        "模拟": "Gold-C",
        "混合": "Gold-E+Gold-C",
        "虚拟候选": "Gold-V",
        "证据": "Not-Gold",
    }
    assert all(row["gold_layer"] == expected[row["origin_kind"]] for row in ledger)
    layer_by_target_origin = {
        "experimental": "Gold-E",
        "computational": "Gold-C",
        "dft": "Gold-C",
        "aimd": "Gold-C",
        "md": "Gold-C",
        "coarse_grained_md": "Gold-C",
        "finite_element": "Gold-C",
        "simulation_input": "Gold-C",
        "virtual": "Gold-V",
        "reaction_rule_generated": "Gold-V",
        "enumeration": "Gold-V",
        "model_generated": "Gold-V",
        "ml_prediction": "Gold-V",
        "mixed": "Gold-E+Gold-C",
        "evidence": "Not-Gold",
    }
    assert all(
        row["gold_layer"]
        == layer_by_target_origin.get(row["target_origin"], expected[row["origin_kind"]])
        for row in manifest
    )
    assert all(row["target_origin"] for row in manifest)

    virtual = {row["source_directory"]: row for row in ledger if row["origin_kind"] == "虚拟候选"}
    assert set(virtual) == {
        "基础数据/smipoly_monomers.csv",
        "第七批虚拟_PUR-GEN片段库",
        "第七批虚拟_PolyUniverse百万PU",
    }
    assert all(row["gold_layer"] == "Gold-V" for row in virtual.values())
    assert all(row["gold_admission_status"] == "admitted_reference" for row in virtual.values())
    assert all(row["weight_ceiling"] == 0.0 for row in virtual.values())
    assert all(row["model_ready_record_count"] == 0 for row in virtual.values())
    assert virtual["基础数据/smipoly_monomers.csv"]["license_status"] == "allow_with_attribution"
    assert virtual["第七批虚拟_PUR-GEN片段库"]["license_status"] == "manual_review"
    assert virtual["第七批虚拟_PolyUniverse百万PU"]["license_status"] == "allow_with_attribution"

    assert inventory["summary"]["source_gold_layer_counts"]["Gold-C"] > 0
    assert inventory["summary"]["source_gold_admission_status_counts"][
        "admitted_reference"
    ] > 0


def test_reliable_computational_reference_is_not_blocked_by_training_readiness(
    inventory: dict,
):
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    for source in (
        "PCL_GitLFS轨迹补采",
        "Zenodo_反应型粗粒化聚脲固化",
        "Zenodo_PTMO_MDI_BDO聚氨酯冲击MD",
    ):
        row = ledger[source]
        assert row["gold_layer"] == "Gold-C"
        assert row["gold_admission_status"] == "conditional_reference"
        assert row["weight_ceiling"] == 0.0

    dft_runs = [
        row
        for row in inventory["record_manifest"]
        if row["source_directory"] == "Zenodo_TPU回收封端剂DFT与机器学习"
        and row["record_granularity"] == "run"
    ]
    assert len(dft_runs) == 158
    assert {row["gold_layer"] for row in dft_runs} == {"Gold-C"}
    assert {row["target_origin"] for row in dft_runs} == {"computational"}
    assert sum(
        row["gold_admission_status"] == "admitted_reference" for row in dft_runs
    ) == 107
    assert sum(
        row["gold_admission_status"] == "conditional_reference"
        for row in dft_runs
    ) == 51
    assert all(row["gold_admission_status"] != "blocked" for row in dft_runs)
    assert max(row["weight_ceiling"] for row in dft_runs) == 0.50
    assert min(
        row["weight_ceiling"]
        for row in dft_runs
        if row["gold_admission_status"] == "conditional_reference"
    ) == 0.10
    assert all(row["model_ready"] is False for row in dft_runs)


def test_sixth_batch_computational_sources_keep_fidelity_and_inputs_separate(inventory: dict):
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    expected = {
        "MDPI_HDI_PEG双力场TPU": (5, 5, 100, 26, 26, 52, 5),
        "MDPI_MDI聚醚双组分PU分子动力学": (4, 3, 16, 120, 79, 136, 4),
        "Frontiers_PU_ReaxFF热解": (1, None, 6, 2, 1, 2, 1),
        "Figshare_商用PUR形状记忆本构FEA": (4, None, 0, 0, 0, 112, 4),
    }
    for source, counts in expected.items():
        row = ledger[source]
        assert (
            row["material_count"],
            row["formulation_count"],
            row["run_count"],
            row["scalar_count_observed"],
            row["scalar_count_candidate"],
            row["numeric_value_count"],
            row["computational_system_count"],
        ) == counts
        assert row["license_status"] == "allow_with_attribution"
        assert row["citation_keys"]

    rows = inventory["record_manifest"]
    hdi = [row for row in rows if row["source_directory"] == "MDPI_HDI_PEG双力场TPU"]
    assert sum(row["target_origin"] == "experimental" and row["gold_layer"] == "Gold-E" for row in hdi) == 6
    assert sum(row["target_origin"] == "md" and row["gold_layer"] == "Gold-C" for row in hdi) == 20
    assert max(row["weight_ceiling"] for row in hdi if row["target_origin"] == "md") == 0.40
    assert max(
        row["weight_ceiling"]
        for row in hdi
        if row["target_origin"] == "md"
        and row["candidate_id"] in {"PEG-H800", "PEG-H2000"}
    ) <= 0.20

    mdi = [row for row in rows if row["source_directory"] == "MDPI_MDI聚醚双组分PU分子动力学"]
    assert len([row for row in mdi if row["record_granularity"] == "scalar"]) == 120
    assert sum(row["weight_ceiling"] > 0 for row in mdi) == 80  # 79候选 + 来源聚合行
    assert max(row["weight_ceiling"] for row in mdi) <= 0.20

    fea = ledger["Figshare_商用PUR形状记忆本构FEA"]
    assert fea["gold_layer"] == "Gold-C"
    assert fea["gold_admission_status"] == "conditional_reference"
    assert fea["scalar_count_candidate"] == 0
    assert fea["weight_ceiling"] == 0.0


def test_batch8_tpu_literature_aggregates_enter_conditional_gold_reference(
    inventory: dict,
):
    source = "第八批实验_TPU文献力学汇总"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"] == "doi:10.17632/ftntxg4zdz.1"
    assert row["gold_layer"] == "Gold-E"
    assert row["gold_admission_status"] == "conditional_reference"
    assert row["license_status"] == "allow_with_attribution"
    assert row["source_record_count"] == 62
    assert row["specimen_count"] == 0
    assert row["formulation_count"] == 0
    assert row["scalar_count_observed"] == 186
    assert row["scalar_count_candidate"] == 186
    assert row["evidence_group_count"] == 45

    records = [
        item for item in inventory["record_manifest"] if item["source_directory"] == source
    ]
    scalar_records = [item for item in records if item["record_granularity"] == "scalar"]
    assert len(scalar_records) == 186
    assert len({item["leakage_group_key"] for item in scalar_records}) == 45
    assert all(item["target_origin"] == "experimental" for item in scalar_records)
    assert all(item["gold_layer"] == "Gold-E" for item in scalar_records)
    assert all(item["gold_admission_status"] == "conditional_reference" for item in scalar_records)
    assert all(item["weight_ceiling"] == 0.0 for item in scalar_records)


def test_batch8_copper_pu_pyrolysis_keeps_experiment_and_dft_as_multifidelity_reference(
    inventory: dict,
):
    source = "第八批混合_PU铜调控热解多尺度"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"] == "10.5281/zenodo.18414263"
    assert row["gold_layer"] == "Gold-E+Gold-C"
    assert row["gold_admission_status"] == "admitted_reference"
    assert row["license_status"] == "allow_with_attribution"
    assert row["material_count"] == 1
    assert row["formulation_count"] == 0
    assert row["specimen_count"] == 0
    assert row["curve_count_observed"] == 26
    assert row["scalar_count_observed"] == 411
    assert row["scalar_count_candidate"] == 319
    assert row["computational_system_count"] == 6
    assert row["weight_ceiling"] == 0.25
    assert set(row["citation_keys"].split(";")) == {
        "ledger-138-zhang-2026-copper-pu-pyrolysis-data",
        "ledger-139-zhang-2026-copper-pu-pyrolysis",
    }

    records = [
        item for item in inventory["record_manifest"] if item["source_directory"] == source
    ]
    experimental = [item for item in records if item["target_origin"] == "experimental"]
    computational = [item for item in records if item["target_origin"] == "computational"]
    assert len(experimental) == 14
    assert len(computational) == 14
    assert sum(item["point_count"] for item in experimental) == 140_680
    assert all(item["gold_layer"] == "Gold-E" for item in experimental)
    assert all(item["gold_layer"] == "Gold-C" for item in computational)
    assert all(
        item["gold_admission_status"] == "conditional_reference"
        for item in computational
    )
    assert all(item["specimen_count"] == 0 for item in records)
    assert max(item["weight_ceiling"] for item in computational) == 0.15
    assert any(
        item["gold_admission_status"] == "conditional_reference"
        for item in experimental
    )


def test_batch8_sls_tpu_lattice_keeps_real_replicates_without_inventing_formulations(
    inventory: dict,
):
    source = "第八批实验_SLS_TPU晶格工艺"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"] == "10.6084/m9.figshare.31550614.v1"
    assert row["gold_layer"] == "Gold-E"
    assert row["gold_admission_status"] == "admitted_reference"
    assert row["material_count"] == 1
    assert row["formulation_count"] == 0
    assert row["specimen_count"] == 75
    assert row["scalar_count_observed"] == 375
    assert row["evidence_group_count"] == 25
    assert row["weight_ceiling"] == 0.35
    assert set(row["citation_keys"].split(";")) == {
        "ledger-140-seong-2026-sls-tpu-lattice-data",
        "ledger-141-seong-2026-sls-tpu-lattice",
    }

    records = [
        item for item in inventory["record_manifest"] if item["source_directory"] == source
    ]
    scalars = [item for item in records if item["record_granularity"] == "scalar"]
    assert len(scalars) == 375
    assert len({item["specimen_key"] for item in scalars}) == 75
    assert len({item["leakage_group_key"] for item in scalars}) == 25
    assert sum(item["gold_admission_status"] == "admitted_reference" for item in scalars) == 300
    assert sum(item["gold_admission_status"] == "conditional_reference" for item in scalars) == 75
    assert sum(item["weight_ceiling"] == 0.15 for item in scalars) == 75
    assert sum(item["weight_ceiling"] == 0.10 for item in scalars) == 75
    assert max(item["weight_ceiling"] for item in scalars) == 0.35


def test_batch8_nonisocyanate_phcu_keeps_six_formulations_and_multimodal_curves(
    inventory: dict,
):
    source = "第八批实验_非异氰酸酯PHCU热塑性聚氨酯"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"] == "10.17632/bvv43yk29c.1"
    assert row["gold_layer"] == "Gold-E"
    assert row["gold_admission_status"] == "admitted_reference"
    assert row["scientific_role"] == "TPU核心"
    assert row["license_status"] == "allow_with_attribution"
    assert row["material_count"] == 6
    assert row["formulation_count"] == 6
    assert row["specimen_count"] == 0
    assert row["curve_count_observed"] == 39
    assert row["curve_count_candidate"] == 30
    assert row["point_count_observed"] == 331_138
    assert row["weight_ceiling"] == 0.60
    assert set(row["citation_keys"].split(";")) == {
        "ledger-142-shen-2018-nonisocyanate-phcu-data",
        "ledger-143-shen-2018-nonisocyanate-phcu",
        "ledger-144-zhang-2021-phcu-composition-semantics",
    }

    records = [
        item for item in inventory["record_manifest"] if item["source_directory"] == source
    ]
    curves = [item for item in records if item["record_granularity"] == "curve"]
    assert len(curves) == 39
    assert sum(item["point_count"] for item in curves) == 331_138
    assert len({item["material_formula_key"] for item in curves if item["material_formula_key"]}) == 6
    assert sum(item["gold_admission_status"] == "admitted_reference" for item in curves) == 23
    assert sum(item["gold_admission_status"] == "conditional_reference" for item in curves) == 7
    assert sum(item["gold_admission_status"] == "evidence_only" for item in curves) == 9
    assert sum(item["weight_ceiling"] == 0.60 for item in curves) == 6
    assert sum(item["weight_ceiling"] == 0.55 for item in curves) == 6
    supervised_curves = [
        item
        for item in curves
        if item["gold_admission_status"]
        in {"admitted_reference", "conditional_reference"}
    ]
    evidence_curves = [
        item for item in curves if item["gold_admission_status"] == "evidence_only"
    ]
    assert all(item["target_origin"] == "experimental" for item in supervised_curves)
    assert all(item["target_origin"] == "evidence" for item in evidence_curves)
    assert all(item["gold_layer"] == "Not-Gold" for item in evidence_curves)
    assert all(item["weight_ceiling"] == 0.0 for item in evidence_curves)


def test_batch9_radonpy_keeps_direct_pu_computation_separate_from_general_transfer(
    inventory: dict,
):
    source = "第九批计算_RadonPy_PI1070"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"].endswith(
        "/RadonPy/tree/840dd4a2b5f261fc9370bb6786eff0b71a463d2f/data"
    )
    assert row["gold_layer"] == "Gold-C"
    assert row["gold_admission_status"] == "admitted_reference"
    assert row["license_status"] == "allow_with_attribution"
    assert row["source_record_count"] == 1_077
    assert row["material_count"] == 1_077
    assert row["computational_system_count"] == 1_077
    assert row["scalar_count_observed"] == 440
    assert row["weight_ceiling"] == 0.20
    assert set(row["citation_keys"].split(";")) == {
        "ledger-145-hayashi-radonpy-pi1070-data",
        "ledger-146-hayashi-2022-radonpy",
    }

    records = [
        item for item in inventory["record_manifest"] if item["source_directory"] == source
    ]
    observations = [item for item in records if item["record_granularity"] == "scalar"]
    assert len(observations) == 440
    assert len({item["candidate_id"] for item in observations}) == 11
    assert len({item["leakage_group_key"] for item in observations}) == 11
    assert all(item["target_origin"] == "computational" for item in observations)
    assert all(item["gold_layer"] == "Gold-C" for item in observations)
    assert all(item["gold_admission_status"] == "admitted_reference" for item in observations)
    assert all(item["current_weight_materialized"] is False for item in observations)


def test_batch9_polyomics_materializes_only_pu_related_computational_references(
    inventory: dict,
):
    source = "第九批计算_PolyOmics"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"] == "10.57967/hf/7475"
    assert row["gold_layer"] == "Gold-C"
    assert row["gold_admission_status"] == "admitted_reference"
    assert row["source_record_count"] == 95_335
    assert row["material_count"] == 6_448
    assert row["run_count"] == 6_588
    assert row["computational_system_count"] == 6_537
    assert row["scalar_count_observed"] == 209_905
    assert row["weight_ceiling"] == 0.20
    assert {"ledger-020-polyomics-2025", "ledger-151-hayashi-2026-polyomics-data"} <= set(
        row["citation_keys"].split(";")
    )

    records = [
        item for item in inventory["record_manifest"] if item["source_directory"] == source
    ]
    runs = [item for item in records if item["record_granularity"] == "run"]
    assert len(runs) == 6_588
    assert sum(item["gold_admission_status"] == "admitted_reference" for item in runs) == 3_384
    assert sum(item["gold_admission_status"] == "conditional_reference" for item in runs) == 3_204
    assert len({item["material_formula_key"] for item in runs}) == 6_448
    assert len({item["run_key"] for item in runs}) == 6_537
    assert sum(item["numeric_value_count"] for item in runs) == 209_905
    assert sum(item["scalar_count"] for item in runs) < 209_905
    assert max(item["weight_ceiling"] for item in runs if item["gold_admission_status"] == "admitted_reference") == 0.20
    assert max(item["weight_ceiling"] for item in runs if item["gold_admission_status"] == "conditional_reference") == 0.10
    assert all(item["target_origin"] == "computational" for item in runs)
    assert all(item["current_weight_materialized"] is False for item in runs)


def test_batch9_sheffield_keeps_pu_experiments_and_external_controls_separate(
    inventory: dict,
):
    source = "第九批实验_Sheffield_PU理性设计"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"] == "10.15131/shef.data.21510876.v1"
    assert row["gold_layer"] == "Gold-E"
    assert row["formulation_count"] == 39
    assert row["batch_count"] == 40
    assert row["curve_count_observed"] == 155
    assert row["scalar_count_observed"] == 764
    assert row["point_count_observed"] == 63_132

    records = [
        item for item in inventory["record_manifest"] if item["source_directory"] == source
    ]
    curves = [item for item in records if item["record_granularity"] == "curve"]
    scalars = [item for item in records if item["record_granularity"] == "scalar"]
    assert len(curves) == 155
    assert len(scalars) == 764
    assert sum(item["point_count"] for item in curves) == 63_132
    assert sum(item["gold_admission_status"] == "admitted_reference" for item in curves) == 151
    assert sum(item["gold_admission_status"] == "evidence_only" for item in curves) == 4
    assert sum(item["gold_admission_status"] == "admitted_reference" for item in scalars) == 723
    assert sum(item["gold_admission_status"] == "conditional_reference" for item in scalars) == 32
    assert sum(item["gold_admission_status"] == "evidence_only" for item in scalars) == 9
    evidence = [item for item in [*curves, *scalars] if item["gold_admission_status"] == "evidence_only"]
    assert all(item["gold_layer"] == "Not-Gold" for item in evidence)
    assert all(item["weight_ceiling"] == 0.0 for item in evidence)


def test_batch9_sugar_filled_spu_preserves_experiment_model_fidelity_layers(
    inventory: dict,
):
    source = "第九批实验_糖填充超分子聚氨酯"
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    row = ledger[source]

    assert row["canonical_identifier"] == "10.17632/z4zy523b8c.1"
    assert row["gold_layer"] == "Gold-E+Gold-C"
    assert row["license_status"] == "manual_review"
    assert row["material_count"] == row["formulation_count"] == 9
    assert row["curve_count_observed"] == 331
    assert row["point_count_observed"] == 115_013
    assert row["numeric_value_count"] == 230_026

    curves = [
        item
        for item in inventory["record_manifest"]
        if item["source_directory"] == source
        and item["record_granularity"] == "curve"
    ]
    assert len(curves) == 331
    assert sum(item["point_count"] for item in curves) == 115_013
    assert sum(item["numeric_value_count"] for item in curves) == 230_026
    assert sum(item["gold_admission_status"] == "admitted_reference" for item in curves) == 87
    assert sum(item["gold_admission_status"] == "conditional_reference" for item in curves) == 244
    assert {layer: sum(item["gold_layer"] == layer for item in curves) for layer in {
        "Gold-E", "Gold-C", "Gold-E+Gold-C"
    }} == {"Gold-E": 99, "Gold-C": 155, "Gold-E+Gold-C": 77}
    assert {item["leakage_group_key"] for item in curves} == {
        "spu_sugar_composite_z4zy523b8c_v1"
    }
    assert max(item["weight_ceiling"] for item in curves if item["gold_layer"] == "Gold-C") <= 0.25
    assert all(item["current_weight_materialized"] is False for item in curves)


def test_materialized_gold_v_candidates_are_unique_traceable_and_zero_weight(inventory: dict):
    rows = _csv_rows(CANDIDATE_PATH)
    summary = inventory["summary"]

    assert len(rows) == summary["virtual_candidate_count"] == 117_629
    assert sum(row["source_id"] == "ds_smipoly_monomers" for row in rows) == 1071
    assert sum(row["source_id"] == "source_zenodo_11612378_purgen_fragments" for row in rows) == 414
    assert sum(
        row["source_id"] == "source_zenodo_12585902_polyuniverse_pu"
        for row in rows
    ) == 12_059
    assert sum(row["source_id"] == "source_omg_batch10" for row in rows) == 100_584
    assert sum(
        row["source_id"] == "source_openpolymer_challenge_v1"
        for row in rows
    ) == 3_501
    assert len({row["candidate_id"] for row in rows}) == 117_629
    assert len({row["canonical_smiles"] for row in rows}) == 117_629
    assert all(row["inchikey"] for row in rows)
    assert all(row["gold_layer"] == "Gold-V" for row in rows)
    assert sum(row["gold_admission_status"] == "admitted_reference" for row in rows) == 114_889
    assert sum(row["gold_admission_status"] == "conditional_reference" for row in rows) == 2_740
    assert all(float(row["direct_property_supervision_weight_ceiling"]) == 0.0 for row in rows)
    assert summary["virtual_candidate_priority1_structure_count"] == 9_631
    assert summary["virtual_candidate_direct_building_block_count"] == 9_490
    assert summary["virtual_candidate_reference_count"] == 117_629
    assert summary["virtual_candidate_synthesis_primary_count"] == 9_490
    assert summary["virtual_candidate_mixture_or_salt_reference_count"] == 2_579
    assert summary["virtual_candidate_not_synthesis_candidate_count"] == 161
    assert summary["virtual_candidate_functional_group_matched_count"] == 113_807
    assert summary["virtual_candidate_unclassified_count"] == 531

    candidate_manifest = {
        row["candidate_id"]: row
        for row in inventory["record_manifest"]
        if row["record_granularity"] == "candidate"
    }
    assert len(candidate_manifest) == 117_629
    source_sets_by_family: dict[str, set[str]] = {}
    row_count_by_family: dict[str, int] = {}
    for row in rows:
        connectivity = row["inchikey"].split("-", 1)[0]
        expected_key = f"standard_inchikey_connectivity_family|{connectivity}"
        manifest_row = candidate_manifest[row["candidate_id"]]
        assert manifest_row["leakage_group_key"] == expected_key
        assert manifest_row["leakage_key_status"] == "explicit_candidate_family"
        source_sets_by_family.setdefault(connectivity, set()).add(row["source_id"])
        row_count_by_family[connectivity] = row_count_by_family.get(connectivity, 0) + 1
    cross_source_families = {
        family for family, sources in source_sets_by_family.items() if len(sources) > 1
    }
    assert len(cross_source_families) == 7
    assert sum(row_count_by_family[family] for family in cross_source_families) == 31


def test_manifest_enums_unique_ids_and_leakage_keys_are_valid(inventory: dict):
    manifest = inventory["record_manifest"]
    enums = inventory["enums"]
    row_ids = [row["manifest_row_id"] for row in manifest]

    assert len(manifest) == inventory["summary"]["manifest_row_count"]
    assert len(row_ids) == len(set(row_ids))
    assert all(row["record_granularity"] in enums["record_granularity"] for row in manifest)
    assert all(row["origin_kind"] in enums["origin_kind"] for row in manifest)
    assert all(row["scientific_role"] in enums["scientific_role"] for row in manifest)
    assert all(row["quality_status"] in enums["quality_status"] for row in manifest)
    assert all(str(row["leakage_group_key"]).strip() for row in manifest)
    assert all(str(row["record_role"]).strip() for row in manifest)
    assert all(str(row["audit_basis"]).strip() for row in manifest)


def test_manifest_contains_no_legacy_layout_paths(inventory: dict):
    legacy_prefixes = (
        "01_原始数据/",
        "02_暂存数据/",
        "03_规范数据/",
        "04_派生数据/",
        "05_数据库快照/",
        "06_审核导出/",
    )
    path_fields = ("raw_sample_key", "run_key", "curve_key", "source_locator", "audit_basis")

    for row in inventory["record_manifest"]:
        for field in path_fields:
            value = str(row.get(field, "")).replace("\\", "/")
            assert not any(prefix in value for prefix in legacy_prefixes), (field, value)


def test_fdm_doe_and_pcl_counting_boundaries_are_not_inflated(inventory: dict):
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    fdm = ledger["Mendeley_FDM_TPU晶格与基材力学"]
    assert fdm["specimen_count"] == 76
    assert fdm["curve_count_observed"] == 76
    assert fdm["curve_count_candidate"] == 57
    assert fdm["scalar_count_observed"] == 1206
    assert fdm["scalar_count_candidate"] == 935

    doe = ledger["Mendeley_TPU压缩打印DOE"]
    assert doe["scalar_count_observed"] == 2664
    assert doe["scalar_count_candidate"] == 1356
    assert doe["direct_numeric_total"] == 1500
    assert doe["complete_direct_response_count"] == 1372
    assert doe["valid_derived_scalar_count"] == 1292
    assert doe["invalid_cached_formula_count"] == 4
    assert doe["known_missing_direct_count"] == 4
    assert "valid_derived=1292" in doe["notes"]
    assert "invalid_cached_pseudo_zero=4" in doe["notes"]
    assert "4个实心立方体对照试样产生的16个完整直接响应" in doe["notes"]
    assert "载荷、面积" in inventory["audit_metric_semantics"]["direct_numeric_total"]
    assert "训练权重为零" in inventory["audit_metric_semantics"]["invalid_cached_formula_count"]

    pcl_supplement = ledger["PCL_GitLFS轨迹补采"]
    pcl_parent = ledger["Zenodo_PCL软段构象粗粒化MD"]
    assert pcl_supplement["source_identity_count_contribution"] == 0
    assert pcl_supplement["source_family_id"] == pcl_parent["source_family_id"]


def test_fifth_batch_experimental_transfer_sources_are_counted_without_row_inflation(
    inventory: dict,
):
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}

    fisher = ledger["DataInBrief_聚氨酯形状记忆多模态原始数据"]
    assert fisher["formulation_count"] == 12
    assert fisher["specimen_count"] is None
    assert fisher["curve_count_observed"] == 109
    assert fisher["curve_count_candidate"] == 109
    assert fisher["point_count_observed"] == 975_903
    assert fisher["point_count_candidate"] == 974_201
    assert fisher["weight_ceiling"] == 0.35
    assert fisher["license_status"] == "allow_with_attribution"

    lignin = ledger["Zenodo_木质素_TPU多模态数据"]
    assert lignin["formulation_count"] == 19
    assert lignin["curve_count_observed"] == 39
    assert lignin["curve_count_candidate"] == 22
    assert lignin["scalar_count_observed"] == 38
    assert lignin["scalar_count_candidate"] == 22
    assert lignin["point_count_observed"] == 106_731
    assert lignin["point_count_candidate"] == 92_346
    assert lignin["weight_ceiling"] == 0.20

    manifest = inventory["record_manifest"]
    fisher_curves = [
        row
        for row in manifest
        if row["source_directory"] == fisher["source_directory"]
        and row["record_granularity"] == "curve"
    ]
    assert len(fisher_curves) == 109
    assert len({row["curve_key"] for row in fisher_curves}) == 109
    assert sum(row["curve_count"] for row in fisher_curves) == 109
    assert sum(row["point_count"] for row in fisher_curves) == 975_903
    assert all(row["model_ready"] is False for row in fisher_curves)

    lignin_detail = [
        row
        for row in manifest
        if row["source_directory"] == lignin["source_directory"]
        and row["record_granularity"] in {"curve", "scalar"}
    ]
    assert sum(row["record_granularity"] == "curve" for row in lignin_detail) == 39
    assert sum(row["record_granularity"] == "scalar" for row in lignin_detail) == 12
    assert sum(
        row["scalar_count"]
        for row in lignin_detail
        if row["quality_status"] == "降权"
    ) == 22
    assert sum(row["quality_status"] == "隔离" for row in lignin_detail) == 21
    assert all(row["current_weight_materialized"] is False for row in lignin_detail)


def test_audited_local_mechanical_backlog_is_included_as_gold_e_reference(inventory: dict):
    ledger = {row["source_directory"]: row for row in inventory["source_ledger"]}
    expected = {
        "SelfHealingTPU_4TU": (68, 61, 148_379, 131_022, 32, 32),
        "Schwarz2022_EPU40": (45, 45, 73_500, 73_500, 205, 205),
        "Zenodo4156000": (33, 25, 377_353, 152_271, 0, 0),
        "Zenodo1098206": (55, 55, 43_032, 43_032, 63, 57),
    }
    for source, counts in expected.items():
        row = ledger[source]
        assert (
            row["curve_count_observed"],
            row["curve_count_candidate"],
            row["point_count_observed"],
            row["point_count_candidate"],
            row["scalar_count_observed"],
            row["scalar_count_candidate"],
        ) == counts
        assert row["origin_kind"] == "实验"
        assert row["scientific_role"] == "迁移"
        assert row["license_status"] == "allow_with_attribution"
        assert row["citation_keys"]

    assert ledger["SelfHealingTPU_4TU"]["material_count"] == 2
    assert ledger["SelfHealingTPU_4TU"]["run_count"] is None
    assert ledger["Zenodo4156000"]["material_count"] == 2
    assert ledger["Zenodo4156000"]["formulation_count"] == 2

    manifest = inventory["record_manifest"]
    backlog_rows = [row for row in manifest if row["source_directory"] in expected]
    assert sum(row["record_granularity"] == "curve" for row in backlog_rows) == 201
    assert sum(row["record_granularity"] == "scalar" for row in backlog_rows) == 97
    assert all(row["current_weight_materialized"] is False for row in backlog_rows)

    four_tu_mechanical = [
        row
        for row in backlog_rows
        if row["source_directory"] == "SelfHealingTPU_4TU"
        and row["record_granularity"] == "curve"
        and row["task"] == "mechanical"
    ]
    assert len(four_tu_mechanical) == 36
    assert len({row["specimen_key"] for row in four_tu_mechanical}) == 26

    zenodo_4156_curves = [
        row
        for row in backlog_rows
        if row["source_directory"] == "Zenodo4156000"
        and row["record_granularity"] == "curve"
    ]
    assert len(zenodo_4156_curves) == 33
    # 15个名义文件/运行标签中有1对跨工艺条件的逐字节重复载荷；
    # run_key保留名义条件，载荷去重由审计哈希和零权重门处理。
    assert len({row["run_key"] for row in zenodo_4156_curves}) == 15
    duplicate_rows = [row for row in zenodo_4156_curves if row["quality_status"] == "隔离"]
    assert duplicate_rows
    assert all(row["weight_ceiling"] == 0.0 for row in duplicate_rows)


def test_manifest_recomputes_strict_core_and_excludes_all_drum_controls(inventory: dict):
    manifest = inventory["record_manifest"]
    strict = [
        row
        for row in manifest
        if row["record_granularity"] == "curve"
        and row["source_directory"]
        in {
            "DRUM_TPUU_机械回收",
            "DRUM_TPUU_低天花板",
            "QUB_生物基三重自修复TPU",
        }
        and row["quality_status"] == "入选"
    ]
    assert sum(row["curve_count"] for row in strict) == 217
    assert sum(row["point_count"] for row in strict) == 913_608

    drum = [
        row
        for row in manifest
        if row["record_granularity"] == "curve"
        and row["source_directory"] == "DRUM_TPUU_机械回收"
    ]
    noncore = [row for row in drum if row["quality_status"] != "入选"]
    assert len(drum) == 158
    assert len(noncore) == 10
    assert sum(row["quality_status"] == "降权" for row in noncore) == 9
    assert sum(row["quality_status"] == "仅验证" for row in noncore) == 1
    assert all(row["weight_ceiling"] < 1.0 for row in noncore)

    rubber = next(row for row in drum if row["decision_basis"] == "排除核心训练")
    assert rubber["quality_status"] == "仅验证"
    assert rubber["weight_ceiling"] == 0.0


def test_doe_solid_cube_controls_are_four_specimens_and_sixteen_zero_weight_responses(
    inventory: dict,
):
    controls = [
        row
        for row in inventory["record_manifest"]
        if row["source_directory"] == "Mendeley_TPU压缩打印DOE"
        and row["record_granularity"] == "specimen"
        and "solid_cube_control" in row["specimen_key"]
    ]
    assert len(controls) == 4
    assert sum(row["specimen_count"] for row in controls) == 4
    assert sum(row["scalar_count"] for row in controls) == 16
    assert len({row["specimen_key"] for row in controls}) == 4
    assert len({row["leakage_group_key"] for row in controls}) == 1
    assert all(row["quality_status"] == "仅验证" for row in controls)
    assert all(row["weight_ceiling"] == 0.0 for row in controls)
    assert all(row["model_ready"] is False for row in controls)


def test_qub_auxiliary_curves_have_bounded_nonmaterialized_weight_ceilings(
    inventory: dict,
):
    rows = [
        row
        for row in inventory["record_manifest"]
        if row["source_directory"] == "QUB_生物基三重自修复TPU"
        and row["record_granularity"] == "curve"
        and row["quality_status"] == "降权"
    ]
    assert len(rows) == 27
    assert sum(row["weight_ceiling"] == 0.35 for row in rows) == 21
    assert sum(row["weight_ceiling"] == 0.25 for row in rows) == 6
    assert all(0.0 < row["weight_ceiling"] < 1.0 for row in rows)
    assert all(row["current_weight_materialized"] is False for row in rows)


def test_machine_and_human_ledgers_keep_traceable_source_citations(inventory: dict):
    ledger_rows = _csv_rows(LEDGER_PATH)
    manifest_rows = _csv_rows(MANIFEST_PATH)
    report = REPORT_PATH.read_text(encoding="utf-8")

    assert len(ledger_rows) == 77
    assert len(manifest_rows) == inventory["summary"]["manifest_row_count"]
    for row in ledger_rows:
        assert row["source_scope_id"].strip()
        assert row["canonical_identifier"].strip()
        # 正式引用键优先；老快照若只剩稳定仓库/DOI，必须显式保留该标识。
        assert row["citation_keys"].strip() or row["canonical_identifier"].startswith(
            ("doi:", "http://", "https://")
        )
        assert f"`{row['source_scope_id']}`" in report
        assert row["canonical_identifier"] in report

    assert "## 8. 数据来源参考文献" in report
    assert "https://doi.org/" in report


def test_gold_e_scalars_are_materialized_as_multifidelity_reference(
    inventory: dict,
):
    rows = _csv_rows(GOLD_E_TABLE_PATH)
    assert len(rows) == 5_230
    assert Counter(row["source_id"] for row in rows) == {
        "ledger_source_118": 143,
        "ledger_source_106": 95,
        "ledger_source_110": 217,
        "ledger_source_112": 45,
        "source_sciencedb_pue643_v1": 1_929,
        "source_zenodo_6406174": 171,
        "ledger_source_093": 1_170,
        "source_nature_spore_filled_tpu_source_data": 144,
        "source_sheffield_21510876_v1": 755,
        "source_figshare_31550614_sls_tpu_lattice": 375,
        "ledger_source_034": 186,
    }
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 2_756,
        "conditional_reference": 2_474,
    }
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert all(row["file_sha256"] and row["source_locator"] for row in rows)
    metadata = inventory["summary"]["gold_e_published_table_long_table"]
    assert metadata["row_count"] == 5_230
    assert metadata["current_weight_materialized"] is False
    assert inventory["summary"]["batch11_existing_experimental_scalar_audit"][
        "record_count"
    ] == 2_630

    manifest_rows = [
        row
        for row in inventory["record_manifest"]
        if row["audit_basis"] == "代码/审计/第十批ACS表格物化.py"
    ]
    assert len(manifest_rows) == 505
    assert Counter(row["gold_layer"] for row in manifest_rows) == {
        "Gold-E": 500,
        "Gold-C": 5,
    }


def test_batch10_gold_e_keeps_targets_and_context_without_label_inflation(
    inventory: dict,
):
    rows = _csv_rows(GOLD_E_TABLE_PATH)
    sciencedb = [
        row
        for row in rows
        if row["source_id"] == "source_sciencedb_pue643_v1"
    ]
    kinetics = [
        row for row in rows if row["source_id"] == "source_zenodo_6406174"
    ]

    assert len(sciencedb) == 1_929
    assert Counter(row["property_name"] for row in sciencedb) == {
        "logYM": 643,
        "logTS": 643,
        "logEB": 643,
    }
    assert {row["gold_admission_status"] for row in sciencedb} == {
        "conditional_reference"
    }
    assert all(
        len(json.loads(row["condition_value"])) == 20 for row in sciencedb
    )

    assert len(kinetics) == 171
    assert Counter(row["gold_admission_status"] for row in kinetics) == {
        "admitted_reference": 169,
        "conditional_reference": 2,
    }
    assert sum(float(row["value"]) == 0.0 for row in kinetics) == 23
    assert all(row["property_name"] == "NCO_content" for row in kinetics)
    assert all(row["training_weight"] == "" for row in [*sciencedb, *kinetics])

    manifest_rows = [
        row
        for row in inventory["record_manifest"]
        if row["audit_basis"] == "代码/审计/第十批多保真物化.py"
    ]
    assert len(manifest_rows) == 2_100
    assert {row["gold_layer"] for row in manifest_rows} == {"Gold-E"}


def test_two_runs_are_byte_reproducible_atomic_and_reconciled(
    inventory: dict,
    regenerated_inventory_outputs: tuple[dict[str, str], dict[str, str]],
):
    first, second = regenerated_inventory_outputs
    assert first == second
    for path in OUTPUT_PATHS:
        assert not list(path.parent.glob(f".{path.name}.*.tmp"))

    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    ledger_rows = _csv_rows(LEDGER_PATH)
    manifest_rows = _csv_rows(MANIFEST_PATH)
    report = REPORT_PATH.read_text(encoding="utf-8")
    assert payload["summary"]["audit_as_of_utc"] == "2026-07-21T14:00:00Z"
    assert len(payload["input_fingerprints"]) == payload["summary"]["input_file_count"]
    assert len(ledger_rows) == len(payload["source_ledger"]) == 77
    manifest_artifact = payload["record_manifest_artifact"]
    assert manifest_artifact == {
        "path": "结果/样本清单.csv.gz",
        "format": "csv.gz",
        "row_count": len(manifest_rows),
        "sha256": _sha256(MANIFEST_PATH),
    }
    assert JSON_PATH.stat().st_size < 1_000_000
    assert MANIFEST_PATH.stat().st_size < 10_000_000
    assert "| 严格核心键控试样/曲线/已审计点行 | 217 / 217 / 913,608 |" in report
    assert "| 当前模型就绪记录 | **0** |" in report


def test_source_profile_covers_every_open_data_directory():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    configured = {row["source_directory"] for row in profile["profiles"]}
    raw_root = ROOT / "数据/原始" / "外部数据" / "新增开放数据"
    actual = {path.name for path in raw_root.iterdir() if path.is_dir()}

    assert len(profile["baseline_profiles"]) == 4
    assert len(configured) == 69
    assert configured == actual
    backlog = profile["local_backlog_profiles"]
    assert len(backlog) == 4
    assert all((ROOT / "数据/原始" / row["source_path"]).is_dir() for row in backlog)


def test_source_profile_flow_mappings_do_not_silently_split_notes_on_commas():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    common_keys = {
        "source_directory",
        "task",
        "origin_kind",
        "scientific_role",
        "quality_status",
        "reference_admission_status",
        "weight_ceiling",
        "unit_status",
        "license_status",
        "dedup_status",
        "split_group_status",
        "completeness",
        "audit_basis",
        "notes",
        "counts",
    }
    section_extra_keys = {
        "baseline_profiles": {"source_id", "source_scope_id", "source_family_id"},
        "profiles": {"audit_metrics"},
        "local_backlog_profiles": {"source_path"},
    }

    for section, extra_keys in section_extra_keys.items():
        for row in profile[section]:
            assert set(row) <= common_keys | extra_keys, row.get("source_directory")

    wide_rate = next(
        row
        for row in profile["profiles"]
        if row["source_directory"] == "Mendeley_热可逆超分子PU宽应变率"
    )
    assert "35,919同步点" in wide_rate["notes"]
