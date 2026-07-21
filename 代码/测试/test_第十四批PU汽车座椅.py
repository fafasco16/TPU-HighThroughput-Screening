from __future__ import annotations

import csv
import json
from collections import Counter

import pytest

from 审计.第十批ACS表格物化 import RECORD_COLUMNS as CANONICAL_RECORD_COLUMNS
from 审计 import 第十四批PU汽车座椅 as seat_module
from 审计.第十四批PU汽车座椅 import (
    CURVE_SPECS,
    EXPECTED_ADMITTED_ROWS,
    EXPECTED_CONDITIONAL_ROWS,
    EXPECTED_CURVE_POINTS,
    EXPECTED_IFD_POINTS,
    EXPECTED_RELAXATION_POINTS,
    EXPECTED_SUMMARY_SCALARS,
    EXPECTED_TOTAL_ROWS,
    OUTPUT_AUDIT,
    OUTPUT_CHECKSUMS,
    OUTPUT_README,
    OUTPUT_TSV,
    PAPER_DOI,
    PROCESSED_DOI,
    RAW_DOI,
    RECORD_COLUMNS,
    SOURCE_FAMILY_KEY,
    SYSTEMS,
    audit,
    build_gold_e_rows,
    write_outputs,
)


@pytest.fixture(scope="module")
def payload() -> dict:
    return audit()


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return build_gold_e_rows()


def test_固定版本_许可证_论文家族与官方字节身份(payload: dict) -> None:
    metadata = payload["metadata"]
    assert metadata["raw_dataset"] == {
        "source_id": "source_mendeley_pu_seat_raw_v2",
        "doi": RAW_DOI,
        "version": 2,
        "published": "2019-07-15T04:15:27.132Z",
        "file_count": 18,
        "bytes": 1_938_121,
        "license": "CC BY 4.0",
    }
    assert metadata["processed_dataset"] == {
        "source_id": "source_mendeley_pu_seat_processed_v2",
        "doi": PROCESSED_DOI,
        "version": 2,
        "published": "2019-07-15T04:16:04.707Z",
        "file_count": 1,
        "bytes": 9_950,
        "license": "CC BY 4.0",
    }
    assert metadata["publication"]["doi"] == PAPER_DOI
    assert metadata["publication"]["authors"] == ["Moon", "Sinha", "Kwak", "Ha", "Oh"]
    assert metadata["publication"]["official_preview_pages"] == 1
    assert len(payload["input_identity"]) == 25
    assert all(len(item["sha256"]) == 64 for item in payload["input_identity"])

    family = payload["source_family"]
    assert family["source_family_key"] == SOURCE_FAMILY_KEY
    assert family["dataset_doi_count"] == 2
    assert family["datasets_are_independent"] is False
    assert family["same_publication_family"] is True
    assert family["independent_experiment_campaign_count"] == 1
    assert family["material_system_count"] == 9
    assert family["raw_curve_points_are_independent_samples"] is False
    assert family["processed_scalars_are_independent_replicates"] is False


def test_九体系_十八曲线_点数和采集数组结构(payload: dict) -> None:
    assert len(SYSTEMS) == 9
    assert len(CURVE_SPECS) == 18
    assert Counter(spec.curve_kind for spec in CURVE_SPECS) == {
        "ifd": 9,
        "stress_relaxation": 9,
    }
    assert Counter(spec.system_key for spec in CURVE_SPECS) == {
        key: 2 for key in SYSTEMS
    }
    curves = payload["curves"]
    assert curves["ifd_curve_count"] == 9
    assert curves["stress_relaxation_curve_count"] == 9
    assert curves["ifd_point_count"] == EXPECTED_IFD_POINTS == 6_320
    assert curves["stress_relaxation_point_count"] == EXPECTED_RELAXATION_POINTS == 67_276
    assert curves["total_curve_point_count"] == EXPECTED_CURVE_POINTS == 73_596
    assert curves["source_numeric_axis_and_value_count"] == 147_192
    assert len(curves["curve_summaries"]) == 18
    assert all(item["points"] > 0 for item in curves["curve_summaries"])


def test_汇总工作簿_九体系_五十九标量与缺失模式(payload: dict) -> None:
    summary = payload["processed_summary"]
    assert summary["sheet"] == "Sheet1"
    assert summary["headers"] == [
        "IHF",
        "MIF",
        "SF",
        "Hysters loss",
        "Hardness",
        "Stress relaxation",
        "Crosslink density",
    ]
    assert summary["material_system_count"] == 9
    assert summary["scalar_count"] == EXPECTED_SUMMARY_SCALARS == 59
    assert summary["crosslink_density_count"] == 5
    assert summary["type_a_to_d_crosslink_density_missing_by_source"] is True


def test_gold_e_字段契约_唯一性_分组和准入(rows: list[dict[str, str]]) -> None:
    assert RECORD_COLUMNS == CANONICAL_RECORD_COLUMNS
    assert len(rows) == EXPECTED_TOTAL_ROWS == 73_655
    assert all(tuple(row) == RECORD_COLUMNS for row in rows)
    assert len({row["source_record_id"] for row in rows}) == len(rows)
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert len({row["split_group"] for row in rows}) == 9
    assert {row["target_origin"] for row in rows} == {"experimental"}
    assert {row["license"] for row in rows} == {"CC BY 4.0"}
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "conditional_reference": EXPECTED_CONDITIONAL_ROWS,
        "admitted_reference": EXPECTED_ADMITTED_ROWS,
    }


