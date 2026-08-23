"""只读、确定性复算 QUB 核心来源与 TPU95A 重复下载镜像。

本脚本只读取两个新增开放数据来源及既有 ``TPU95A_2026`` 镜像。唯一允许
写入的文件是来源目录内已经采用的以下五个审计产物：

* QUB：``内容审计摘要.json``、``文件校验清单.tsv``；
* TPU95A：``内容审计摘要.json``、``文件校验清单.tsv``、
  ``曲线解析清单.tsv``。

运行时不访问网络、不创建训练集、不修改 ZIP、只读解包、CSV、JPG、官方清单
或既有镜像。输出使用固定审计基准日、项目相对路径、同目录普通临时文件、
``flush + fsync`` 与 ``os.replace``，相同输入可得到字节一致的 JSON/TSV。
"""

from __future__ import annotations

import argparse
import binascii
import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import statistics
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
PROJECT_ROOT = SCRIPT_PATH.parents[2]
OPEN_DATA_ROOT = PROJECT_ROOT / "数据/原始" / "外部数据" / "新增开放数据"

QUB_DIR = OPEN_DATA_ROOT / "QUB_生物基三重自修复TPU"
QUB_ZIP = QUB_DIR / "MA_d4ma00289j_dataset.zip"
QUB_DATACITE = QUB_DIR / "官方DataCite元数据.json"
QUB_UNPACKED = QUB_DIR / "解压数据_只读"

TPU95A_DIR = OPEN_DATA_ROOT / "Mendeley_TPU95A_TPMS应变率力学"
TPU95A_PRIOR_DIR = (
    PROJECT_ROOT / "数据/原始" / "外部数据" / "力学曲线" / "TPU95A_2026"
)
TPU95A_DATACITE = TPU95A_DIR / "官方DataCite元数据.json"
TPU95A_FEA_MANIFEST = TPU95A_DIR / "官方FEA文件清单_仅登记未下载.json"

# 审计日期是协议常量，不读取运行时时钟。
AUDIT_BASELINE_DATE = "2026-07-20"
QUB_DOI = "10.17034/83fdb865-0ead-4c8b-81d2-59265a8810f3"
TPU95A_DOI = "10.17632/mc6zh4cwhf.2"
QUB_DEFAULT_LEAKAGE_KEY = "dataset_doi|formulation"

QUB_OUTPUT_NAMES = frozenset({"内容审计摘要.json", "文件校验清单.tsv"})
TPU95A_OUTPUT_NAMES = frozenset(
    {"内容审计摘要.json", "文件校验清单.tsv", "曲线解析清单.tsv"}
)
OUTPUT_WHITELIST = frozenset(
    {QUB_DIR / name for name in QUB_OUTPUT_NAMES}
    | {TPU95A_DIR / name for name in TPU95A_OUTPUT_NAMES}
)

EXPECTED_QUB_COUNTS = {
    "formulation_count": 4,
    "raw_curve_instance_count": 79,
    "deduplicated_curve_count": 68,
    "core_bulk_tensile_curve_count": 41,
    "core_formulation_condition_group_count": 12,
    "raw_point_count": 234_822,
    "deduplicated_point_count": 224_733,
    "cross_file_duplicate_instance_count": 11,
    "partial_xy_row_count": 115,
    "finite_numeric_cell_count": 469_848,
}

EXPECTED_TPU95A_COUNTS = {
    "downloaded_experimental_asset_count": 14,
    "downloaded_csv_curve_count": 12,
    "downloaded_supporting_jpg_count": 2,
    "prior_directory_csv_count": 12,
    "mirror_hash_match_count": 12,
    "raw_point_count": 47_065,
    "manifest_only_fea_count": 9,
    "incremental_scientific_sample_count": 0,
}


class AuditBlocked(RuntimeError):
    """输入、路径或科学计数不满足失败关闭条件。"""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    """识别符号链接、Windows junction 和其他重解析点。"""

    if path.is_symlink():
        return True
    if hasattr(path, "is_junction") and path.is_junction():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _assert_plain_ancestors(path: Path) -> None:
    """拒绝目标及现有祖先中的符号链接/重解析点。"""

    candidates = [path, *path.parents]
    for candidate in candidates:
        if candidate.is_symlink() or (
            candidate.exists() and _is_reparse_point(candidate)
        ):
            raise AuditBlocked(f"拒绝符号链接或重解析点: {candidate}")


def assert_output_allowed(path: Path) -> Path:
    """确认输出位于显式白名单，且目标和目录均为普通路径。"""

    candidate = Path(path)
    if candidate not in OUTPUT_WHITELIST:
        raise AuditBlocked(f"拒绝写入白名单外路径: {candidate}")
    # 保持本函数可由公共安全测试连同 AuditBlocked/atomic_write 单独抽取执行。
    def is_reparse(current: Path) -> bool:
        if current.is_symlink():
            return True
        if hasattr(current, "is_junction") and current.is_junction():
            return True
        attributes = getattr(current.lstat(), "st_file_attributes", 0)
        return bool(attributes & 0x400)  # Windows FILE_ATTRIBUTE_REPARSE_POINT

    for current in [candidate, *candidate.parents]:
        if current.is_symlink() or (current.exists() and is_reparse(current)):
            raise AuditBlocked(f"拒绝符号链接或重解析点: {current}")
    parent = candidate.parent.resolve(strict=True)
    if not parent.is_dir():
        raise AuditBlocked(f"输出父目录不是普通目录: {candidate.parent}")
    if candidate.exists():
        if not candidate.is_file():
            raise AuditBlocked(f"输出目标不是普通文件: {candidate}")
        resolved = candidate.resolve(strict=True)
        if resolved.parent != parent:
            raise AuditBlocked(f"输出目标解析后越界: {candidate}")
    return candidate


def atomic_write(path: Path, payload: bytes) -> None:
    """用同目录临时普通文件、fsync 和原子替换提交审计产物。"""

    target = assert_output_allowed(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".audit.tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        if (
            temporary.is_symlink()
            or (hasattr(temporary, "is_junction") and temporary.is_junction())
            or not temporary.is_file()
        ):
            raise AuditBlocked(f"临时审计文件不是普通文件: {temporary}")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        assert_output_allowed(target)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _tsv_bytes(columns: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(
        handle,
        fieldnames=columns,
        delimiter="\t",
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _clean(row.get(column, "")) for column in columns})
    return handle.getvalue().encode("utf-8")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _hash_stream(handle: Any, chunk_size: int = 1024 * 1024) -> tuple[str, str]:
    digest = hashlib.sha256()
    crc = 0
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            break
        digest.update(chunk)
        crc = binascii.crc32(chunk, crc)
    return digest.hexdigest(), f"{crc & 0xFFFFFFFF:08x}"


def _input_file(path: Path) -> Path:
    if not path.is_file():
        raise AuditBlocked(f"缺少科学输入文件: {path}")
    _assert_plain_ancestors(path)
    resolved_project = PROJECT_ROOT.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not _is_relative_to(resolved, resolved_project):
        raise AuditBlocked(f"科学输入越出项目根目录: {path}")
    return path


def project_relative(path: Path) -> str:
    resolved_project = PROJECT_ROOT.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not _is_relative_to(resolved, resolved_project):
        raise AuditBlocked(f"路径越出项目根目录: {path}")
    return resolved.relative_to(resolved_project).as_posix()


def _scientific_input_paths() -> list[Path]:
    paths: set[Path] = set()
    for root in (QUB_DIR, TPU95A_DIR):
        if not root.is_dir():
            raise AuditBlocked(f"缺少来源目录: {root}")
        _assert_plain_ancestors(root)
        for path in root.rglob("*"):
            if path.is_file() and path not in OUTPUT_WHITELIST:
                paths.add(_input_file(path))
    if not TPU95A_PRIOR_DIR.is_dir():
        raise AuditBlocked(f"缺少既有TPU95A镜像目录: {TPU95A_PRIOR_DIR}")
    _assert_plain_ancestors(TPU95A_PRIOR_DIR)
    for path in TPU95A_PRIOR_DIR.rglob("*"):
        if path.is_file():
            paths.add(_input_file(path))
    return sorted(paths, key=project_relative)


