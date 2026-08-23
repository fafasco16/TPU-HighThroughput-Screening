"""再生 PU 泡沫 Zenodo 5713819 数据的定向回归测试。"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import zipfile
from collections import Counter, defaultdict

import pytest

from 审计 import 第十六批再生PU泡沫 as recycled_module
from 审计.第十六批再生PU泡沫 import (
    ARCHIVE_BYTES,
    ARCHIVE_MD5,
    ARCHIVE_PATH,
    ARCHIVE_SHA256,
    DATA_ROOT,
    EXTRACTED_AGGREGATE_SHA256,
    EXTRACTED_BYTES,
    EXTRACTED_FILE_COUNT,
    EXPECTED_COMPRESSION_CANDIDATE_POINTS,
    EXPECTED_COMPRESSION_FILE_COUNT,
    EXPECTED_COMPRESSION_OBSERVED_POINTS,
    EXPECTED_COMPRESSION_VALID_CURVES,
    EXPECTED_DERIVED_ENDPOINTS,
    EXPECTED_GOLD_E_ROWS,
    EXPECTED_THERMAL_ENDPOINTS,
    EXPECTED_THERMAL_WORKBOOKS,
    EXPECTED_VISCOSITY_CURVES,
    EXPECTED_VISCOSITY_POINTS,
    PDF_BYTES,
    PDF_PATH,
    PDF_SHA256,
    RECORD_COLUMNS,
    SOURCE_FAMILY_KEY,
    _hash,
    _thermal_endpoint,
    _xlsx_cells,
    _xlsx_content_digest,
    audit,
    build_gold_e_rows,
    verify_source,
)


pytestmark = pytest.mark.skipif(
    not ARCHIVE_PATH.is_file() or not PDF_PATH.is_file(),
    reason="第十六批再生 PU 泡沫冻结原件不在当前检出中",
)


@pytest.fixture(scope="module")
def materialized() -> tuple[list[dict[str, object]], dict[str, object]]:
    return build_gold_e_rows()


@pytest.fixture(scope="module")
def rows(materialized: tuple[list[dict[str, object]], dict[str, object]]) -> list[dict[str, object]]:
    return materialized[0]


@pytest.fixture(scope="module")
def summary(materialized: tuple[list[dict[str, object]], dict[str, object]]) -> dict[str, object]:
    return materialized[1]


def test_冻结归档_论文与81文件内容身份() -> None:
    source = verify_source()
    assert ARCHIVE_PATH.stat().st_size == ARCHIVE_BYTES == 3_857_900
    assert _hash(ARCHIVE_PATH, "md5") == ARCHIVE_MD5
    assert _hash(ARCHIVE_PATH) == ARCHIVE_SHA256
    assert PDF_PATH.stat().st_size == PDF_BYTES == 1_465_953
    assert _hash(PDF_PATH) == PDF_SHA256
    assert source["pdf_pages"] == 21
    assert source["extracted_file_count"] == EXTRACTED_FILE_COUNT == 81
    assert source["extracted_bytes"] == EXTRACTED_BYTES == 7_161_867
    assert source["extracted_aggregate_sha256"] == EXTRACTED_AGGREGATE_SHA256
    assert source["dataset_doi"] == "10.5281/zenodo.5713819"
    assert source["article_doi"] == "10.12688/openreseurope.13288.2"
    assert source["license"] == "CC BY 4.0"
    assert source["peer_review_status"] == (
        "two_approved_with_reservations_two_not_approved"
    )

    raw_files = [path for path in DATA_ROOT.rglob("*") if path.is_file()]
    assert Counter(path.suffix.lower() for path in raw_files) == {
        ".csv": 59,
        ".xlsx": 18,
        ".txt": 3,
        ".jpg": 1,
    }


def test_压缩CSV双语结构_59条曲线均有效(summary: dict[str, object]) -> None:
    compression = summary["compression"]
    assert compression["file_count"] == EXPECTED_COMPRESSION_FILE_COUNT == 59
    assert compression["observed_point_count"] == EXPECTED_COMPRESSION_OBSERVED_POINTS == 45_922
    assert compression["valid_curve_count"] == EXPECTED_COMPRESSION_VALID_CURVES == 59
    assert compression["candidate_point_count"] == EXPECTED_COMPRESSION_CANDIDATE_POINTS == 13_159
    assert compression["derived_endpoint_count"] == EXPECTED_DERIVED_ENDPOINTS == 118
    assert compression["alternate_language_schema_files"] == [
        "RPUF1_compression_Specimen_7.csv"
    ]
    assert compression["excluded_insufficient_strain_files"] == []

    alternate = (
        DATA_ROOT
        / "RPUF00_compression_RawData/RPUF1_compression_Specimen_7.csv"
    )
    with alternate.open("r", encoding="cp1252", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = next(reader)
        units = next(reader)
        raw_rows = list(reader)
    assert len(header) == 13
    assert header[5] == "Recuento de ciclos"
    assert header[9] == "Deformación por compresión"
    assert units[9] == "(mm/mm)"
    assert len(raw_rows) == 828
    strains = [float(row[9]) for row in raw_rows]
    assert max(strains) == pytest.approx(0.93436)
    assert sum(0 <= value <= 0.27 for value in strains) == 239
    assert all(right > left for left, right in zip(strains, strains[1:]))


def test_压缩只保留0至27pct且论文端点复现(rows: list[dict[str, object]], summary: dict[str, object]) -> None:
    compression_points = [
        row
        for row in rows
        if row["record_kind"] == "curve_point"
        and row["property_name"] == "compressive_stress"
    ]
    assert len(compression_points) == EXPECTED_COMPRESSION_CANDIDATE_POINTS
    assert all(0 <= float(row["condition_value"]) <= 0.27 for row in compression_points)
    assert all(row["unit"] == "MPa" for row in compression_points)
    assert all(row["gold_admission_status"] == "conditional_reference" for row in compression_points)

    alternate_points = [
        row
        for row in compression_points
        if row["sample_id"] == "RPUF1_compression_Specimen_7"
    ]
    assert len(alternate_points) == 239
    assert float(alternate_points[0]["condition_value"]) == 0
    assert float(alternate_points[0]["value"]) == pytest.approx(0.00001)

    reproduction = summary["compression"]["aggregate_reproduction"]
    assert reproduction["RPUF_0"]["valid_raw_curve_count"] == 11
    assert {entry["valid_raw_curve_count"] for entry in reproduction.values()} == {6, 11}
    assert max(
        max(
            entry["absolute_difference_10pct_kpa"],
            entry["absolute_difference_25pct_kpa"],
        )
        for entry in reproduction.values()
    ) < 0.1


def test_十八工作簿_黏度计数与唯一导热重复冲突(summary: dict[str, object]) -> None:
    workbooks = sorted(DATA_ROOT.rglob("*.xlsx"))
    viscosity = sorted(DATA_ROOT.rglob("*viscosty.xlsx"))
    thermal = sorted(DATA_ROOT.glob("*thermal conductivity.xlsx"))
    assert len(workbooks) == 18
    assert len(viscosity) == EXPECTED_VISCOSITY_CURVES == 10
    assert len(thermal) == EXPECTED_THERMAL_WORKBOOKS == 8

    formula_count = 0
    for path in workbooks:
        with zipfile.ZipFile(path) as archive:
            sheet_xml = archive.read("xl/worksheets/sheet1.xml")
        formula_count += sheet_xml.count(b"<f>") + sheet_xml.count(b"<f ")
    assert formula_count == 0

    viscosity_summary = summary["viscosity"]
    assert viscosity_summary["point_count"] == EXPECTED_VISCOSITY_POINTS == 581
    assert viscosity_summary["points_per_curve"] == {
        "RPUF_0": 60,
        "RPUF_1.5": 60,
        "RPUF_3.0": 60,
        "RPUF_4.5": 60,
        "RPUF_6.0": 41,
        "SPUF_0": 60,
        "SPUF_1.5": 60,
        "SPUF_3.0": 60,
        "SPUF_4.5": 60,
        "SPUF_6.0": 60,
    }

    digest_groups: defaultdict[str, list[str]] = defaultdict(list)
    for path in workbooks:
        digest_groups[_xlsx_content_digest(path)].append(path.name)
    duplicate_groups = sorted(sorted(names) for names in digest_groups.values() if len(names) > 1)
    assert duplicate_groups == [[
        "RPUF00_thermal conductivity.xlsx",
        "RPUF30_thermal conductivity.xlsx",
    ]]
    by_name = {path.name: path for path in thermal}
    rpuf00 = by_name["RPUF00_thermal conductivity.xlsx"]
    rpuf30 = by_name["RPUF30_thermal conductivity.xlsx"]
    assert _hash(rpuf00) != _hash(rpuf30)
    assert _xlsx_cells(rpuf00) == _xlsx_cells(rpuf30)
    assert _thermal_endpoint(rpuf00) == _thermal_endpoint(rpuf30)

    thermal_summary = summary["thermal_conductivity"]
    assert thermal_summary["candidate_endpoint_count"] == EXPECTED_THERMAL_ENDPOINTS == 7
    assert set(thermal_summary["endpoints"]) == {
        "RPUF_0",
        "RPUF_1.5",
        "RPUF_4.5",
        "SPUF_0",
        "SPUF_1.5",
        "SPUF_3.0",
        "SPUF_4.5",
    }


def test_gold_e精确计数_字段契约_准入与泄漏边界(rows: list[dict[str, object]], summary: dict[str, object]) -> None:
    assert len(rows) == EXPECTED_GOLD_E_ROWS == 13_931
    assert all(tuple(row) == RECORD_COLUMNS for row in rows)
    assert len({str(row["observation_id"]) for row in rows}) == len(rows)
    assert Counter(str(row["record_kind"]) for row in rows) == {
        "curve_point": 13_740,
        "derived_scalar": 118,
        "aggregate_scalar": 27,
        "formulation_component": 39,
        "scalar_measurement": 7,
    }
    assert Counter(str(row["gold_admission_status"]) for row in rows) == {
        "conditional_reference": 13_865,
        "admitted_reference": 66,
    }
    assert summary["admission_counts"] == {
        "conditional_reference": 13_865,
        "admitted_reference": 66,
    }
    assert {str(row["split_group"]) for row in rows} == {SOURCE_FAMILY_KEY}
    assert {str(row["target_origin"]) for row in rows} == {"experimental"}
    assert {str(row["current_weight_materialized"]) for row in rows} == {"false"}
    assert all(row["training_weight"] == "" for row in rows)
    assert all(math.isfinite(float(row["value"])) for row in rows)
    assert all(len(str(row["file_sha256"])) == 64 for row in rows)
    assert summary["training_state"] == {
        "current_weight_materialized": False,
        "model_ready_record_count": 0,
        "split_group_count": 1,
    }


def test_论文表格66条正式参考与配方质量守恒(rows: list[dict[str, object]], summary: dict[str, object]) -> None:
    assert summary["article_tables"] == {
        "formulation_component_rows": 39,
        "apparent_density_rows": 9,
        "compression_aggregate_rows": 18,
    }
    admitted = [row for row in rows if row["gold_admission_status"] == "admitted_reference"]
    assert len(admitted) == 66
    components: defaultdict[str, list[float]] = defaultdict(list)
    for row in admitted:
        if row["record_kind"] == "formulation_component":
            components[str(row["formulation_id"])].append(float(row["value"]))
    assert set(components) == set(recycled_module.FORMULATION_DATA)
    assert all(sum(values) == pytest.approx(100.0) for values in components.values())
    assert not any(
        row["property_name"] == "apparent_density"
        and row["formulation_id"] in {"RPUF_10", "SPUF_6.0"}
        for row in rows
    )


def test_输出完整_校验清单且可重复生成(tmp_path, monkeypatch) -> None:
    outputs = {
        "OUTPUT_GOLD_E": tmp_path / "Gold_E_实验记录.tsv",
        "OUTPUT_COMPRESSION": tmp_path / "压缩曲线点.tsv",
        "OUTPUT_VISCOSITY": tmp_path / "黏度曲线点.tsv",
        "OUTPUT_AUDIT": tmp_path / "内容审计摘要.json",
        "OUTPUT_CHECKSUMS": tmp_path / "文件校验清单.tsv",
        "OUTPUT_README": tmp_path / "来源说明.md",
    }
    for name, path in outputs.items():
        monkeypatch.setattr(recycled_module, name, path)

    recycled_module.main()
    first_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in outputs.values()
    }
    with outputs["OUTPUT_GOLD_E"].open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == RECORD_COLUMNS
        assert sum(1 for _ in reader) == EXPECTED_GOLD_E_ROWS
    with outputs["OUTPUT_CHECKSUMS"].open("r", encoding="utf-8", newline="") as handle:
        checksums = list(csv.DictReader(handle, delimiter="\t"))
    assert len(checksums) == 83
    assert {row["path"] for row in checksums}.issuperset(
        {recycled_module._relative(ARCHIVE_PATH), recycled_module._relative(PDF_PATH)}
    )
    persisted = json.loads(outputs["OUTPUT_AUDIT"].read_text(encoding="utf-8"))
    assert persisted == audit()
    readme = outputs["OUTPUT_README"].read_text(encoding="utf-8")
    assert "13,931" in readme
    assert "西班牙语 13 列导出" in readme

    recycled_module.main()
    second_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in outputs.values()
    }
    assert first_hashes == second_hashes
