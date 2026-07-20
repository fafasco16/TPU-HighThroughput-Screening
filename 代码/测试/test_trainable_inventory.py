import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "生成v0.2可训练样本总账.py"
JSON_PATH = ROOT / "06_审核导出" / "TPU数据库_v0.2_可训练样本总账.json"
LEDGER_PATH = ROOT / "06_审核导出" / "TPU数据库_v0.2_数据规模总账.csv"
MANIFEST_PATH = ROOT / "06_审核导出" / "TPU数据库_v0.2_可训练样本清单.csv"
REPORT_PATH = ROOT / "文档" / "质量报告" / "TPU数据库_v0.2_可训练样本与数据规模总账.md"
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

    assert summary["ledger_source_scope_count"] == 50
    assert summary["v0_2_source_directory_count"] == 46
    assert summary["v0_2_independent_source_identity_count"] == 45
    assert summary["v0_1_frozen_baseline_source_count"] == 4
    assert summary["total_independent_source_contribution_count"] == 49
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
        "value": 1258,
        "known_source_scope_count": 14,
    }
    assert experimental["curve_count_observed"] == {
        "value": 2031,
        "known_source_scope_count": 24,
    }
    assert experimental["curve_count_candidate"] == {
        "value": 1905,
        "known_source_scope_count": 24,
    }
    assert experimental["point_count_observed"] == {
        "value": 6_980_144,
        "known_source_scope_count": 24,
    }

    mixed = summary["known_origin_totals"]["mixed_experiment_and_simulation"]
    assert mixed["curve_count_observed"] == {
        "value": 344,
        "known_source_scope_count": 5,
    }
    assert mixed["point_count_observed"] == {
        "value": 7_606_461,
        "known_source_scope_count": 4,
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

    assert len(ledger_rows) == 50
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
    assert payload["summary"]["audit_as_of_utc"] == "2026-07-20T00:00:00Z"
    assert len(payload["input_fingerprints"]) == payload["summary"]["input_file_count"]
    assert len(ledger_rows) == len(payload["source_ledger"]) == 50
    assert len(manifest_rows) == len(payload["record_manifest"]) == 3132
    assert "| 严格核心键控试样/曲线/已审计点行 | 217 / 217 / 913,608 |" in report
    assert "| 当前模型就绪记录 | **0** |" in report


def test_source_profile_covers_every_open_data_directory():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    configured = {row["source_directory"] for row in profile["profiles"]}
    raw_root = ROOT / "01_原始数据" / "外部数据" / "新增开放数据"
    actual = {path.name for path in raw_root.iterdir() if path.is_dir()}

    assert len(profile["baseline_profiles"]) == 4
    assert len(configured) == 46
    assert configured == actual