def capture_input_snapshot() -> dict[str, dict[str, Any]]:
    return {
        project_relative(path): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in _scientific_input_paths()
    }


def input_snapshot_sha256(snapshot: dict[str, dict[str, Any]]) -> str:
    canonical = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _is_read_only(path: Path) -> bool:
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    readonly_flag = getattr(stat, "FILE_ATTRIBUTE_READONLY", 0)
    if readonly_flag:
        return bool(attributes & readonly_flag)
    return not bool(path.stat().st_mode & stat.S_IWUSR)


def _safe_zip_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    return (
        not pure.is_absolute()
        and ".." not in pure.parts
        and not re.match(r"^[A-Za-z]:", normalized)
    )


def _decode_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    try:
        return "utf-8-sig", raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "cp1252", raw.decode("cp1252")


def _number(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _number_token(value: float) -> str:
    return format(value, ".17g")


@dataclass(frozen=True)
class QubCurveIdentity:
    label: str
    formulation: str
    modality: str
    specimen_id: str | None
    condition: str | None
    grade: str
    eligibility: str


@dataclass(frozen=True)
class QubCurveFile:
    suffix: str
    header_row: int
    data_start: int
    x_header_token: str
    curves: tuple[QubCurveIdentity, ...]


def _curve(
    label: str,
    formulation: str,
    modality: str,
    specimen_id: str | None,
    condition: str | None,
    grade: str,
    eligibility: str,
) -> QubCurveIdentity:
    return QubCurveIdentity(
        label,
        formulation,
        modality,
        specimen_id,
        condition,
        grade,
        eligibility,
    )


def _bulk_replicates(
    formulation: str, condition: str, count: int, label_condition: str
) -> tuple[QubCurveIdentity, ...]:
    return tuple(
        _curve(
            f"{formulation}-{label_condition}-{index}",
            formulation,
            "bulk_tensile",
            f"{formulation}-{label_condition}-{index}",
            condition,
            "金",
            "main_curve",
        )
        for index in range(1, count + 1)
    )


QUB_PREFIX = "MA d4ma00289j dataset_"
QUB_CURVE_FILES = (
    QubCurveFile(
        "Figure 10B.csv",
        0,
        1,
        "Strain",
        (
            _curve(
                "P40-silver-circuit-strain-resistance",
                "P40",
                "electromechanical",
                "P40-printed-circuit-1",
                "strain_to_300pct",
                "银",
                "application_auxiliary",
            ),
        ),
    ),
    QubCurveFile(
        "Figure 12B.csv",
        1,
        2,
        "Strain",
        tuple(
            _curve(
                f"P40-fibre-{index}",
                "P40",
                "fibre_tensile",
                f"P40-fibre-{index}",
                "undamaged_fibre",
                "银",
                "application_auxiliary",
            )
            for index in range(1, 4)
        ),
    ),
    QubCurveFile(
        "Figure 1A.csv",
        1,
        2,
        "Wavenumber",
        tuple(
            _curve(f"{formulation}-FTIR", formulation, "FTIR", None, None, "银", "auxiliary")
            for formulation in ("P35", "P40", "P45")
        ),
    ),
    QubCurveFile(
        "Figure 1B.csv",
        1,
        2,
        "logM",
        tuple(
            _curve(
                f"{formulation}-GPC",
                formulation,
                "GPC_distribution",
                None,
                None,
                "银",
                "auxiliary",
            )
            for formulation in ("P35", "P40", "P45")
        ),
    ),
    QubCurveFile(
        "Figure 2A.csv",
        1,
        2,
        "Chemical Shift",
        (_curve("P40-1H-NMR", "P40", "1H_NMR", None, None, "银", "auxiliary"),),
    ),
    QubCurveFile(
        "Figure 2B.csv",
        1,
        2,
        "Chemical Shift",
        (_curve("P40-13C-NMR", "P40", "13C_NMR", None, None, "银", "auxiliary"),),
    ),
    QubCurveFile(
        "Figure 3A.csv",
        1,
        2,
        "Temperature",
        tuple(
            _curve(f"{formulation}-DSC", formulation, "DSC", None, None, "银", "auxiliary")
            for formulation in ("P35", "P40", "P45")
        ),
    ),
    QubCurveFile(
        "Figure 3B.csv",
        1,
        2,
        "Temperature",
        tuple(
            _curve(f"{formulation}-TGA", formulation, "TGA", None, None, "银", "auxiliary")
            for formulation in ("P35", "P40", "P45")
        ),
    ),
    QubCurveFile(
        "Figure 3C.csv",
        1,
        2,
        "Temperature",
        tuple(
            _curve(f"{formulation}-DTG", formulation, "DTG", None, None, "银", "auxiliary")
            for formulation in ("P35", "P40", "P45")
        ),
    ),
    QubCurveFile(
        "Figure 4A.csv",
        1,
        2,
        "Strain",
        _bulk_replicates("P35", "undamaged", 4, "undamaged")
        + _bulk_replicates("P40", "undamaged", 4, "undamaged")
        + _bulk_replicates("P45", "undamaged", 3, "undamaged"),
    ),
    QubCurveFile(
        "Figure 4B.csv",
        1,
        2,
        "Strain",
        tuple(
            _curve(
                f"P40-cyclic-sequence-cycle-{index}",
                "P40",
                "cyclic_tensile",
                "P40-cyclic-sequence-1",
                f"cycle_{index}",
                "银",
                "auxiliary_dependent",
            )
            for index in range(1, 7)
        ),
    ),
    QubCurveFile(
        "Figure 6A.csv",
        1,
        2,
        "Strain",
        _bulk_replicates("P35", "undamaged", 4, "undamaged")
        + _bulk_replicates("P35", "heal_1h_ambient", 3, "heal-1h")
        + _bulk_replicates("P35", "heal_3h_ambient", 3, "heal-3h"),
    ),
    QubCurveFile(
        "Figure 6B.csv",
        1,
        2,
        "Strain",
        _bulk_replicates("P40", "undamaged", 4, "undamaged")
        + _bulk_replicates("P40", "heal_20min_ambient", 3, "heal-20min")
        + _bulk_replicates("P40", "heal_1h_ambient", 4, "heal-1h")
        + _bulk_replicates("P40", "heal_3h_ambient", 4, "heal-3h"),
    ),
    QubCurveFile(
        "Figure 6C.csv",
        1,
        2,
        "Strain",
        _bulk_replicates("P45", "undamaged", 3, "undamaged")
        + _bulk_replicates("P45", "heal_1h_ambient", 3, "heal-1h")
        + _bulk_replicates("P45", "heal_3h_ambient", 4, "heal-3h"),
    ),
    QubCurveFile(
        "Figure 7.csv",
        1,
        2,
        "Strain",
        _bulk_replicates("P40-HDO", "undamaged", 3, "undamaged")
        + _bulk_replicates("P40-HDO", "heal_3h_ambient", 3, "heal-3h"),
    ),
)


QUB_FILE_SEMANTICS: dict[str, tuple[str, str, str, str]] = {
    "Cover page.csv": ("文档封面", "隔离", "documentation", "非数值训练数据。"),
    "Figure 10B.csv": ("印刷电路应变-电阻曲线", "银", "application_auxiliary", "单条P40/银墨电路曲线。"),
    "Figure 12B.csv": ("纺丝纤维拉伸曲线", "银", "application_auxiliary", "CSV仅3条曲线，论文方法称5个试样。"),
    "Figure 1A.csv": ("FTIR原始曲线", "银", "auxiliary", "P40/P45透过率为作图整体上移。"),
    "Figure 1B.csv": ("GPC分布曲线", "银", "auxiliary", "未直接提供Mn/Mw标量。"),
    "Figure 2A.csv": ("1H NMR曲线", "银", "auxiliary", "仅P40单次谱图。"),
    "Figure 2B.csv": ("13C NMR曲线", "银", "auxiliary", "点数不是独立样本数。"),
    "Figure 3A.csv": ("DSC曲线", "银", "auxiliary", "三条曲线末端存在93个半对XY行。"),
    "Figure 3B.csv": ("TGA曲线", "银", "auxiliary", "与DTG同源，拆分时必须同组。"),
    "Figure 3C.csv": ("DTG曲线", "银", "auxiliary", "TGA导数曲线，不是独立实验。"),
    "Figure 4A.csv": ("本体拉伸原始曲线", "金", "main_curve", "11个明确编号试样。"),
    "Figure 4B.csv": ("循环拉伸曲线", "银", "auxiliary_dependent", "同一P40试样的6个依赖循环。"),
    "Figure 6A.csv": ("P35自愈拉伸曲线", "金/重复", "main_curve", "4条未损伤曲线与Figure 4A重复。"),
    "Figure 6B.csv": ("P40自愈拉伸曲线", "金/重复", "main_curve", "4条未损伤曲线与Figure 4A重复。"),
    "Figure 6C.csv": ("P45自愈拉伸曲线", "金/重复", "main_curve", "3条未损伤曲线与Figure 4A重复。"),
    "Figure 6D.csv": ("自愈效率汇总", "银", "aggregate_target", "14个均值标签及14个标准差。"),
    "Figure 7.csv": ("P40-HDO对照拉伸曲线", "金", "main_curve", "6个明确试样。"),
    "Figure 8.csv": ("接触角", "金/银", "scalar_auxiliary", "9个原始测点；末两行是无标签汇总。"),
    "README.txt": ("数据说明", "文档", "documentation", "许可和数据集DOI以DataCite为准。"),
}


def _qub_basename(suffix: str) -> str:
    if suffix == "README.txt":
        return suffix
    return QUB_PREFIX + suffix


def _curve_statistics(
    rows: list[list[str]], data_start: int, x_column: int, y_column: int
) -> dict[str, Any]:
    points = 0
    x_only = 0
    y_only = 0
    nonnumeric_pair_rows = 0
    distinct: set[tuple[float, float]] = set()
    previous: tuple[float, float] | None = None
    consecutive_duplicates = 0
    x_decrease_steps = 0
    x_equal_steps = 0
    min_x: float | None = None
    max_x: float | None = None
    min_y: float | None = None
    max_y: float | None = None
    last: tuple[float, float] | None = None
    digest = hashlib.sha256()

    for row in rows[data_start:]:
        raw_x = row[x_column] if x_column < len(row) else ""
        raw_y = row[y_column] if y_column < len(row) else ""
        x = _number(raw_x)
        y = _number(raw_y)
        if x is not None and y is not None:
            pair = (x, y)
            points += 1
            distinct.add(pair)
            if previous is not None:
                if pair == previous:
                    consecutive_duplicates += 1
                if x < previous[0]:
                    x_decrease_steps += 1
                elif x == previous[0]:
                    x_equal_steps += 1
            previous = pair
            last = pair
            min_x = x if min_x is None else min(min_x, x)
            max_x = x if max_x is None else max(max_x, x)
            min_y = y if min_y is None else min(min_y, y)
            max_y = y if max_y is None else max(max_y, y)
            digest.update(f"{_number_token(x)}\t{_number_token(y)}\n".encode("ascii"))
        elif x is not None:
            x_only += 1
        elif y is not None:
            y_only += 1
        elif _clean(raw_x) or _clean(raw_y):
            nonnumeric_pair_rows += 1

    if not points:
        raise AuditBlocked("识别到零点曲线，拒绝生成空科学记录")
    return {
        "point_rows": points,
        "distinct_xy_values": len(distinct),
        "exact_duplicate_xy_rows": points - len(distinct),
        "consecutive_duplicate_xy_rows": consecutive_duplicates,
        "x_only_rows": x_only,
        "y_only_rows": y_only,
        "nonnumeric_pair_rows": nonnumeric_pair_rows,
        "x_min": min_x,
        "x_max": max_x,
        "y_min": min_y,
        "y_max": max_y,
        "last_x": last[0] if last else None,
        "last_y": last[1] if last else None,
        "x_decrease_steps": x_decrease_steps,
        "x_equal_steps": x_equal_steps,
        "curve_content_sha256": digest.hexdigest().upper(),
    }


def _read_csv_rows(path: Path) -> tuple[str, list[list[str]]]:
    encoding, text = _decode_text(path)
    return encoding, list(csv.reader(io.StringIO(text, newline="")))


def audit_qub_zip() -> dict[str, Any]:
    _input_file(QUB_ZIP)
    _input_file(QUB_DATACITE)
    if not QUB_UNPACKED.is_dir():
        raise AuditBlocked(f"缺少QUB只读解包目录: {QUB_UNPACKED}")
    _assert_plain_ancestors(QUB_UNPACKED)

    records: list[dict[str, Any]] = []
    with zipfile.ZipFile(QUB_ZIP, "r") as archive:
        infos = archive.infolist()
        normalized_names = [info.filename.replace("\\", "/") for info in infos]
        if len(normalized_names) != len(set(normalized_names)):
            raise AuditBlocked("QUB ZIP含重复成员名")
        if any(not _safe_zip_member(name) for name in normalized_names):
            raise AuditBlocked("QUB ZIP含路径穿越或绝对路径成员")
        bad_member = archive.testzip()
        if bad_member is not None:
            raise AuditBlocked(f"QUB ZIP CRC失败: {bad_member}")

        for info in infos:
            normalized = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            extracted = QUB_UNPACKED.joinpath(*PurePosixPath(normalized).parts)
            _input_file(extracted)
            with archive.open(info, "r") as member:
                member_sha, actual_crc = _hash_stream(member)
            extracted_sha = sha256_file(extracted)
            size_match = extracted.stat().st_size == info.file_size
            hash_match = extracted_sha == member_sha
            crc_match = actual_crc == f"{info.CRC:08x}"
            if not (size_match and hash_match and crc_match):
                raise AuditBlocked(f"QUB ZIP成员与只读解包不一致: {normalized}")
            records.append(
                {
                    "zip_member_path": normalized,
                    "zip_member_bytes": info.file_size,
                    "zip_member_compressed_bytes": info.compress_size,
                    "zip_member_crc32_declared": f"{info.CRC:08X}",
                    "zip_member_crc32_actual": actual_crc.upper(),
                    "zip_member_sha256": member_sha.upper(),
                    "extracted_relative_path": project_relative(extracted),
                    "extracted_bytes": extracted.stat().st_size,
                    "extracted_sha256": extracted_sha.upper(),
                    "extracted_read_only": _is_read_only(extracted),
                    "byte_identical": True,
                }
            )

    extracted_files = sorted(
        (path for path in QUB_UNPACKED.rglob("*") if path.is_file()),
        key=project_relative,
    )
    if len(records) != 19 or len(extracted_files) != 19:
        raise AuditBlocked(
            f"QUB文件数异常: ZIP文件成员={len(records)}, 解包文件={len(extracted_files)}"
        )
    if not all(record["extracted_read_only"] for record in records):
        raise AuditBlocked("QUB只读解包中存在未设置只读属性的文件")

    return {
        "zip_bytes": QUB_ZIP.stat().st_size,
        "zip_sha256": sha256_file(QUB_ZIP).upper(),
        "zip_crc_full_test": "通过",
        "zip_member_count_including_directories": len(infos),
        "zip_file_count": len(records),
        "zip_uncompressed_bytes": sum(record["zip_member_bytes"] for record in records),
        "extracted_file_count": len(extracted_files),
        "all_extracted_files_read_only": True,
        "all_zip_members_match_extracted_files": True,
        "members": records,
    }


def audit_qub_curves() -> tuple[list[dict[str, Any]], dict[Path, dict[str, Any]]]:
    dataset_directory = QUB_UNPACKED / "MA d4ma00289j dataset"
    curves: list[dict[str, Any]] = []
    csv_details: dict[Path, dict[str, Any]] = {}

    for config in QUB_CURVE_FILES:
        path = dataset_directory / _qub_basename(config.suffix)
        _input_file(path)
        encoding, rows = _read_csv_rows(path)
        if config.header_row >= len(rows):
            raise AuditBlocked(f"QUB CSV缺少表头: {project_relative(path)}")
        header = rows[config.header_row]
        x_columns = [
            index
            for index, value in enumerate(header)
            if _clean(value).casefold().startswith(config.x_header_token.casefold())
        ]
        if len(x_columns) != len(config.curves):
            raise AuditBlocked(
                f"QUB曲线列组数异常: {path.name}; "
                f"期望={len(config.curves)}, 实际={len(x_columns)}"
            )
        max_columns = max((len(row) for row in rows), default=0)
        for identity, x_column in zip(config.curves, x_columns, strict=True):
            statistics_row = _curve_statistics(
                rows, config.data_start, x_column, x_column + 1
            )
            curves.append(
                {
                    "file_name": path.name,
                    "relative_path": project_relative(path),
                    "label": identity.label,
                    "formulation": identity.formulation,
                    "modality": identity.modality,
                    "specimen_id": identity.specimen_id,
                    "condition": identity.condition,
                    "grade": identity.grade,
                    "eligibility": identity.eligibility,
                    "source_max_columns": max_columns,
                    "leakage_group": f"{QUB_DOI}|{identity.formulation}",
                    **statistics_row,
                }
            )
        csv_details[path] = {"encoding": encoding, "rows": rows}

    first_by_hash: dict[str, dict[str, Any]] = {}
    for row in curves:
        digest = row["curve_content_sha256"]
        first = first_by_hash.get(digest)
        if first is None:
            first_by_hash[digest] = row
            row["cross_file_duplicate_of"] = None
            row["effective_grade"] = row["grade"]
            row["effective_eligibility"] = row["eligibility"]
        else:
            row["cross_file_duplicate_of"] = {
                "relative_path": first["relative_path"],
                "label": first["label"],
            }
            row["effective_grade"] = "重复排除"
            row["effective_eligibility"] = "exclude_duplicate"

    return curves, csv_details


def _qub_file_statistics(
    path: Path,
    encoding: str,
    rows: list[list[str]],
    curves: list[dict[str, Any]],
) -> dict[str, Any]:
    max_columns = max((len(row) for row in rows), default=0)
    finite_numeric_cells = sum(
        _number(value) is not None for row in rows for value in row
    )
    nonempty_cells = sum(bool(_clean(value)) for row in rows for value in row)
    padded_cell_count = len(rows) * max_columns
    exact_duplicate_rows = sum(
        count - 1 for count in Counter(tuple(row) for row in rows).values() if count > 1
    )
    related = [row for row in curves if row["relative_path"] == project_relative(path)]
    return {
        "encoding": encoding,
        "row_count": len(rows),
        "max_column_count": max_columns,
        "nonempty_cell_count": nonempty_cells,
        "finite_numeric_cell_count": finite_numeric_cells,
        "blank_cells_within_rectangle": padded_cell_count - nonempty_cells,
        "exact_duplicate_rows_beyond_first": exact_duplicate_rows,
        "curve_instance_count": len(related),
        "complete_xy_row_count": sum(row["point_rows"] for row in related),
        "partial_xy_row_count": sum(
            row["x_only_rows"] + row["y_only_rows"] for row in related
        ),
    }


QUB_FILE_COLUMNS = [
    "相对路径",
    "角色",
    "字节数",
    "SHA256",
    "编码",
    "行数",
    "最大列数",
    "有限数值单元格",
    "完整XY点行",
    "半对XY行",
    "曲线实例",
    "只读",
    "CRC",
    "分级",
    "模型资格",
    "ZIP成员SHA256",
    "ZIP解包匹配",
    "备注",
]


def build_qub_outputs(
    input_snapshot_digest: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    zip_audit = audit_qub_zip()
    curves, parsed_csv = audit_qub_curves()
    dataset_directory = QUB_UNPACKED / "MA d4ma00289j dataset"
    member_by_extracted = {
        member["extracted_relative_path"]: member for member in zip_audit["members"]
    }

    file_rows: list[dict[str, Any]] = [
        {
            "相对路径": project_relative(QUB_ZIP),
            "角色": "官方压缩包",
            "字节数": QUB_ZIP.stat().st_size,
            "SHA256": zip_audit["zip_sha256"],
            "编码": "ZIP",
            "只读": _is_read_only(QUB_ZIP),
            "CRC": "通过",
            "分级": "原始证据",
            "模型资格": "source_archive",
            "备注": "20个ZIP成员（含1个目录），19个文件。",
        },
        {
            "相对路径": project_relative(QUB_DATACITE),
            "角色": "官方DataCite元数据",
            "字节数": QUB_DATACITE.stat().st_size,
            "SHA256": sha256_file(QUB_DATACITE).upper(),
            "编码": "UTF-8 JSON",
            "行数": len(QUB_DATACITE.read_text(encoding="utf-8").splitlines()),
            "只读": _is_read_only(QUB_DATACITE),
            "分级": "原始证据",
            "模型资格": "documentation",
            "备注": "许可和数据集DOI的权威来源。",
        },
    ]

    detailed_files: list[dict[str, Any]] = []
    all_source_files = sorted(
        (path for path in dataset_directory.iterdir() if path.is_file()),
        key=lambda path: path.name,
    )
    if len(all_source_files) != 19:
        raise AuditBlocked(f"QUB解包根文件数异常: {len(all_source_files)}")

    for path in all_source_files:
        suffix = path.name.removeprefix(QUB_PREFIX)
        if path.name == "README.txt":
            suffix = path.name
        if suffix not in QUB_FILE_SEMANTICS:
            raise AuditBlocked(f"QUB出现未登记解包文件: {path.name}")
        role, grade, eligibility, note = QUB_FILE_SEMANTICS[suffix]
        if path.suffix.casefold() == ".csv":
            if path in parsed_csv:
                encoding = parsed_csv[path]["encoding"]
                rows = parsed_csv[path]["rows"]
            else:
                encoding, rows = _read_csv_rows(path)
            stats_row = _qub_file_statistics(path, encoding, rows, curves)
        else:
            encoding, text = _decode_text(path)
            stats_row = {
                "encoding": encoding,
                "row_count": len(text.splitlines()),
                "max_column_count": None,
                "nonempty_cell_count": None,
                "finite_numeric_cell_count": None,
                "blank_cells_within_rectangle": None,
                "exact_duplicate_rows_beyond_first": None,
                "curve_instance_count": 0,
                "complete_xy_row_count": 0,
                "partial_xy_row_count": 0,
            }
        relative = project_relative(path)
        member = member_by_extracted.get(relative)
        if member is None:
            raise AuditBlocked(f"QUB解包文件不在ZIP成员映射中: {relative}")
        detailed = {
            "relative_path": relative,
            "file_name": path.name,
            "role": role,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path).upper(),
            "read_only": _is_read_only(path),
            "grade": grade,
            "eligibility": eligibility,
            "note": note,
            "zip_member_sha256": member["zip_member_sha256"],
            "zip_extracted_match": member["byte_identical"],
            **stats_row,
        }
        detailed_files.append(detailed)
        file_rows.append(
            {
                "相对路径": relative,
                "角色": role,
                "字节数": detailed["bytes"],
                "SHA256": detailed["sha256"],
                "编码": detailed["encoding"],
                "行数": detailed["row_count"],
                "最大列数": detailed["max_column_count"],
                "有限数值单元格": detailed["finite_numeric_cell_count"],
                "完整XY点行": detailed["complete_xy_row_count"],
                "半对XY行": detailed["partial_xy_row_count"],
                "曲线实例": detailed["curve_instance_count"],
                "只读": detailed["read_only"],
                "CRC": "由ZIP全检覆盖",
                "分级": grade,
                "模型资格": eligibility,
                "ZIP成员SHA256": member["zip_member_sha256"],
                "ZIP解包匹配": member["byte_identical"],
                "备注": note,
            }
        )

    unique_curves: dict[str, dict[str, Any]] = {}
    for row in curves:
        unique_curves.setdefault(row["curve_content_sha256"], row)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in curves:
        grouped[row["curve_content_sha256"]].append(row)
    duplicate_groups = {
        digest: rows
        for digest, rows in grouped.items()
        if len({row["relative_path"] for row in rows}) > 1
    }
    core_unique = [
        row
        for row in unique_curves.values()
        if row["modality"] == "bulk_tensile" and row["eligibility"] == "main_curve"
    ]
    counts = {
        "formulation_count": len({row["formulation"] for row in curves}),
        "raw_curve_instance_count": len(curves),
        "deduplicated_curve_count": len(unique_curves),
        "core_bulk_tensile_curve_count": len(core_unique),
        "core_formulation_condition_group_count": len(
            {(row["formulation"], row["condition"]) for row in core_unique}
        ),
        "raw_point_count": sum(row["point_rows"] for row in curves),
        "deduplicated_point_count": sum(
            row["point_rows"] for row in unique_curves.values()
        ),
        "cross_file_duplicate_instance_count": sum(
            len(rows) - 1 for rows in duplicate_groups.values()
        ),
        "partial_xy_row_count": sum(
            row["x_only_rows"] + row["y_only_rows"] for row in curves
        ),
        "finite_numeric_cell_count": sum(
            int(row["finite_numeric_cell_count"] or 0) for row in detailed_files
        ),
    }
    if counts != EXPECTED_QUB_COUNTS:
        raise AuditBlocked(
            "QUB科学计数与冻结协议不符: "
            f"expected={EXPECTED_QUB_COUNTS}, actual={counts}"
        )

    datacite = json.loads(QUB_DATACITE.read_text(encoding="utf-8"))
    attributes = datacite["data"]["attributes"]
    if attributes["doi"].casefold() != QUB_DOI.casefold():
        raise AuditBlocked(f"QUB DataCite DOI异常: {attributes['doi']}")
    rights_list = attributes.get("rightsList") or []
    if isinstance(rights_list, dict):
        rights_list = [rights_list]
    cc_right = next(
        (
            item
            for item in rights_list
            if item.get("rightsIdentifier", "").casefold() == "cc-by-4.0"
        ),
        {},
    )

    duplicate_records = [
        {
            "curve_content_sha256": digest,
            "instance_count": len(rows),
            "instances": [
                {"relative_path": row["relative_path"], "label": row["label"]}
                for row in rows
            ],
        }
        for digest, rows in sorted(duplicate_groups.items())
    ]
    summary = {
        "schema_name": "QUB_生物基三重自修复TPU_只读复算",
        "schema_version": "2.0",
        "audit_baseline_date": AUDIT_BASELINE_DATE,
        "source_identity": {
            "dataset_doi": attributes["doi"],
            "dataset_title": attributes["titles"][0]["title"],
            "publisher": attributes["publisher"],
            "publication_year": attributes["publicationYear"],
            "license": cc_right.get("rights"),
            "license_identifier": cc_right.get("rightsIdentifier"),
            "license_url": cc_right.get("rightsUri"),
        },
        "integrity_and_inventory": {
            **{key: value for key, value in zip_audit.items() if key != "members"},
            "datacite_relative_path": project_relative(QUB_DATACITE),
            "datacite_sha256": sha256_file(QUB_DATACITE).upper(),
            "zip_members": zip_audit["members"],
        },
        "scientific_counts": {
            **counts,
            "formulations": sorted({row["formulation"] for row in curves}),
            "cross_file_duplicate_group_count": len(duplicate_groups),
            "core_counting_rule": "跨文件整曲线去重后，按明确试样曲线计数；曲线点不是独立样本。",
        },
        "leakage_and_split": {
            "default_leakage_key": QUB_DEFAULT_LEAKAGE_KEY,
            "resolved_group_values": sorted(
                {row["leakage_group"] for row in curves}
            ),
            "rule": "同一配方的全部试样、愈合条件、循环和表征必须同折；缺少合成批次时不得按曲线点随机拆分。",
        },
        "admission_boundary": {
            "core_gold": "41条跨文件去重后的本体单调拉伸独立试样曲线。",
            "auxiliary": "循环、纤维、电路、光谱、热分析、接触角和派生汇总保留各自依赖关系与分级。",
            "excluded": "11条跨文件重复曲线和115个半对XY行不产生新增训练观测。",
            "training_materialized": False,
        },
        "cross_file_exact_duplicate_curves": duplicate_records,
        "curve_records": curves,
        "file_records": detailed_files,
        "reproducibility": {
            "input_snapshot_sha256": input_snapshot_digest,
            "paths": "project_relative_only",
            "runtime_timestamp_recorded": False,
            "output_files": sorted(QUB_OUTPUT_NAMES),
        },
    }
    return summary, file_rows


TPU95A_MANIFESTS = (
    ("压缩", "官方压缩文件清单.json", "实验文件/压缩"),
    ("拉伸", "官方拉伸文件清单.json", "实验文件/拉伸"),
    ("松弛", "官方松弛文件清单.json", "实验文件/松弛"),
)

TPU95A_FILE_COLUMNS = [
    "asset_class",
    "category",
    "filename",
    "local_relative_path",
    "official_manifest",
    "official_file_id",
    "content_type",
    "official_bytes",
    "actual_bytes",
    "size_match",
    "official_sha256",
    "actual_sha256",
    "sha256_match",
    "download_state",
    "record_type",
    "prior_registered_mirror_path",
    "exact_duplicate_of_prior_registered_asset",
    "incremental_scientific_sample_contribution",
    "current_weight_ceiling",
    "canonical_action",
]

TPU95A_CURVE_COLUMNS = [
    "curve_id",
    "category",
    "filename",
    "relative_path",
    "sha256",
    "bytes",
    "test_run",
    "test_date_raw",
    "source_test_label_raw",
    "source_path_was_instrument_absolute",
    "resolved_material_grade",
    "material_label_conflict",
    "condition",
    "columns",
    "units_json",
    "point_count",
    "invalid_row_count",
    "missing_cell_count",
    "exact_duplicate_rows_beyond_first",
    "time_start_s",
    "time_end_s",
    "median_positive_time_increment_s",
    "time_decrease_count",
    "load_min_N",
    "load_max_N",
    "time_window_counts_json",
    "domain_checks_json",
    "primary_quality_tier",
    "usable_representations",
    "blocked_or_quarantined_representations",
    "anomaly_flags",
    "prior_registered_mirror_path",
    "exact_duplicate_of_prior_registered_file",
    "incremental_scientific_sample_contribution",
    "current_weight_ceiling",
    "leakage_group",
]


def _manifest_content(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content_details")
    if not isinstance(content, dict):
        raise AuditBlocked(f"官方清单项目缺少content_details: {item.get('filename')}")
    return content


def audit_tpu95a_assets() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assets: list[dict[str, Any]] = []
    expected_prior_names: set[str] = set()

    for category, manifest_name, local_subdirectory in TPU95A_MANIFESTS:
        manifest_path = _input_file(TPU95A_DIR / manifest_name)
        items = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise AuditBlocked(f"官方清单不是数组: {manifest_name}")
        local_directory = TPU95A_DIR.joinpath(*PurePosixPath(local_subdirectory).parts)
        if not local_directory.is_dir():
            raise AuditBlocked(f"缺少TPU95A实验目录: {local_directory}")
        _assert_plain_ancestors(local_directory)
        expected_names = {item["filename"] for item in items}
        actual_names = {path.name for path in local_directory.iterdir() if path.is_file()}
        if actual_names != expected_names:
            raise AuditBlocked(
                f"TPU95A本地资产集合不符: {category}; "
                f"missing={sorted(expected_names - actual_names)}, "
                f"unexpected={sorted(actual_names - expected_names)}"
            )

        for item in items:
            filename = item["filename"]
            if PurePosixPath(filename).name != filename:
                raise AuditBlocked(f"官方清单文件名含路径: {filename}")
            content = _manifest_content(item)
            local_path = _input_file(local_directory / filename)
            actual_sha = sha256_file(local_path)
            official_sha = str(content["sha256_hash"]).casefold()
            official_size = int(content["size"])
            if local_path.stat().st_size != official_size or actual_sha != official_sha:
                raise AuditBlocked(f"TPU95A官方资产哈希或大小不符: {filename}")

            is_csv = local_path.suffix.casefold() == ".csv"
            is_jpg = local_path.suffix.casefold() in {".jpg", ".jpeg"}
            if not (is_csv or is_jpg):
                raise AuditBlocked(f"TPU95A出现未支持实验资产类型: {filename}")
            mirror_path: Path | None = None
            mirror_match = False
            if is_csv:
                mirror_name = f"{item['folder_id']}_{filename}"
                mirror_path = _input_file(TPU95A_PRIOR_DIR / mirror_name)
                expected_prior_names.add(mirror_name)
                mirror_match = (
                    mirror_path.stat().st_size == local_path.stat().st_size
                    and sha256_file(mirror_path) == actual_sha
                )
                if not mirror_match:
                    raise AuditBlocked(f"TPU95A新旧CSV镜像不一致: {filename}")

            assets.append(
                {
                    "asset_class": "experimental_curve_csv" if is_csv else "supporting_visual_jpg",
                    "category": category,
                    "filename": filename,
                    "local_path": local_path,
                    "local_relative_path": project_relative(local_path),
                    "official_manifest": project_relative(manifest_path),
                    "official_file_id": item["id"],
                    "content_type": content["content_type"],
                    "official_bytes": official_size,
                    "actual_bytes": local_path.stat().st_size,
                    "official_sha256": official_sha,
                    "actual_sha256": actual_sha,
                    "prior_path": mirror_path,
                    "prior_registered_mirror_path": (
                        project_relative(mirror_path) if mirror_path else ""
                    ),
                    "mirror_match": mirror_match,
                }
            )

    actual_prior_names = {
        path.name for path in TPU95A_PRIOR_DIR.iterdir() if path.is_file()
    }
    if actual_prior_names != expected_prior_names:
        raise AuditBlocked(
            "TPU95A既有镜像文件集合不符: "
            f"missing={sorted(expected_prior_names - actual_prior_names)}, "
            f"unexpected={sorted(actual_prior_names - expected_prior_names)}"
        )

    fea_manifest = _input_file(TPU95A_FEA_MANIFEST)
    fea_items = json.loads(fea_manifest.read_text(encoding="utf-8"))
    if not isinstance(fea_items, list):
        raise AuditBlocked("TPU95A FEA官方清单不是数组")
    fea_records = []
    for item in fea_items:
        content = _manifest_content(item)
        fea_records.append(
            {
                "asset_class": "abaqus_input_manifest_only",
                "category": "FEA",
                "filename": item["filename"],
                "local_relative_path": "",
                "official_manifest": project_relative(fea_manifest),
                "official_file_id": item["id"],
                "content_type": content["content_type"],
                "official_bytes": int(content["size"]),
                "actual_bytes": "",
                "official_sha256": content["sha256_hash"],
                "actual_sha256": "",
                "size_match": "",
                "sha256_match": "",
                "download_state": "official_manifest_only_not_downloaded",
                "record_type": "simulation_input_not_result",
                "prior_registered_mirror_path": "",
                "exact_duplicate_of_prior_registered_asset": False,
                "incremental_scientific_sample_contribution": 0,
                "current_weight_ceiling": 0.0,
                "canonical_action": "registry_only; training_weight_0; do_not_count_as_curve_or_label",
            }
        )
    return assets, fea_records


def _metadata_value(rows: list[list[str]], prefix: str) -> str:
    prefix_folded = prefix.casefold()
    for row in rows[:8]:
        if not row:
            continue
        text = _clean(row[0])
        if text.casefold().startswith(prefix_folded):
            return text[len(prefix) :].strip()
    return ""


def _compact_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def parse_tpu95a_curve(asset: dict[str, Any]) -> dict[str, Any]:
    path: Path = asset["local_path"]
    encoding, rows = _read_csv_rows(path)
    if encoding != "utf-8-sig":
        raise AuditBlocked(f"TPU95A CSV编码异常: {project_relative(path)}")
    header_index = next(
        (
            index
            for index, row in enumerate(rows[:10])
            if "Load" in {_clean(value) for value in row}
            and "Time" in {_clean(value) for value in row}
        ),
        -1,
    )
    if header_index < 0 or header_index + 1 >= len(rows):
        raise AuditBlocked(f"TPU95A CSV缺少字段或单位表头: {project_relative(path)}")
    columns = [_clean(value) for value in rows[header_index]]
    units = [_clean(value) for value in rows[header_index + 1]]
    if len(columns) != len(units):
        raise AuditBlocked(f"TPU95A CSV字段与单位数不符: {project_relative(path)}")

    numeric_rows: list[tuple[float, ...]] = []
    invalid_rows = 0
    missing_cells = 0
    for row in rows[header_index + 2 :]:
        values = [row[index] if index < len(row) else "" for index in range(len(columns))]
        missing_cells += sum(not _clean(value) for value in values)
        converted = tuple(_number(value) for value in values)
        if any(value is None for value in converted):
            invalid_rows += 1
            continue
        numeric_rows.append(tuple(value for value in converted if value is not None))
    if invalid_rows or missing_cells or not numeric_rows:
        raise AuditBlocked(
            f"TPU95A CSV存在无效或缺失数据: {project_relative(path)}; "
            f"invalid={invalid_rows}, missing={missing_cells}"
        )

    time_index = columns.index("Time")
    load_index = columns.index("Load")
    time_values = [row[time_index] for row in numeric_rows]
    load_values = [row[load_index] for row in numeric_rows]
    positive_increments = [
        current - previous
        for previous, current in zip(time_values, time_values[1:])
        if current > previous
    ]
    duplicate_rows = sum(
        count - 1
        for count in Counter(numeric_rows).values()
        if count > 1
    )
    source_path_raw = _metadata_value(rows, "File Path:")
    source_path_absolute = bool(
        re.match(r"^[A-Za-z]:[\\/]", source_path_raw)
        or source_path_raw.startswith("\\\\")
    )
    test_label = _metadata_value(rows, "Test:")
    test_run_text = _metadata_value(rows, "Test Run:")
    run_match = re.search(r"(\d+)", test_run_text)
    if not run_match:
        raise AuditBlocked(f"TPU95A CSV无法解析Test Run: {project_relative(path)}")
    test_run = int(run_match.group(1))
    test_date = _metadata_value(rows, "Date:")
    category = asset["category"]

    time_window_counts = {
        "at_or_below_100_s": sum(value <= 100 for value in time_values),
        "above_100_s": sum(value > 100 for value in time_values),
        "at_or_below_101_s": sum(value <= 101 for value in time_values),
        "above_101_s": sum(value > 101 for value in time_values),
    }
    anomalies: list[str] = []
    if category == "压缩":
        strain_index = columns.index("Strain")
        deflectometer_index = columns.index("Deflectometer")
        residuals = [
            abs(row[strain_index] - row[deflectometer_index] / 12.5)
            for row in numeric_rows
        ]
        domain_checks = {
            "article_nominal_height_mm": 12.5,
            "strain_equals_deflectometer_divided_by_12_5_max_abs_residual": max(residuals),
            "strain_equals_deflectometer_divided_by_12_5_median_abs_residual": statistics.median(residuals),
        }
        condition = "paper nominal strain rate=0.001 s^-1"
        quality_tier = "gold"
        usable = "原始载荷-位移-时间；CSV直接工程应变；论文名义几何仅作名义换算。"
        blocked = "不得宣称逐试样实测截面积；论文载荷量程冲突未澄清。"
        anomalies.append("paper_load_cell_2.5kN_but_raw_load_reaches_about_9.95kN")
        material_conflict = False
    elif category == "拉伸":
        extension_index = columns.index("Extensometer")
        baseline = numeric_rows[0][extension_index]
        strains = [(row[extension_index] - baseline) / 33.0 for row in numeric_rows]
        average_rate = (strains[-1] - strains[0]) / (time_values[-1] - time_values[0])
        domain_checks = {
            "article_indexed_gauge_length_mm": 33,
            "engineering_strain_min_using_33_mm": min(strains),
            "engineering_strain_max_using_33_mm": max(strains),
            "full_record_average_engineering_strain_rate_using_33_mm_per_s": average_rate,
        }
        condition = (
            "paper nominal strain rate=0.001 s^-1; "
            f"raw full-record mean from 33 mm gauge={format(average_rate, '.6g')} s^-1"
        )
        quality_tier = "silver"
        usable = "原始载荷-引伸计位移-时间；按33 mm标距并扣除基线得到工程应变。"
        blocked = "绝对应力、强度和韧性：窄段宽度与厚度未无歧义获得。"
        anomalies.extend(
            [
                "raw_test_template_says_PLA_but_path_dataset_and_article_identify_TPU95A",
                "paper_nominal_rate_0.001_per_s_conflicts_with_raw_full_record_mean_about_0.0056_to_0.0059_per_s",
            ]
        )
        material_conflict = "PLA" in test_label
    elif category == "松弛":
        crosshead_index = columns.index("Crosshead")
        deflectometer_index = columns.index("Deflectometer")
        nominal_strain = 0.1 if "Strain0_1" in path.name else 0.2
        max_crosshead = max(abs(row[crosshead_index]) for row in numeric_rows)
        max_deflectometer = max(abs(row[deflectometer_index]) for row in numeric_rows)
        domain_checks = {
            "nominal_strain_from_filename": nominal_strain,
            "max_abs_crosshead_mm": max_crosshead,
            "max_abs_crosshead_divided_by_article_height_12_5": max_crosshead / 12.5,
            "max_abs_crosshead_divided_by_28_6": max_crosshead / 28.6,
            "max_abs_deflectometer_mm": max_deflectometer,
            "max_abs_deflectometer_divided_by_article_height_12_5": max_deflectometer / 12.5,
            "max_abs_deflectometer_divided_by_28_6": max_deflectometer / 28.6,
        }
        condition = (
            f"nominal strain={nominal_strain}; paper loading rate=0.1 s^-1; hold=100 s"
        )
        quality_tier = "silver"
        usable = "原始载荷-时间及峰值归一化松弛；建模时统一峰后100 s窗口。"
        blocked = "绝对应力和实际应变：论文几何与原始位移存在冲突。"
        anomalies.append(
            "article_nominal_height_12.5mm_is_inconsistent_with_raw_displacement_and_filename_strain"
        )
        if time_values[-1] > 150:
            anomalies.append(
                "record_duration_about_201.7s_instead_of_about_101s; preserve_raw_but_standardize_first_100s_after_peak"
            )
        material_conflict = False
    else:
        raise AuditBlocked(f"未知TPU95A曲线类别: {category}")

    mirror_path: Path = asset["prior_path"]
    return {
        "curve_id": f"mc6zh4cwhf_v2_{category}_{path.stem}",
        "category": category,
        "filename": path.name,
        "relative_path": project_relative(path),
        "sha256": asset["actual_sha256"],
        "bytes": path.stat().st_size,
        "test_run": test_run,
        "test_date_raw": test_date,
        "source_test_label_raw": test_label,
        "source_path_was_instrument_absolute": source_path_absolute,
        "resolved_material_grade": "eSUN eTPU-95A",
        "material_label_conflict": material_conflict,
        "condition": condition,
        "columns": columns,
        "units": dict(zip(columns, units, strict=True)),
        "point_count": len(numeric_rows),
        "invalid_row_count": invalid_rows,
        "missing_cell_count": missing_cells,
        "exact_duplicate_rows_beyond_first": duplicate_rows,
        "time_start_s": time_values[0],
        "time_end_s": time_values[-1],
        "median_positive_time_increment_s": statistics.median(positive_increments),
        "time_decrease_count": sum(
            current < previous
            for previous, current in zip(time_values, time_values[1:])
        ),
        "load_min_N": min(load_values),
        "load_max_N": max(load_values),
        "time_window_counts": time_window_counts,
        "domain_checks": domain_checks,
        "primary_quality_tier": quality_tier,
        "usable_representations": usable,
        "blocked_or_quarantined_representations": blocked,
        "anomaly_flags": anomalies,
        "prior_registered_mirror_path": project_relative(mirror_path),
        "exact_duplicate_of_prior_registered_file": True,
        "incremental_scientific_sample_contribution": 0,
        "current_weight_ceiling": 0.0,
        "leakage_group": f"{TPU95A_DOI}|eSUN eTPU-95A",
    }


def _tpu95a_curve_tsv_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "material_label_conflict": str(row["material_label_conflict"]).lower(),
        "source_path_was_instrument_absolute": str(
            row["source_path_was_instrument_absolute"]
        ).lower(),
        "columns": "|".join(row["columns"]),
        "units_json": _compact_json(row["units"]),
        "time_window_counts_json": _compact_json(row["time_window_counts"]),
        "domain_checks_json": _compact_json(row["domain_checks"]),
        "anomaly_flags": "|".join(row["anomaly_flags"]),
        "exact_duplicate_of_prior_registered_file": "true",
    }


def build_tpu95a_outputs(
    input_snapshot_digest: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    assets, fea_rows = audit_tpu95a_assets()
    csv_assets = [asset for asset in assets if asset["asset_class"] == "experimental_curve_csv"]
    curves = [parse_tpu95a_curve(asset) for asset in csv_assets]
    category_order = {"压缩": 0, "拉伸": 1, "松弛": 2}
    curves.sort(key=lambda row: (category_order[row["category"]], row["filename"]))

    file_rows: list[dict[str, Any]] = []
    for asset in assets:
        is_curve = asset["asset_class"] == "experimental_curve_csv"
        file_rows.append(
            {
                "asset_class": asset["asset_class"],
                "category": asset["category"],
                "filename": asset["filename"],
                "local_relative_path": asset["local_relative_path"],
                "official_manifest": asset["official_manifest"],
                "official_file_id": asset["official_file_id"],
                "content_type": asset["content_type"],
                "official_bytes": asset["official_bytes"],
                "actual_bytes": asset["actual_bytes"],
                "size_match": "true",
                "official_sha256": asset["official_sha256"],
                "actual_sha256": asset["actual_sha256"],
                "sha256_match": "true",
                "download_state": "downloaded_verified",
                "record_type": "experimental_raw_curve" if is_curve else "supporting_visual",
                "prior_registered_mirror_path": asset["prior_registered_mirror_path"],
                "exact_duplicate_of_prior_registered_asset": (
                    "true" if is_curve else "false"
                ),
                "incremental_scientific_sample_contribution": 0,
                "current_weight_ceiling": 0.0,
                "canonical_action": (
                    "collapse_to_existing_TPU95A_2026_asset; do_not_double_count"
                    if is_curve
                    else "retain_as_supporting_visual; no_scientific_sample"
                ),
            }
        )
    file_rows.extend(fea_rows)

    counts = {
        "downloaded_experimental_asset_count": len(assets),
        "downloaded_csv_curve_count": len(csv_assets),
        "downloaded_supporting_jpg_count": sum(
            asset["asset_class"] == "supporting_visual_jpg" for asset in assets
        ),
        "prior_directory_csv_count": len(
            [path for path in TPU95A_PRIOR_DIR.iterdir() if path.suffix.casefold() == ".csv"]
        ),
        "mirror_hash_match_count": sum(asset["mirror_match"] for asset in csv_assets),
        "raw_point_count": sum(row["point_count"] for row in curves),
        "manifest_only_fea_count": len(fea_rows),
        "incremental_scientific_sample_count": 0,
    }
    if counts != EXPECTED_TPU95A_COUNTS:
        raise AuditBlocked(
            "TPU95A镜像计数与冻结协议不符: "
            f"expected={EXPECTED_TPU95A_COUNTS}, actual={counts}"
        )
    category_curve_counts = Counter(row["category"] for row in curves)
    category_point_counts = Counter()
    for row in curves:
        category_point_counts[row["category"]] += row["point_count"]
    if category_curve_counts != Counter({"拉伸": 3, "压缩": 3, "松弛": 6}):
        raise AuditBlocked(f"TPU95A类别曲线数异常: {category_curve_counts}")
    if category_point_counts != Counter({"拉伸": 15_468, "压缩": 10_365, "松弛": 21_232}):
        raise AuditBlocked(f"TPU95A类别点数异常: {category_point_counts}")

    datacite = json.loads(TPU95A_DATACITE.read_text(encoding="utf-8"))
    attributes = datacite["data"]["attributes"]
    if attributes["doi"].casefold() != TPU95A_DOI.casefold():
        raise AuditBlocked(f"TPU95A DataCite DOI异常: {attributes['doi']}")
    rights_list = attributes.get("rightsList") or []
    if isinstance(rights_list, dict):
        rights_list = [rights_list]
    cc_right = next(
        (
            item
            for item in rights_list
            if item.get("rightsIdentifier", "").casefold() == "cc-by-4.0"
        ),
        {},
    )

    summary = {
        "schema_name": "TPU95A_Mendeley_镜像只读复算",
        "schema_version": "2.0",
        "audit_baseline_date": AUDIT_BASELINE_DATE,
        "source_identity": {
            "dataset_doi": attributes["doi"],
            "dataset_title": attributes["titles"][0]["title"],
            "dataset_publisher": attributes["publisher"],
            "dataset_version": attributes.get("version"),
            "publication_year": attributes["publicationYear"],
            "license": cc_right.get("rights"),
            "license_identifier": cc_right.get("rightsIdentifier"),
            "license_url": cc_right.get("rightsUri"),
            "resolved_material_grade": "eSUN eTPU-95A",
        },
        "integrity_and_inventory": {
            **counts,
            "prior_directory": project_relative(TPU95A_PRIOR_DIR),
            "official_experimental_asset_hash_match_count": len(assets),
            "all_local_paths_project_relative": True,
            "all_csv_rows_finite_and_complete": all(
                row["invalid_row_count"] == 0 and row["missing_cell_count"] == 0
                for row in curves
            ),
        },
        "scientific_counts": {
            "resolved_material_grade_count": 1,
            "reported_specimen_test_count": len(curves),
            "raw_curve_count": len(curves),
            "curve_counts_by_category": dict(sorted(category_curve_counts.items())),
            "raw_numeric_points_by_category": dict(sorted(category_point_counts.items())),
            "total_raw_numeric_points": sum(category_point_counts.values()),
            "incremental_scientific_sample_count": 0,
            "counting_rule": "12条CSV是既有TPU95A_2026资产的逐字节镜像；时间点不计独立试样。",
        },
        "mirror_admission_boundary": {
            "classification": "exact_download_mirror",
            "matched_csv_count": 12,
            "incremental_scientific_sample_count": 0,
            "current_weight_ceiling": 0.0,
            "canonical_action": "collapse_to_existing_TPU95A_2026_asset; do_not_double_count",
            "supporting_jpg_scientific_sample_contribution": 0,
            "manifest_only_fea_current_weight_ceiling": 0.0,
        },
        "leakage_and_split": {
            "default_group": f"{TPU95A_DOI}|eSUN eTPU-95A",
            "rule": "新旧镜像折叠到同一canonical_asset_id；同一曲线的时间点和未来由本文实验拟合的FEA结果必须同折。",
        },
        "quality_boundaries": {
            "tensile": "保留PLA测试模板冲突；缺少无歧义截面积时不生成绝对应力标签。",
            "compression": "CSV应变可用；名义几何和论文载荷量程冲突必须保留。",
            "relaxation": "载荷-时间与归一化响应可用；几何冲突解决前隔离绝对应力/实际应变。",
            "weighting": "按曲线/试样等权，不按点数等权；镜像副本权重为0。",
        },
        "curve_records": curves,
        "manifest_only_fea_records": [
            {
                "filename": row["filename"],
                "official_manifest": row["official_manifest"],
                "official_bytes": row["official_bytes"],
                "official_sha256": row["official_sha256"],
                "record_type": row["record_type"],
                "current_weight_ceiling": 0.0,
            }
            for row in fea_rows
        ],
        "reproducibility": {
            "input_snapshot_sha256": input_snapshot_digest,
            "paths": "project_relative_only",
            "runtime_timestamp_recorded": False,
            "output_files": sorted(TPU95A_OUTPUT_NAMES),
        },
    }
    return summary, file_rows, curves


def validate_layout() -> dict[str, Any]:
    if SCRIPT_DIR.name != "审计" or SCRIPT_DIR.parent.name != "代码":
        raise AuditBlocked(f"脚本必须位于 <项目根>/代码/审计: {SCRIPT_PATH}")
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        raise AuditBlocked(f"无法确认项目根目录: {PROJECT_ROOT}")
    for directory in (QUB_DIR, QUB_UNPACKED, TPU95A_DIR, TPU95A_PRIOR_DIR):
        if not directory.is_dir():
            raise AuditBlocked(f"缺少必需目录: {directory}")
        _assert_plain_ancestors(directory)
    for path in OUTPUT_WHITELIST:
        assert_output_allowed(path)
    stale = sorted(
        [
            path
            for root in (QUB_DIR, TPU95A_DIR)
            for path in root.glob(".*.audit.tmp")
        ],
        key=str,
    )
    if stale:
        raise AuditBlocked(f"来源目录存在遗留审计临时文件: {stale}")
    return {
        "audit_baseline_date": AUDIT_BASELINE_DATE,
        "project_root_verified": True,
        "source_directories": [project_relative(QUB_DIR), project_relative(TPU95A_DIR)],
        "prior_mirror_directory": project_relative(TPU95A_PRIOR_DIR),
        "allowed_outputs": sorted(project_relative(path) for path in OUTPUT_WHITELIST),
        "scientific_input_file_count": len(_scientific_input_paths()),
    }


def _validate_written_outputs(expected_rows: dict[Path, int]) -> None:
    for path in OUTPUT_WHITELIST:
        assert_output_allowed(path)
        if not path.is_file():
            raise AuditBlocked(f"审计输出未生成: {path}")
        if path.suffix.casefold() == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        else:
            with path.open("r", encoding="utf-8", newline="") as handle:
                actual_rows = sum(1 for _ in csv.reader(handle, delimiter="\t")) - 1
            if actual_rows != expected_rows[path]:
                raise AuditBlocked(
                    f"TSV回读行数不符: {project_relative(path)}; "
                    f"expected={expected_rows[path]}, actual={actual_rows}"
                )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读复算QUB核心来源和TPU95A重复下载镜像。"
    )
    parser.add_argument(
        "--check-layout",
        "--检查环境",
        action="store_true",
        help="只检查路径、白名单和输入布局，不写审计产物。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    layout = validate_layout()
    if args.check_layout:
        print(json.dumps(layout, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    before = capture_input_snapshot()
    before_digest = input_snapshot_sha256(before)
    qub_summary, qub_file_rows = build_qub_outputs(before_digest)
    tpu_summary, tpu_file_rows, tpu_curves = build_tpu95a_outputs(before_digest)

    atomic_write(QUB_DIR / "文件校验清单.tsv", _tsv_bytes(QUB_FILE_COLUMNS, qub_file_rows))
    atomic_write(QUB_DIR / "内容审计摘要.json", _json_bytes(qub_summary))
    atomic_write(
        TPU95A_DIR / "文件校验清单.tsv",
        _tsv_bytes(TPU95A_FILE_COLUMNS, tpu_file_rows),
    )
    atomic_write(
        TPU95A_DIR / "曲线解析清单.tsv",
        _tsv_bytes(
            TPU95A_CURVE_COLUMNS,
            [_tpu95a_curve_tsv_row(row) for row in tpu_curves],
        ),
    )
    atomic_write(TPU95A_DIR / "内容审计摘要.json", _json_bytes(tpu_summary))

    after = capture_input_snapshot()
    if before != after:
        raise AuditBlocked("科学输入哈希在审计运行期间发生变化")
    _validate_written_outputs(
        {
            QUB_DIR / "文件校验清单.tsv": len(qub_file_rows),
            TPU95A_DIR / "文件校验清单.tsv": len(tpu_file_rows),
            TPU95A_DIR / "曲线解析清单.tsv": len(tpu_curves),
        }
    )

    result = {
        "audit_baseline_date": AUDIT_BASELINE_DATE,
        "input_snapshot_sha256": before_digest,
        "input_hashes_unchanged": True,
        "QUB_生物基三重自修复TPU": qub_summary["scientific_counts"],
        "Mendeley_TPU95A_TPMS应变率力学": {
            **tpu_summary["scientific_counts"],
            "mirror_current_weight_ceiling": 0.0,
        },
        "output_sha256": {
            project_relative(path): sha256_file(path)
            for path in sorted(OUTPUT_WHITELIST, key=project_relative)
        },
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
