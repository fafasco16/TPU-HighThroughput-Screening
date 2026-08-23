"""审计并物化老化植物基聚氨酯泡沫的 4,200 次有限元模拟。

固定原件来自 Mendeley Data DOI ``10.17632/n9h66xjk7y.1``。本模块直接
从 ZIP 流读取 ``Age.npy``、``Temp.npy`` 和 4,200 条 Abaqus RPT 曲线，
不把归档解包到文件系统，也不把 424,200 个曲线点误计成独立材料样本。

同一方向、老化时间和温度构成一个输入工况。重复工况只保留最低运行编号
作为 Gold-C 代表，但所有原始运行仍保留在审计清单中。训练划分和实际训练
权重均不在这里生成。
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import os
import stat
import struct
import tempfile
import zipfile
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十九批模拟_老化植物基PU泡沫"
)
ARCHIVE_PATH = SOURCE_DIR / "n9h66xjk7y-1.zip"
OUTPUT_AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUTPUT_CHECKSUMS = SOURCE_DIR / "文件校验清单.tsv"
OUTPUT_RUNS = SOURCE_DIR / "模拟运行清单.tsv"
OUTPUT_GOLD_C = SOURCE_DIR / "Gold_C_紧凑标量表.tsv"
OUTPUT_README = SOURCE_DIR / "来源说明.md"

SOURCE_ID = "source_mendeley_n9h66xjk7y_v1"
DATASET_DOI = "10.17632/n9h66xjk7y.1"
ARTICLE_DOI = "10.1002/pen.27196"
AGING_ARTICLE_DOI = "10.1002/pen.26725"
EXPERIMENT_DATA_DOI = "10.17632/2sp8fyvhfm.3"
DATA_IN_BRIEF_DOI = "10.1016/j.dib.2024.110199"
LICENSE = "CC BY 4.0"
DATASET_URL = "https://data.mendeley.com/datasets/n9h66xjk7y/1"
DOWNLOAD_URL = "https://data.mendeley.com/public-api/zip/n9h66xjk7y/download/1"
API_URL = "https://data.mendeley.com/public-api/datasets/n9h66xjk7y"

ARCHIVE_BYTES = 7_042_325
ARCHIVE_SHA256 = "e672f248e43b5cd6d9173ec87612643749b2b7e59ef528975b64d82d69e27b02"
EXPECTED_MEMBER_COUNT = 4_202
EXPECTED_UNCOMPRESSED_BYTES = 29_380_486
EXPECTED_COMPRESSED_MEMBER_BYTES = 6_052_929
EXPECTED_RUN_COUNT = 4_200
EXPECTED_POINT_COUNT_PER_RUN = 101
EXPECTED_TOTAL_POINT_COUNT = 424_200
EXPECTED_UNIQUE_CONDITION_COUNT = 3_868
EXPECTED_UNIQUE_NUMERIC_CURVE_COUNT = 3_863
EXPECTED_UNIQUE_RAW_RPT_COUNT = 4_192
EXPECTED_GOLD_C_ROW_COUNT = 19_340

ROOT_MEMBER = "Simulated Results for Aged Polyurethane Foam"
AGE_MEMBER = f"{ROOT_MEMBER}/Age.npy"
TEMP_MEMBER = f"{ROOT_MEMBER}/Temp.npy"
RPT_DIR = f"{ROOT_MEMBER}/Stress-Strain data"
AGE_SHA256 = "25d7252161749991f1c18adf5615defa6026990b58b32b830659e1560a1f0f77"
TEMP_SHA256 = "04117494cd06fd972e4f127d46824400a6862dc297051c26ce760ae235ae7736"

GLOBAL_STRUCTURE_FAMILY_KEY = "family_mendeley_aged_vegetable_puf"
SYSTEM_IDENTITY = (
    "single nominal castor-oil-based polyurethane foam formulation; "
    "commercial Kehl IC200 MDI and KT1106-R vegetable-oil polyol blend at "
    "1:1 mass ratio; exact proprietary molecular composition unresolved"
)
STRUCTURE_IDENTITY_STATUS = (
    "single_nominal_formulation_commercial_component_identity_only_"
    "exact_structure_unresolved"
)
METHOD_FAMILY = "Abaqus_UMAT_Arrhenius_large_deformation"
METHOD_DETAIL = (
    "Abaqus UMAT finite-element constitutive simulation using Jaumann stress "
    "rate, logarithmic strain and Arrhenius aging parameters reported in "
    "DOI 10.1002/pen.26725"
)
FIDELITY_LEVEL = "experiment_anchored_constitutive_finite_element_simulation"
CITATION_KEYS = (
    "ledger-174-pires-da-silva-2024-aged-puf-simulation-data;"
    "ledger-175-pires-2025-aged-puf-dnn;"
    "ledger-176-da-silva-2024-aged-puf-arrhenius;"
    "ledger-084-pires-da-silva-2023-aged-puf-data"
)

DIRECTION_DESCRIPTIONS = {
    1: "transverse_to_expansion",
    3: "parallel_to_expansion",
}

GOLD_C_VALUE_COLUMNS = (
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "system_identity",
    "structure_identity_status",
    "global_structure_family_key",
    "simulation_key",
    "split_group",
    "property_name",
    "value",
    "unit",
    "unit_status",
    "method_family",
    "method_detail",
    "fidelity_level",
    "temp",
    "press",
    "gold_admission_status",
    "property_admission_status",
    "source_validation_status",
    "record_role",
    "potential_weight_ceiling",
    "current_weight_materialized",
    "training_weight",
    "source_locator",
    "citation_keys",
)

FILE_COLUMNS = (
    "member_path",
    "file_role",
    "bytes",
    "compressed_bytes",
    "crc32",
    "sha256",
)

RUN_COLUMNS = (
    "run_id",
    "direction_code",
    "direction_description",
    "age_days",
    "temperature_C",
    "condition_key",
    "condition_group_size",
    "canonical_run_id",
    "condition_duplicate_status",
    "rpt_member_path",
    "point_count",
    "x_min",
    "x_max",
    "compressive_log_strain_max",
    "stress_min_Pa",
    "stress_max_Pa",
    "stress_at_log_strain_0_1_MPa",
    "stress_at_log_strain_0_5_MPa",
    "stress_at_log_strain_1_0_MPa",
    "energy_density_to_max_log_strain_MJ_m3",
    "raw_sha256",
    "numerical_curve_sha256",
    "numerical_curve_group_size",
    "numerical_curve_unique_condition_count",
    "cross_condition_curve_collision",
    "curve_quality_status",
    "issue_notes",
)

GOLD_PROPERTIES = (
    (
        "mises_stress_at_compressive_log_strain_0_1",
        "stress_at_log_strain_0_1_MPa",
        "MPa",
        "linear_interpolation_from_native_Pa_then_converted_to_MPa",
    ),
    (
        "mises_stress_at_compressive_log_strain_0_5",
        "stress_at_log_strain_0_5_MPa",
        "MPa",
        "linear_interpolation_from_native_Pa_then_converted_to_MPa",
    ),
    (
        "mises_stress_at_compressive_log_strain_1_0",
        "stress_at_log_strain_1_0_MPa",
        "MPa",
        "linear_interpolation_from_native_Pa_then_converted_to_MPa",
    ),
    (
        "maximum_observed_mises_stress",
        "stress_max_Pa",
        "MPa",
        "maximum_native_Pa_converted_to_MPa",
    ),
    (
        "compression_energy_density_to_max_observed_log_strain",
        "energy_density_to_max_log_strain_MJ_m3",
        "MJ/m3",
        "trapezoidal_integral_of_native_Pa_over_log_strain_converted_to_MJ_per_m3",
    ),
)


class AuditBlocked(RuntimeError):
    """固定原件、归档边界或数值语义发生漂移。"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _finite_text(value: float | int) -> str:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise AuditBlocked(f"尝试输出非有限数值: {value!r}")
    if numeric == 0:
        return "0"
    return format(numeric, ".15g")


