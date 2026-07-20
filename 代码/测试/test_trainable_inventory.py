import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "生成数据总账.py"
JSON_PATH = ROOT / "结果" / "数据总账.json"
LEDGER_PATH = ROOT / "结果" / "数据规模总账.csv"
MANIFEST_PATH = ROOT / "结果" / "样本清单.csv"
REPORT_PATH = ROOT / "结果" / "数据总账说明.md"
PROFILE_PATH = ROOT / "配置" / "v0.2可训练样本总账来源画像.yaml"
OUTPUT_PATHS = (LEDGER_PATH, MANIFEST_PATH, JSON_PATH, REPORT_PATH)


@pytest.fixture(scope="module")
def inventory() -> dict:
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_and_scientific_denominator_totals_are_frozen(inventory: dict):
    summary = inventory["summary"]

    assert summary["ledger_source_scope_count"] == 60
    assert summary["v0_2_source_directory_count"] == 52
    assert summary["v0_2_independent_source_identity_count"] == 51
    assert summary["local_backlog_source_directory_count"] == 4
    assert summary["local_backlog_independent_source_identity_count"] == 4
    assert summary["v0_1_frozen_baseline_source_count"] == 4
    assert summary["total_independent_source_contribution_count"] == 59
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
        "value": 1354,
        "known_source_scope_count": 17,
    }
    assert experimental["curve_count_observed"] == {
        "value": 2380,
        "known_source_scope_count": 30,
    }
    assert experimental["curve_count_candidate"] == {
        "value": 2222,
        "known_source_scope_count": 30,
    }
    assert experimental["point_count_observed"] == {
        "value": 8_705_042,
        "known_source_scope_count": 30,
    }

    mixed = summary["known_origin_totals"]["mixed_experiment_and_simulation"]
    assert mixed["curve_count_observed"] == {
        "value": 344,
        "known_source_scope_count": 6,
    }
    assert mixed["point_count_observed"] == {
        "value": 7_606_461,
        "known_source_scope_count": 5,
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
        if row["quality_status"] in {"仅验证", "隔离"}
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
        "mixed": "Gold-E+Gold-C",
        "evidence": "Not-Gold",
    }
    assert all(
        row["gold_layer"]
        == layer_by_target_origin.get(row["target_origin"], expected[row["origin_kind"]])
        for row in manifest
    )
    assert all(row["target_origin"] for row in manifest)

    virtual = next(row for row in ledger if row["origin_kind"] == "虚拟候选")
    assert virtual["gold_layer"] == "Gold-V"
    assert virtual["gold_admission_status"] == "admitted_reference"
    assert virtual["weight_ceiling"] == 0.0
    assert virtual["model_ready_record_count"] == 0

    assert inventory["summary"]["source_gold_layer_counts"]["Gold-C"] > 0
    assert inventory["summary"]["source_gold_admission_status_counts"][
        "admitted_reference"
    ] > 0


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

    assert len(ledger_rows) == 60
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


def test_two_runs_are_byte_reproducible_atomic_and_reconciled(inventory: dict):
    first = {path.name: _sha256(path) for path in OUTPUT_PATHS}
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    second = {path.name: _sha256(path) for path in OUTPUT_PATHS}
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    third = {path.name: _sha256(path) for path in OUTPUT_PATHS}

    assert first == second == third
    for directory in {path.parent for path in OUTPUT_PATHS}:
        assert not list(directory.glob(".TPU数据库_v0.2_*.tmp"))

    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    ledger_rows = _csv_rows(LEDGER_PATH)
    manifest_rows = _csv_rows(MANIFEST_PATH)
    report = REPORT_PATH.read_text(encoding="utf-8")
    assert payload["summary"]["audit_as_of_utc"] == "2026-07-21T14:00:00Z"
    assert len(payload["input_fingerprints"]) == payload["summary"]["input_file_count"]
    assert len(ledger_rows) == len(payload["source_ledger"]) == 60
    assert len(manifest_rows) == len(payload["record_manifest"]) == 3748
    assert "| 严格核心键控试样/曲线/已审计点行 | 217 / 217 / 913,608 |" in report
    assert "| 当前模型就绪记录 | **0** |" in report


def test_source_profile_covers_every_open_data_directory():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    configured = {row["source_directory"] for row in profile["profiles"]}
    raw_root = ROOT / "数据/原始" / "外部数据" / "新增开放数据"
    actual = {path.name for path in raw_root.iterdir() if path.is_dir()}

    assert len(profile["baseline_profiles"]) == 4
    assert len(configured) == 52
    assert configured == actual
    backlog = profile["local_backlog_profiles"]
    assert len(backlog) == 4
    assert all((ROOT / "数据/原始" / row["source_path"]).is_dir() for row in backlog)