def test_曲线单位边界_不把电压冒充绝对应力(rows: list[dict[str, str]]) -> None:
    curve_rows = [row for row in rows if row["record_kind"] == "mechanical_curve_point"]
    assert len(curve_rows) == EXPECTED_CURVE_POINTS
    assert Counter((row["property_name"], row["unit"]) for row in curve_rows) == {
        ("indentation_force", "kgf"): EXPECTED_IFD_POINTS,
        ("load_cell_signal_voltage", "V"): EXPECTED_RELAXATION_POINTS,
    }
    assert Counter(
        (row["condition_name"], row["condition_unit"]) for row in curve_rows
    ) == {
        ("indentation_displacement", "mm"): EXPECTED_IFD_POINTS,
        ("elapsed_time", "s"): EXPECTED_RELAXATION_POINTS,
    }
    assert all(row["gold_admission_status"] == "conditional_reference" for row in curve_rows)
    voltage_rows = [row for row in curve_rows if row["unit"] == "V"]
    assert all("not be treated as absolute force or stress" in row["notes"] for row in voltage_rows)


def test_代表记录_数值_定位与同族split(rows: list[dict[str, str]]) -> None:
    by_source_id = {row["source_record_id"]: row for row in rows}
    hard_ifd = by_source_id["pu_seat|system=hard_puf|curve=ifd|point=000001"]
    assert float(hard_ifd["condition_value"]) == pytest.approx(0.0942944109837447)
    assert float(hard_ifd["value"]) == pytest.approx(0.127118644067797)
    assert "x_line=21" in hard_ifd["source_locator"]
    assert "y_line=674" in hard_ifd["source_locator"]

    hard_relaxation = by_source_id[
        "pu_seat|system=hard_puf|curve=stress_relaxation|point=000000"
    ]
    assert float(hard_relaxation["condition_value"]) == pytest.approx(0.1)
    assert float(hard_relaxation["value"]) == pytest.approx(0.00733680276933566)

    hard_hardness = by_source_id[
        "pu_seat|system=hard_puf|summary=indentation_hardness"
    ]
    assert hard_hardness["value"] == "202.86"
    assert hard_hardness["unit"] == "N"
    assert hard_hardness["gold_admission_status"] == "admitted_reference"
    assert hard_hardness["split_group"] == hard_ifd["split_group"] == hard_relaxation["split_group"]

    crosslink = by_source_id[
        "pu_seat|system=soft_puf|summary=crosslink_density"
    ]
    assert crosslink["value"] == "18.8"
    assert crosslink["unit"] == "source_native_unit_unresolved"
    assert crosslink["gold_admission_status"] == "conditional_reference"
    assert not any(
        row["property_name"] == "crosslink_density"
        and row["formulation_id"].startswith("pu_seat_multilayer")
        for row in rows
    )


def test_原始与汇总内部交叉验证(payload: dict) -> None:
    crosschecks = payload["lineage_crosschecks"]
    assert crosschecks["hardness_exact_kgf_times_9_8_header_matches"] == 6
    assert crosschecks["hardness_within_1_newton_header_matches"] == 7
    assert crosschecks[
        "stress_relaxation_peak_to_terminal_max_abs_difference_percentage_points"
    ] < 5.0
    assert len(crosschecks["stress_relaxation_abs_differences_percentage_points"]) == 9


def test_输出物化_行数_校验清单和说明() -> None:
    payload = write_outputs()
    assert OUTPUT_TSV.is_file()
    assert OUTPUT_AUDIT.is_file()
    assert OUTPUT_CHECKSUMS.is_file()
    assert OUTPUT_README.is_file()

    with OUTPUT_TSV.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == RECORD_COLUMNS
        assert sum(1 for _ in reader) == EXPECTED_TOTAL_ROWS
    with OUTPUT_CHECKSUMS.open("r", encoding="utf-8", newline="") as handle:
        checksums = list(csv.DictReader(handle, delimiter="\t"))
    assert len(checksums) == 25
    assert {item["role"] for item in checksums} == {"raw_curve", "supporting_evidence"}

    persisted = json.loads(OUTPUT_AUDIT.read_text(encoding="utf-8"))
    assert persisted["materialization"] == payload["materialization"]
    readme = OUTPUT_README.read_text(encoding="utf-8")
    assert RAW_DOI in readme and PROCESSED_DOI in readme and PAPER_DOI in readme
    assert "73,655" in readme


def test_模块只物化来源目录_不修改共享总账() -> None:
    assert OUTPUT_TSV.parent == seat_module.SOURCE_DIR
    assert OUTPUT_AUDIT.parent == seat_module.SOURCE_DIR
    assert OUTPUT_CHECKSUMS.parent == seat_module.SOURCE_DIR
    assert OUTPUT_README.parent == seat_module.SOURCE_DIR