def _safe_member(info: zipfile.ZipInfo) -> None:
    if info.is_dir():
        raise AuditBlocked(f"固定归档不应含目录成员: {info.filename!r}")
    if "\\" in info.filename or "\x00" in info.filename:
        raise AuditBlocked(f"ZIP 成员路径格式不安全: {info.filename!r}")
    path = PurePosixPath(info.filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AuditBlocked(f"ZIP 成员路径逃逸: {info.filename!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise AuditBlocked(f"ZIP 成员是符号链接: {info.filename!r}")
    if info.flag_bits & 0x1:
        raise AuditBlocked(f"ZIP 成员被加密: {info.filename!r}")


def _expected_members() -> list[str]:
    return [
        AGE_MEMBER,
        TEMP_MEMBER,
        *(f"{RPT_DIR}/{run_id}.rpt" for run_id in range(EXPECTED_RUN_COUNT)),
    ]


def _load_condition_array(payload: bytes, label: str) -> list[int]:
    try:
        array = np.load(io.BytesIO(payload), allow_pickle=False)
    except (OSError, ValueError) as error:
        raise AuditBlocked(f"{label}.npy 无法安全读取") from error
    if array.shape != (EXPECTED_RUN_COUNT,) or array.dtype != np.dtype("float64"):
        raise AuditBlocked(
            f"{label}.npy 形状或类型漂移: shape={array.shape}, dtype={array.dtype}"
        )
    if not np.isfinite(array).all() or not np.equal(array, np.rint(array)).all():
        raise AuditBlocked(f"{label}.npy 含非有限值或非整数条件")
    return [int(value) for value in array.tolist()]


def _parse_rpt(payload: bytes, member_path: str) -> list[tuple[float, float, float]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise AuditBlocked(f"RPT 不是 UTF-8: {member_path}") from error
    points: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            values = tuple(float(field) for field in fields)
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in values):
            raise AuditBlocked(f"RPT 含非有限数值: {member_path}")
        points.append(values)  # type: ignore[arg-type]
    if len(points) != EXPECTED_POINT_COUNT_PER_RUN:
        raise AuditBlocked(
            f"RPT 数值点数漂移: {member_path}; expected=101, actual={len(points)}"
        )
    return points


def _numeric_curve_sha256(points: Iterable[tuple[float, float, float]]) -> str:
    digest = hashlib.sha256()
    for point in points:
        digest.update(struct.pack("<ddd", *point))
    return digest.hexdigest()


def _interpolate(x: list[float], y: list[float], target: float) -> float:
    if target < x[0] or target > x[-1]:
        raise AuditBlocked(
            f"插值目标超出曲线范围: target={target}, range=({x[0]}, {x[-1]})"
        )
    index = bisect.bisect_left(x, target)
    if index < len(x) and math.isclose(x[index], target, abs_tol=1e-12):
        return y[index]
    if index == 0 or index == len(x):
        raise AuditBlocked(f"无法对目标值插值: {target}")
    left_x, right_x = x[index - 1], x[index]
    if right_x <= left_x:
        raise AuditBlocked("插值区间不是严格递增")
    fraction = (target - left_x) / (right_x - left_x)
    return y[index - 1] + fraction * (y[index] - y[index - 1])


def _trapezoid(x: list[float], y: list[float]) -> float:
    return sum(
        (right_x - left_x) * (left_y + right_y) / 2
        for left_x, right_x, left_y, right_y in zip(x, x[1:], y, y[1:])
    )


def _histogram(values: Iterable[int]) -> dict[str, int]:
    return {
        str(key): value
        for key, value in sorted(Counter(values).items(), key=lambda pair: pair[0])
    }


def _condition_key(direction: int, age: int, temperature: int) -> str:
    return (
        f"direction={direction}|age_days={age}|temperature_C={temperature}"
    )


def _simulation_key(direction: int, age: int, temperature: int) -> str:
    return (
        f"mendeley:n9h66xjk7y:v1:direction={direction}:"
        f"age_days={age}:temperature_C={temperature}"
    )


def _file_role(member_path: str) -> str:
    if member_path == AGE_MEMBER:
        return "simulation_age_condition_array"
    if member_path == TEMP_MEMBER:
        return "simulation_temperature_condition_array"
    return "abaqus_stress_strain_report"


def _validate_archive_identity() -> None:
    if not ARCHIVE_PATH.is_file():
        raise AuditBlocked(f"缺少固定原件: {ARCHIVE_PATH}")
    size = ARCHIVE_PATH.stat().st_size
    sha256 = _sha256_file(ARCHIVE_PATH)
    if (size, sha256) != (ARCHIVE_BYTES, ARCHIVE_SHA256):
        raise AuditBlocked(
            "固定 ZIP 漂移: "
            f"bytes={size}, sha256={sha256}, expected={ARCHIVE_BYTES}/{ARCHIVE_SHA256}"
        )


def _curve_metrics(
    points: list[tuple[float, float, float]], run_id: int
) -> tuple[dict[str, Any], list[str]]:
    x_values = [point[0] for point in points]
    strain = [-point[1] for point in points]
    stress = [point[2] for point in points]

    if any(value < -1e-12 for value in strain) or any(value < -1e-12 for value in stress):
        raise AuditBlocked(f"运行 {run_id} 含负的压缩应变或 Mises 应力")
    if any(right < left for left, right in zip(strain, strain[1:])):
        raise AuditBlocked(f"运行 {run_id} 的压缩对数应变非单调")
    if any(right < left for left, right in zip(stress, stress[1:])):
        raise AuditBlocked(f"运行 {run_id} 的 Mises 应力非单调")

    expected_x = [index / 100 for index in range(EXPECTED_POINT_COUNT_PER_RUN)]
    x_grid_ok = all(
        math.isclose(actual, expected, abs_tol=1e-12)
        for actual, expected in zip(x_values, expected_x, strict=True)
    )
    duplicate_x_count = sum(
        math.isclose(left, right, abs_tol=1e-12)
        for left, right in zip(x_values, x_values[1:])
    )
    issues: list[str] = []
    if not x_grid_ok:
        issues.append("expected_x_grid_0_to_1_by_0_01_not_met")
    if duplicate_x_count:
        issues.append(f"duplicate_adjacent_x_count={duplicate_x_count}")
    if not math.isclose(x_values[-1], 1.0, abs_tol=1e-12):
        issues.append("final_x_1_0_missing")

    metrics: dict[str, Any] = {
        "point_count": len(points),
        "x_min": min(x_values),
        "x_max": max(x_values),
        "compressive_log_strain_max": max(strain),
        "stress_min_Pa": min(stress),
        "stress_max_Pa": max(stress),
        "stress_at_log_strain_0_1_MPa": _interpolate(strain, stress, 0.1)
        / 1_000_000,
        "stress_at_log_strain_0_5_MPa": _interpolate(strain, stress, 0.5)
        / 1_000_000,
        "stress_at_log_strain_1_0_MPa": _interpolate(strain, stress, 1.0)
        / 1_000_000,
        "energy_density_to_max_log_strain_MJ_m3": _trapezoid(strain, stress)
        / 1_000_000,
    }
    return metrics, issues


def _raw_audit() -> dict[str, Any]:
    _validate_archive_identity()
    expected_members = _expected_members()
    expected_member_set = set(expected_members)

    try:
        archive = zipfile.ZipFile(ARCHIVE_PATH)
    except zipfile.BadZipFile as error:
        raise AuditBlocked("固定原件不是有效 ZIP") from error

    with archive:
        infos = archive.infolist()
        if len(infos) != EXPECTED_MEMBER_COUNT:
            raise AuditBlocked(
                f"ZIP 成员数漂移: expected={EXPECTED_MEMBER_COUNT}, actual={len(infos)}"
            )
        if len({info.filename for info in infos}) != len(infos):
            raise AuditBlocked("ZIP 含重复成员路径")
        for info in infos:
            _safe_member(info)
        actual_member_set = {info.filename for info in infos}
        if actual_member_set != expected_member_set:
            missing = sorted(expected_member_set - actual_member_set)[:5]
            extra = sorted(actual_member_set - expected_member_set)[:5]
            raise AuditBlocked(f"ZIP 成员集合漂移: missing={missing}, extra={extra}")
        if sum(info.file_size for info in infos) != EXPECTED_UNCOMPRESSED_BYTES:
            raise AuditBlocked("ZIP 解压后总字节数漂移")
        if sum(info.compress_size for info in infos) != EXPECTED_COMPRESSED_MEMBER_BYTES:
            raise AuditBlocked("ZIP 成员压缩字节数漂移")
        if archive.testzip() is not None:
            raise AuditBlocked("ZIP CRC 校验失败")

        info_by_name = {info.filename: info for info in infos}
        payloads: dict[str, bytes] = {}
        file_rows: list[dict[str, str]] = []
        for member_path in expected_members:
            info = info_by_name[member_path]
            payload = archive.read(info)
            payloads[member_path] = payload
            file_rows.append(
                {
                    "member_path": member_path,
                    "file_role": _file_role(member_path),
                    "bytes": str(info.file_size),
                    "compressed_bytes": str(info.compress_size),
                    "crc32": f"{info.CRC:08x}",
                    "sha256": _sha256_bytes(payload),
                }
            )

    if _sha256_bytes(payloads[AGE_MEMBER]) != AGE_SHA256:
        raise AuditBlocked("Age.npy SHA-256 漂移")
    if _sha256_bytes(payloads[TEMP_MEMBER]) != TEMP_SHA256:
        raise AuditBlocked("Temp.npy SHA-256 漂移")
    ages = _load_condition_array(payloads[AGE_MEMBER], "Age")
    temperatures = _load_condition_array(payloads[TEMP_MEMBER], "Temp")

    run_work: list[dict[str, Any]] = []
    condition_to_runs: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    numeric_hash_to_runs: dict[str, list[int]] = defaultdict(list)
    raw_hash_to_runs: dict[str, list[int]] = defaultdict(list)
    issue_runs: list[dict[str, Any]] = []

    for run_id in range(EXPECTED_RUN_COUNT):
        direction = 1 if run_id < 2_100 else 3
        member_path = f"{RPT_DIR}/{run_id}.rpt"
        payload = payloads.pop(member_path)
        points = _parse_rpt(payload, member_path)
        raw_hash = _sha256_bytes(payload)
        numeric_hash = _numeric_curve_sha256(points)
        metrics, issues = _curve_metrics(points, run_id)
        condition = (direction, ages[run_id], temperatures[run_id])
        condition_to_runs[condition].append(run_id)
        numeric_hash_to_runs[numeric_hash].append(run_id)
        raw_hash_to_runs[raw_hash].append(run_id)
        row: dict[str, Any] = {
            "run_id": run_id,
            "direction_code": direction,
            "direction_description": DIRECTION_DESCRIPTIONS[direction],
            "age_days": ages[run_id],
            "temperature_C": temperatures[run_id],
            "condition": condition,
            "condition_key": _condition_key(*condition),
            "rpt_member_path": member_path,
            "raw_sha256": raw_hash,
            "numerical_curve_sha256": numeric_hash,
            "curve_quality_status": "conditional_missing_final_step" if issues else "pass",
            "issue_notes": ";".join(issues),
            **metrics,
        }
        run_work.append(row)
        if issues:
            issue_runs.append(
                {
                    "run_id": run_id,
                    "condition_key": row["condition_key"],
                    "member_path": member_path,
                    "issues": issues,
                }
            )

    del payloads

    numeric_hash_conditions = {
        curve_hash: {run_work[run_id]["condition"] for run_id in run_ids}
        for curve_hash, run_ids in numeric_hash_to_runs.items()
    }
    condition_groups_with_multiple_curves = sum(
        len({run_work[run_id]["numerical_curve_sha256"] for run_id in run_ids}) > 1
        for run_ids in condition_to_runs.values()
    )
    if condition_groups_with_multiple_curves:
        raise AuditBlocked("相同输入工况出现不同数值曲线，不能自动去重")

    run_rows: list[dict[str, str]] = []
    for row in run_work:
        condition_runs = condition_to_runs[row["condition"]]
        canonical_run_id = min(condition_runs)
        condition_size = len(condition_runs)
        curve_runs = numeric_hash_to_runs[row["numerical_curve_sha256"]]
        curve_condition_count = len(
            numeric_hash_conditions[row["numerical_curve_sha256"]]
        )
        if condition_size == 1:
            duplicate_status = "unique_condition"
        elif row["run_id"] == canonical_run_id:
            duplicate_status = "canonical_representative_of_repeated_condition"
        else:
            duplicate_status = "duplicate_repeated_condition"
        run_rows.append(
            {
                "run_id": str(row["run_id"]),
                "direction_code": str(row["direction_code"]),
                "direction_description": row["direction_description"],
                "age_days": str(row["age_days"]),
                "temperature_C": str(row["temperature_C"]),
                "condition_key": row["condition_key"],
                "condition_group_size": str(condition_size),
                "canonical_run_id": str(canonical_run_id),
                "condition_duplicate_status": duplicate_status,
                "rpt_member_path": row["rpt_member_path"],
                "point_count": str(row["point_count"]),
                "x_min": _finite_text(row["x_min"]),
                "x_max": _finite_text(row["x_max"]),
                "compressive_log_strain_max": _finite_text(
                    row["compressive_log_strain_max"]
                ),
                "stress_min_Pa": _finite_text(row["stress_min_Pa"]),
                "stress_max_Pa": _finite_text(row["stress_max_Pa"]),
                "stress_at_log_strain_0_1_MPa": _finite_text(
                    row["stress_at_log_strain_0_1_MPa"]
                ),
                "stress_at_log_strain_0_5_MPa": _finite_text(
                    row["stress_at_log_strain_0_5_MPa"]
                ),
                "stress_at_log_strain_1_0_MPa": _finite_text(
                    row["stress_at_log_strain_1_0_MPa"]
                ),
                "energy_density_to_max_log_strain_MJ_m3": _finite_text(
                    row["energy_density_to_max_log_strain_MJ_m3"]
                ),
                "raw_sha256": row["raw_sha256"],
                "numerical_curve_sha256": row["numerical_curve_sha256"],
                "numerical_curve_group_size": str(len(curve_runs)),
                "numerical_curve_unique_condition_count": str(
                    curve_condition_count
                ),
                "cross_condition_curve_collision": (
                    "true" if curve_condition_count > 1 else "false"
                ),
                "curve_quality_status": row["curve_quality_status"],
                "issue_notes": row["issue_notes"],
            }
        )

    condition_sizes = [len(run_ids) for run_ids in condition_to_runs.values()]
    numeric_curve_sizes = [
        len(run_ids) for run_ids in numeric_hash_to_runs.values()
    ]
    raw_curve_sizes = [len(run_ids) for run_ids in raw_hash_to_runs.values()]
    cross_condition_collisions = []
    for curve_hash, conditions in sorted(numeric_hash_conditions.items()):
        if len(conditions) <= 1:
            continue
        run_ids = sorted(numeric_hash_to_runs[curve_hash])
        cross_condition_collisions.append(
            {
                "numerical_curve_sha256": curve_hash,
                "run_ids": run_ids,
                "unique_condition_count": len(conditions),
                "condition_keys": sorted(_condition_key(*condition) for condition in conditions),
            }
        )

    raw_duplicate_groups = [
        {
            "raw_sha256": curve_hash,
            "run_ids": sorted(run_ids),
            "condition_keys": sorted(
                {run_work[run_id]["condition_key"] for run_id in run_ids}
            ),
        }
        for curve_hash, run_ids in sorted(raw_hash_to_runs.items())
        if len(run_ids) > 1
    ]

    direction_summary: dict[str, dict[str, Any]] = {}
    for direction in (1, 3):
        selected = [row for row in run_work if row["direction_code"] == direction]
        direction_summary[str(direction)] = {
            "description": DIRECTION_DESCRIPTIONS[direction],
            "simulation_run_count": len(selected),
            "unique_condition_count": len({row["condition"] for row in selected}),
            "unique_numerical_curve_count": len(
                {row["numerical_curve_sha256"] for row in selected}
            ),
        }

    raw_summary: dict[str, Any] = {
        "audit_version": "batch19-aged-vegetable-puf-simulation-v1",
        "source_id": SOURCE_ID,
        "dataset": {
            "title": "Simulated Results for Aged Polyurethane Foam",
            "doi": DATASET_DOI,
            "version": 1,
            "published": "2024-03-20",
            "contributor": "Enio Henrique Pires Da Silva",
            "institution": "Universidade de Sao Paulo",
            "license": LICENSE,
            "landing_page": DATASET_URL,
            "official_api": API_URL,
            "official_download": DOWNLOAD_URL,
            "associated_article_doi": ARTICLE_DOI,
            "arrhenius_parameter_article_doi": AGING_ARTICLE_DOI,
            "experimental_dataset_doi": EXPERIMENT_DATA_DOI,
            "experimental_data_article_doi": DATA_IN_BRIEF_DOI,
        },
        "archive": {
            "filename": ARCHIVE_PATH.name,
            "bytes": ARCHIVE_BYTES,
            "sha256": ARCHIVE_SHA256,
            "member_count": EXPECTED_MEMBER_COUNT,
            "uncompressed_bytes": EXPECTED_UNCOMPRESSED_BYTES,
            "compressed_member_bytes": EXPECTED_COMPRESSED_MEMBER_BYTES,
            "crc_status": "pass",
            "path_safety_status": "pass",
            "suffix_counts": {".npy": 2, ".rpt": 4_200},
        },
        "count_semantics": {
            "material_system_count": 1,
            "nominal_formulation_count": 1,
            "simulation_run_count": len(run_work),
            "unique_input_condition_count": len(condition_to_runs),
            "curve_point_count": sum(row["point_count"] for row in run_work),
            "curve_points_are_independent_samples": False,
            "unique_numerical_curve_count": len(numeric_hash_to_runs),
            "unique_raw_rpt_byte_stream_count": len(raw_hash_to_runs),
        },
        "conditions": {
            "age_days_range": [min(ages), max(ages)],
            "age_days_unique_count": len(set(ages)),
            "temperature_C_range": [min(temperatures), max(temperatures)],
            "temperature_C_unique_count": len(set(temperatures)),
            "direction_summary": direction_summary,
        },
        "duplicate_audit": {
            "condition_group_size_distribution": _histogram(condition_sizes),
            "condition_groups_with_multiple_runs": sum(
                size > 1 for size in condition_sizes
            ),
            "runs_in_repeated_condition_groups": sum(
                size for size in condition_sizes if size > 1
            ),
            "repeated_condition_excess_run_count": (
                len(run_work) - len(condition_to_runs)
            ),
            "condition_groups_with_multiple_numerical_curves": (
                condition_groups_with_multiple_curves
            ),
            "numerical_curve_group_size_distribution": _histogram(
                numeric_curve_sizes
            ),
            "numerical_curve_groups_with_multiple_runs": sum(
                size > 1 for size in numeric_curve_sizes
            ),
            "duplicate_numerical_curve_excess_run_count": (
                len(run_work) - len(numeric_hash_to_runs)
            ),
            "raw_rpt_group_size_distribution": _histogram(raw_curve_sizes),
            "raw_rpt_groups_with_multiple_runs": sum(
                size > 1 for size in raw_curve_sizes
            ),
            "duplicate_raw_rpt_excess_run_count": (
                len(run_work) - len(raw_hash_to_runs)
            ),
            "cross_condition_numerical_curve_groups": cross_condition_collisions,
            "raw_duplicate_groups": raw_duplicate_groups,
        },
        "curve_quality": {
            "point_count_per_run": EXPECTED_POINT_COUNT_PER_RUN,
            "all_stress_curves_monotonic_nondecreasing": True,
            "issue_run_count": len(issue_runs),
            "issue_runs": issue_runs,
        },
        "identity": {
            "global_structure_family_key": GLOBAL_STRUCTURE_FAMILY_KEY,
            "system_identity": SYSTEM_IDENTITY,
            "structure_identity_status": STRUCTURE_IDENTITY_STATUS,
            "exact_molecular_structure_resolved": False,
        },
        "training_governance": {
            "training_split_created": False,
            "training_weight_materialized": False,
            "canonical_condition_rule": "lowest_run_id_per_unique_direction_age_temperature",
        },
    }
    return {
        "summary": raw_summary,
        "files": file_rows,
        "runs": run_rows,
        "run_work": run_work,
        "condition_to_runs": condition_to_runs,
    }


def _gold_c_rows(raw: dict[str, Any]) -> list[dict[str, str]]:
    run_work: list[dict[str, Any]] = raw["run_work"]
    condition_to_runs: dict[tuple[int, int, int], list[int]] = raw[
        "condition_to_runs"
    ]
    curve_hash_to_conditions: dict[str, set[tuple[int, int, int]]] = defaultdict(set)
    for item in run_work:
        curve_hash_to_conditions[item["numerical_curve_sha256"]].add(item["condition"])
    cross_condition_curve_hashes = {
        curve_hash
        for curve_hash, conditions in curve_hash_to_conditions.items()
        if len(conditions) > 1
    }
    rows: list[dict[str, str]] = []
    for condition in sorted(condition_to_runs):
        run_id = min(condition_to_runs[condition])
        source = run_work[run_id]
        direction, age, temperature = condition
        simulation_key = _simulation_key(direction, age, temperature)
        cross_condition_collision = (
            source["numerical_curve_sha256"] in cross_condition_curve_hashes
        )
        conditional = bool(source["issue_notes"]) or cross_condition_collision
        admission = "conditional_reference" if conditional else "admitted_reference"
        if source["issue_notes"]:
            source_validation_status = (
                "official_cc_by_dataset_peer_reviewed_method_"
                "single_curve_missing_final_increment"
            )
        elif cross_condition_collision:
            source_validation_status = (
                "official_cc_by_dataset_peer_reviewed_method_"
                "cross_condition_identical_numerical_curve"
            )
        else:
            source_validation_status = (
                "official_cc_by_dataset_peer_reviewed_method_curve_passed"
            )
        for property_name, source_field, unit, unit_status in GOLD_PROPERTIES:
            value = float(source[source_field])
            if property_name == "maximum_observed_mises_stress":
                value /= 1_000_000
            source_record_id = f"mendeley:n9h66xjk7y:v1:run={run_id:04d}"
            rows.append(
                {
                    "source_id": SOURCE_ID,
                    "source_record_id": source_record_id,
                    "observation_id": f"{source_record_id}:{property_name}",
                    "canonical_structure": "",
                    "system_identity": SYSTEM_IDENTITY,
                    "structure_identity_status": STRUCTURE_IDENTITY_STATUS,
                    "global_structure_family_key": GLOBAL_STRUCTURE_FAMILY_KEY,
                    "simulation_key": simulation_key,
                    "split_group": GLOBAL_STRUCTURE_FAMILY_KEY,
                    "property_name": property_name,
                    "value": _finite_text(value),
                    "unit": unit,
                    "unit_status": unit_status,
                    "method_family": METHOD_FAMILY,
                    "method_detail": METHOD_DETAIL,
                    "fidelity_level": FIDELITY_LEVEL,
                    "temp": f"{temperature + 273.15:.2f}",
                    "press": "",
                    "gold_admission_status": admission,
                    "property_admission_status": admission,
                    "source_validation_status": source_validation_status,
                    "record_role": "simulation_curve_derived_scalar",
                    "potential_weight_ceiling": "0.10" if conditional else "0.20",
                    "current_weight_materialized": "false",
                    "training_weight": "",
                    "source_locator": (
                        f"{ARCHIVE_PATH.name}#{source['rpt_member_path']}"
                        f";direction={direction};age_days={age};"
                        f"temperature_C={temperature};canonical_run={run_id}"
                    ),
                    "citation_keys": CITATION_KEYS,
                }
            )

    if len(rows) != EXPECTED_GOLD_C_ROW_COUNT:
        raise AuditBlocked(
            f"Gold-C 行数漂移: expected={EXPECTED_GOLD_C_ROW_COUNT}, actual={len(rows)}"
        )
    if len({row["observation_id"] for row in rows}) != len(rows):
        raise AuditBlocked("Gold-C observation_id 不唯一")
    if any(tuple(row) != GOLD_C_VALUE_COLUMNS for row in rows):
        raise AuditBlocked("Gold-C 字段或字段顺序漂移")
    if any(row["training_weight"] for row in rows):
        raise AuditBlocked("本批次禁止提前物化实际训练权重")
    return rows


def _validate_frozen_semantics(
    raw: dict[str, Any], rows: list[dict[str, str]]
) -> None:
    summary = raw["summary"]
    counts = summary["count_semantics"]
    expected_counts = {
        "material_system_count": 1,
        "nominal_formulation_count": 1,
        "simulation_run_count": 4_200,
        "unique_input_condition_count": 3_868,
        "curve_point_count": 424_200,
        "curve_points_are_independent_samples": False,
        "unique_numerical_curve_count": 3_863,
        "unique_raw_rpt_byte_stream_count": 4_192,
    }
    if counts != expected_counts:
        raise AuditBlocked(f"计数语义漂移: {counts}")
    conditions = summary["conditions"]
    if conditions["age_days_range"] != [3, 2_999]:
        raise AuditBlocked("老化时间范围漂移")
    if conditions["age_days_unique_count"] != 1_629:
        raise AuditBlocked("老化时间唯一值数量漂移")
    if conditions["temperature_C_range"] != [10, 89]:
        raise AuditBlocked("温度范围漂移")
    if conditions["temperature_C_unique_count"] != 80:
        raise AuditBlocked("温度唯一值数量漂移")
    directions = conditions["direction_summary"]
    if directions["1"]["unique_condition_count"] != 1_939:
        raise AuditBlocked("方向 1 唯一工况数量漂移")
    if directions["3"]["unique_condition_count"] != 1_929:
        raise AuditBlocked("方向 3 唯一工况数量漂移")
    if directions["1"]["unique_numerical_curve_count"] != 1_939:
        raise AuditBlocked("方向 1 唯一曲线数量漂移")
    if directions["3"]["unique_numerical_curve_count"] != 1_924:
        raise AuditBlocked("方向 3 唯一曲线数量漂移")

    duplicate = summary["duplicate_audit"]
    if duplicate["condition_group_size_distribution"] != {
        "1": 3_580,
        "2": 248,
        "3": 36,
        "4": 4,
    }:
        raise AuditBlocked("输入工况重复分布漂移")
    if duplicate["numerical_curve_group_size_distribution"] != {
        "1": 3_575,
        "2": 247,
        "3": 36,
        "4": 4,
        "7": 1,
    }:
        raise AuditBlocked("数值曲线重复分布漂移")
    if duplicate["raw_rpt_group_size_distribution"] != {
        "1": 4_189,
        "2": 2,
        "7": 1,
    }:
        raise AuditBlocked("原始 RPT 重复分布漂移")
    collision_groups = duplicate["cross_condition_numerical_curve_groups"]
    if len(collision_groups) != 1:
        raise AuditBlocked("跨工况同曲线组数量漂移")
    collision = collision_groups[0]
    if collision["numerical_curve_sha256"] != (
        "7940fc69f0e93d8571d71ac0110e25aca641497b425a47c26ae93346961094d6"
    ) or collision["run_ids"] != [4059, 4093, 4094, 4138, 4144, 4157, 4192]:
        raise AuditBlocked("跨工况同曲线组身份漂移")
    issues = summary["curve_quality"]["issue_runs"]
    if len(issues) != 1 or issues[0]["run_id"] != 4_193:
        raise AuditBlocked("异常曲线清单漂移")

    admission_counts = Counter(row["gold_admission_status"] for row in rows)
    if admission_counts != {
        "admitted_reference": 19_305,
        "conditional_reference": 35,
    }:
        raise AuditBlocked(f"Gold-C 准入计数漂移: {admission_counts}")


@lru_cache(maxsize=1)
def _materialize() -> tuple[
    tuple[dict[str, str], ...],
    tuple[dict[str, str], ...],
    tuple[dict[str, str], ...],
    dict[str, Any],
]:
    raw = _raw_audit()
    rows = _gold_c_rows(raw)
    _validate_frozen_semantics(raw, rows)
    property_counts = Counter(row["property_name"] for row in rows)
    admission_counts = Counter(row["gold_admission_status"] for row in rows)
    ceiling_counts = Counter(row["potential_weight_ceiling"] for row in rows)
    summary = raw["summary"]
    summary["gold_c_materialization"] = {
        "canonical_unique_condition_count": EXPECTED_UNIQUE_CONDITION_COUNT,
        "scalar_row_count": len(rows),
        "property_counts": dict(sorted(property_counts.items())),
        "admission_counts": dict(sorted(admission_counts.items())),
        "potential_weight_ceiling_counts": dict(sorted(ceiling_counts.items())),
        "conditional_canonical_run_ids": [
            4_059,
            4_093,
            4_138,
            4_144,
            4_157,
            4_192,
            4_193,
        ],
        "simulation_key_count": len({row["simulation_key"] for row in rows}),
        "split_group_count": len({row["split_group"] for row in rows}),
        "actual_training_weight_materialized": False,
        "curve_point_rows_materialized": 0,
    }
    return (
        tuple(dict(row) for row in rows),
        tuple(dict(row) for row in raw["files"]),
        tuple(dict(row) for row in raw["runs"]),
        summary,
    )


def build_gold_c_rows() -> list[dict[str, str]]:
    """返回 19,340 条、与共享总账字段契约完全一致的 Gold-C 标量。"""

    rows, _, _, _ = _materialize()
    return [dict(row) for row in rows]


def audit() -> dict[str, Any]:
    """返回确定性、可 JSON 序列化的冻结审计摘要。"""

    _, _, _, summary = _materialize()
    return json.loads(json.dumps(summary, ensure_ascii=False))


def audit_archive() -> dict[str, Any]:
    """返回摘要及两张完整审计清单，供测试和人工复核。"""

    rows, files, runs, summary = _materialize()
    return {
        "summary": json.loads(json.dumps(summary, ensure_ascii=False)),
        "files": [dict(row) for row in files],
        "runs": [dict(row) for row in runs],
        "gold_c_rows": [dict(row) for row in rows],
    }


def _tsv_bytes(columns: tuple[str, ...], rows: Iterable[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=columns,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _source_readme(summary: dict[str, Any]) -> str:
    counts = summary["count_semantics"]
    materialization = summary["gold_c_materialization"]
    return f"""# 第十九批模拟数据：老化植物基聚氨酯泡沫

## 数据身份与许可

- 数据集：*Simulated Results for Aged Polyurethane Foam*，Mendeley Data v1；
- DOI：<https://doi.org/{DATASET_DOI}>；
- 官方页面：<{DATASET_URL}>；
- 官方下载接口：<{DOWNLOAD_URL}>；
- 发布日期：2024-03-20；贡献者：Enio Henrique Pires Da Silva；
- 许可：**{LICENSE}**；复用时必须保留署名和 DOI；
- 固定 ZIP：`{ARCHIVE_PATH.name}`，{ARCHIVE_BYTES:,} bytes；
- SHA-256：`{ARCHIVE_SHA256}`。

## 数据到底代表什么

归档只对应 **{counts['material_system_count']} 个名义材料体系、
{counts['nominal_formulation_count']} 个名义配方**，不是 4,200 个新材料。
它包含 {counts['simulation_run_count']:,} 次 Abaqus/UMAT 模拟：运行 0--2099
为方向 1（垂直发泡方向），2100--4199 为方向 3（平行发泡方向）。每次运行
由方向、老化时间和温度定义；去除重复输入后有
{counts['unique_input_condition_count']:,} 个唯一工况。

每条 RPT 有 101 个应力--对数应变点，共
{counts['curve_point_count']:,} 点。这些点是曲线内部的相关观测，**不能当作
424,200 个独立材料样本**。`模拟运行清单.tsv` 保留全部 4,200 次运行及重复
关系；`Gold_C_紧凑标量表.tsv` 只对每个唯一工况保留最低运行编号，并物化
0.1、0.5、1.0 压缩对数应变处的 Mises 应力、最大观测应力和曲线积分能量
密度，共 {materialization['scalar_row_count']:,} 条标量。

重复审计发现 332 个重复工况超额运行；相同输入工况的解析数值曲线完全一致。
另有一组 7 条数值完全相同的曲线跨越 6 个不同工况，保留为不同输入但已明确
标记，对应标量降为条件参考。运行 4193 的最后一行重复 0.99 步、缺少 1.00
步；其 0.1、0.5、1.0 应变标量仍在已有范围内，因此也只作为条件参考。其余
{materialization['admission_counts']['admitted_reference']:,} 条为正式参考，
{materialization['admission_counts']['conditional_reference']:,} 条为条件参考。
这里只记录潜在权重上限，实际训练权重和数据划分均为空。

## 化学身份边界

配方信息来自配套实验数据论文：商业 Kehl IC200 MDI 与 KT1106-R 植物油
多元醇共混物（以蓖麻油为主）按 1:1 质量比反应。公开资料没有披露商业组分
的完整分子组成，因此 `canonical_structure` 留空；本数据适合作为同一材料族
的老化--方向--温度多保真参考，不能冒充已解析 SMILES 的单体级记录。

## 文件说明

- `n9h66xjk7y-1.zip`：固定公开原件，不在仓库内解包；
- `文件校验清单.tsv`：4,202 个 ZIP 成员的大小、CRC32 和 SHA-256；
- `模拟运行清单.tsv`：4,200 次模拟的工况、曲线哈希、重复组和质量标志；
- `Gold_C_紧凑标量表.tsv`：3,868 个唯一工况 × 5 个可复算标量；
- `内容审计摘要.json`：冻结计数语义、重复审计和准入统计。

## 参考文献

[1] Da Silva, E. H. P. (2024). *Simulated Results for Aged Polyurethane
Foam* (Version 1) [Data set]. Mendeley Data.
<https://doi.org/{DATASET_DOI}>.

[2] Pires, Ê. H.; de Barros, S.; Casari, P.; Ribeiro, M. L. (2025). Data
generation and deep neural network predictions for aged mechanical properties.
*Polymer Engineering & Science*, 65(6), 3029--3045.
<https://doi.org/{ARTICLE_DOI}>.

[3] Da Silva, E. H. P.; De Barros, S.; Casari, P.; Ribeiro, M. L. (2024).
Aging properties of a vegetable-based polyurethane foam under high relative
humidity and different temperatures. *Polymer Engineering & Science*, 64(6),
2778--2794. <https://doi.org/{AGING_ARTICLE_DOI}>.

[4] Da Silva, E. H. P.; De Barros, S.; Casari, P.; Ribeiro, M. L. (2024).
Raw dataset of compression tests on a vegetable oil-based polyurethane foam
exposed to different ageing conditions. *Data in Brief*, 53, 110199.
<https://doi.org/{DATA_IN_BRIEF_DOI}>. 配套实验数据：
<https://doi.org/{EXPERIMENT_DATA_DOI}>.
"""


def write_outputs() -> dict[str, Any]:
    """原子写出审计摘要、清单、Gold-C 标量和来源说明。"""

    rows, files, runs, summary = _materialize()
    _atomic_write(
        OUTPUT_AUDIT,
        (
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )
    _atomic_write(OUTPUT_CHECKSUMS, _tsv_bytes(FILE_COLUMNS, files))
    _atomic_write(OUTPUT_RUNS, _tsv_bytes(RUN_COLUMNS, runs))
    _atomic_write(OUTPUT_GOLD_C, _tsv_bytes(GOLD_C_VALUE_COLUMNS, rows))
    _atomic_write(OUTPUT_README, _source_readme(summary).encode("utf-8"))
    return audit()


def main() -> None:
    summary = write_outputs()
    print(
        json.dumps(
            {
                "archive_sha256": summary["archive"]["sha256"],
                "simulation_run_count": summary["count_semantics"][
                    "simulation_run_count"
                ],
                "unique_input_condition_count": summary["count_semantics"][
                    "unique_input_condition_count"
                ],
                "gold_c_scalar_row_count": summary["gold_c_materialization"][
                    "scalar_row_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
