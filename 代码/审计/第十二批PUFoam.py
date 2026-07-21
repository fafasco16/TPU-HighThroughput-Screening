"""PUFoam 公开归档的安全审计与 Gold-C 过程观测物化。

本模块只通过 :mod:`tarfile` 顺序读取冻结的 ``PUFoam.tar.gz``，不会把归档
解压到文件系统。归档成员在读取前逐一经过路径门禁；相对符号链接只有在逻辑
解析后仍位于 ``PUFoam/`` 根目录内才允许通过。

物化结果由两部分组成：

* ``cellSource.dat`` 的 50 个时刻 × 5 个源生体积平均值；
* 1--50 s 的 52 个 ``volScalarField`` 内部场空间统计。uniform 场只记录
  mean，nonuniform 场记录 mean/min/max/population_std；与 ``cellSource``
  重复的五个场不再重复记录 derived mean。

这里保留的是一个经过实验验证的反应性 CFD 过程参考，不把 50 个输出时刻
错误计成 50 个材料体系，也不创建训练权重或训练/验证划分。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import posixpath
import re
import statistics
import tarfile
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    PROJECT_ROOT
    / "数据/原始"
    / "外部数据"
    / "新增开放数据"
    / "第十二批计算_PUFoam"
)
ARCHIVE_PATH = SOURCE_DIR / "PUFoam.tar.gz"
OFFICIAL_METADATA_PATH = SOURCE_DIR / "官方元数据.json"
CROSSREF_METADATA_PATH = SOURCE_DIR / "论文Crossref元数据.json"

OUTPUT_TSV = SOURCE_DIR / "Gold_C_过程观测长表.tsv"
OUTPUT_AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUTPUT_CHECKSUMS = SOURCE_DIR / "文件校验清单.tsv"
OUTPUT_README = SOURCE_DIR / "来源说明.md"

SOURCE_ID = "source_mendeley_pufoam_v1"
SOURCE_DOI = "10.17632/62ggzx623g.1"
PAPER_DOI = "10.1016/j.cpc.2017.03.010"
CITATION_KEYS = (
    "ledger-161-karimi-2017-pufoam-data;"
    "ledger-162-karimi-2017-pufoam-paper"
)

ARCHIVE_BYTES = 8_252_834
ARCHIVE_SHA256 = "64e0a24689b8b9dcb8046ed9b3730ae6666180991bc40f75971b69ef56b63b84"
EXPECTED_MEMBER_COUNT = 3_822
EXPECTED_REGULAR_FILE_COUNT = 3_452
EXPECTED_DIRECTORY_COUNT = 331
EXPECTED_SYMLINK_COUNT = 39
EXPECTED_RESULT_TIMES = tuple(range(51))
EXPECTED_SCALAR_TIMES = tuple(range(1, 51))
EXPECTED_VOL_SCALAR_FIELDS_PER_TIME = 52
EXPECTED_UNIFORM_FIELD_FILES = 462
EXPECTED_NONUNIFORM_FIELD_FILES = 2_138
EXPECTED_NATIVE_ROWS = 250
EXPECTED_DERIVED_ROWS = 8_764
EXPECTED_TOTAL_ROWS = 9_014

ARCHIVE_ROOT = "PUFoam"
RESULTS_ROOT = "PUFoam/testCase/results"
CELL_SOURCE_MEMBER = (
    "PUFoam/testCase/results/postProcessing/volAverage/0/cellSource.dat"
)
CONTROL_MEMBER = "PUFoam/testCase/results/system/controlDict"
BOUNDARY_MEMBER = "PUFoam/testCase/results/constant/polyMesh/boundary"
README_MEMBER = "PUFoam/README.md"

SYSTEM_IDENTITY = (
    "generic NCO/OH/water + n-pentane polyurethane foam; "
    "single 2D mixing-cup case"
)
STRUCTURE_FAMILY_KEY = "family_pufoam_generic_nco_oh_water_npentane"
SIMULATION_KEY = "simulation_pufoam_2d_mixing_cup_v1"

GOLD_C_VALUE_COLUMNS = (
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "system_identity",
    "structure_identity_status",
    "global_structure_family_key",
    "simulation_key",
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

CELL_SOURCE_FIELDS = (
    "alpha.gas",
    "mZero",
    "mOne",
    "rho_foam",
    "rho",
)
DUPLICATE_MEAN_FIELDS = frozenset(CELL_SOURCE_FIELDS)

DIMENSION_TO_UNIT = {
    "[0 0 0 0 0 0 0]": "dimensionless",
    "[1 -1 -1 0 0 0 0]": "Pa*s",
    "[0 0 0 1 0 0 0]": "K",
    "[0 2 -1 0 0 0 0]": "m^2/s",
    "[1 -3 0 0 0 0 0]": "kg/m^3",
    "[0 -2 2 0 0 0 0]": "s^2/m^2",
    "[1 -1 -2 0 0 0 0]": "Pa",
}

CELL_SOURCE_UNITS = {
    "alpha.gas": ("dimensionless", "resolved"),
    "mZero": ("source_native_unit_unresolved", "unresolved"),
    "mOne": ("source_native_unit_unresolved", "unresolved"),
    "rho_foam": ("kg/m^3", "resolved"),
    "rho": ("kg/m^3", "resolved"),
}

NATIVE_RESOLVED_FIELDS = frozenset({"alpha.gas", "rho_foam", "rho"})
SEMANTICS_UNCLOSED_DERIVED_FIELDS = frozenset(
    {
        "M0",
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "mZero",
        "mOne",
        "mTwo",
        "mThree",
        "mFour",
        "mFive",
        "node0",
        "node1",
        "node2",
        "weight0",
        "weight1",
        "weight2",
        "Psi1",
        "Psi2",
        "cc1",
        "creamT",
        "g1_BA",
        "g1_CO2",
        # The source field name says conductivity while its declared OpenFOAM
        # dimensions are diffusivity. Preserve the declared unit but do not
        # claim that the physical property semantics are closed.
        "thermalConductivity",
    }
)

POLICY_TIER_NATIVE_RESOLVED = "native_unit_resolved_admitted"
POLICY_TIER_NATIVE_UNRESOLVED = "native_unit_unresolved_conditional"
POLICY_TIER_DERIVED_RESOLVED = "derived_dimensions_resolved_admitted"
POLICY_TIER_DERIVED_UNCLOSED = "derived_semantics_unresolved_conditional"
EXPECTED_POLICY_TIER_COUNTS = {
    POLICY_TIER_NATIVE_RESOLVED: 150,
    POLICY_TIER_NATIVE_UNRESOLVED: 100,
    POLICY_TIER_DERIVED_RESOLVED: 4_143,
    POLICY_TIER_DERIVED_UNCLOSED: 4_621,
}

_RESULT_TIME_DIR_RE = re.compile(
    r"^PUFoam/testCase/results/(?P<time>0|[1-9]|[1-4][0-9]|50)/?$"
)
_RESULT_FIELD_RE = re.compile(
    r"^PUFoam/testCase/results/(?P<time>[1-9]|[1-4][0-9]|50)/"
    r"(?P<field>[^/]+)$"
)
_CLASS_RE = re.compile(r"\bclass\s+(?P<class>\S+);", re.MULTILINE)
_OBJECT_RE = re.compile(r"\bobject\s+(?P<object>\S+);", re.MULTILINE)
_DIMENSION_RE = re.compile(r"\bdimensions\s+(?P<dimensions>\[[^;]+\]);")
_UNIFORM_RE = re.compile(
    r"\binternalField\s+uniform\s+"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*;"
)
_NONUNIFORM_RE = re.compile(
    r"\binternalField\s+nonuniform\s+List<scalar>\s+"
    r"(?P<count>\d+)\s*\((?P<body>.*?)\)\s*;",
    re.DOTALL,
)
_FLOAT_RE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)


class AuditBlocked(RuntimeError):
    """冻结输入、归档安全或确定性数量发生漂移。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_archive_identity() -> None:
    if not ARCHIVE_PATH.is_file():
        raise AuditBlocked(f"缺少冻结归档：{ARCHIVE_PATH}")
    actual_size = ARCHIVE_PATH.stat().st_size
    if actual_size != ARCHIVE_BYTES:
        raise AuditBlocked(
            f"归档字节数漂移：expected={ARCHIVE_BYTES}, actual={actual_size}"
        )
    actual_hash = _sha256(ARCHIVE_PATH)
    if actual_hash != ARCHIVE_SHA256:
        raise AuditBlocked(
            f"归档 SHA-256 漂移：expected={ARCHIVE_SHA256}, actual={actual_hash}"
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked(f"元数据无法读取：{path}") from exc
    if not isinstance(payload, dict):
        raise AuditBlocked(f"元数据顶层不是对象：{path}")
    return payload


def _verify_metadata() -> dict[str, Any]:
    official = _read_json(OFFICIAL_METADATA_PATH)
    if official.get("doi") != SOURCE_DOI or official.get("version") != 1:
        raise AuditBlocked("Mendeley 官方 DOI 或版本漂移")
    licence = official.get("licence")
    if not isinstance(licence, dict) or licence.get("short_name") != "GPLv3":
        raise AuditBlocked("Mendeley 官方许可证不是 GPLv3")

    crossref = _read_json(CROSSREF_METADATA_PATH)
    message = crossref.get("message")
    if not isinstance(message, dict) or str(message.get("DOI", "")).lower() != PAPER_DOI:
        raise AuditBlocked("Crossref 论文 DOI 漂移")
    return {
        "dataset_doi": SOURCE_DOI,
        "paper_doi": PAPER_DOI,
        "dataset_version": 1,
        "license": "GPLv3",
        "published_date": official.get("publish_date", ""),
    }


def _normal_member_name(name: str) -> str:
    """校验归档成员自身路径，返回无尾斜杠的规范 POSIX 路径。"""

    if not name or "\x00" in name or "\\" in name:
        raise AuditBlocked(f"归档成员路径非法：{name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or name.startswith("/"):
        raise AuditBlocked(f"归档成员使用绝对路径：{name}")
    parts = tuple(part for part in name.rstrip("/").split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise AuditBlocked(f"归档成员含路径穿越片段：{name}")
    normalized = posixpath.normpath(name)
    if normalized != ARCHIVE_ROOT and not normalized.startswith(f"{ARCHIVE_ROOT}/"):
        raise AuditBlocked(f"归档成员越出 PUFoam 根目录：{name}")
    return normalized


def _validate_archive_member(member: tarfile.TarInfo) -> dict[str, str] | None:
    """对一个 TarInfo 做 fail-closed 路径与类型门禁。"""

    normalized = _normal_member_name(member.name)
    if member.isfile() or member.isdir():
        return None
    if not member.issym():
        raise AuditBlocked(f"归档包含不允许的特殊成员：{member.name}; type={member.type!r}")

    linkname = member.linkname
    if not linkname or "\x00" in linkname or "\\" in linkname:
        raise AuditBlocked(f"符号链接目标非法：{member.name} -> {linkname!r}")
    if PurePosixPath(linkname).is_absolute() or linkname.startswith("/"):
        raise AuditBlocked(f"符号链接使用绝对目标：{member.name} -> {linkname}")
    resolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(normalized), linkname)
    )
    if resolved != ARCHIVE_ROOT and not resolved.startswith(f"{ARCHIVE_ROOT}/"):
        raise AuditBlocked(f"符号链接越出 PUFoam 根目录：{member.name} -> {linkname}")
    return {"member": normalized, "linkname": linkname, "resolved_target": resolved}


def _decode_member(handle: io.BufferedReader | None, member_name: str) -> str:
    if handle is None:
        raise AuditBlocked(f"无法读取归档成员：{member_name}")
    try:
        return handle.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditBlocked(f"归档文本不是 UTF-8：{member_name}") from exc


def _parse_scalar_field(
    text: str, *, member_name: str, expected_field: str
) -> tuple[str, list[float]]:
    class_match = _CLASS_RE.search(text)
    object_match = _OBJECT_RE.search(text)
    dimension_match = _DIMENSION_RE.search(text)
    if class_match is None or object_match is None or dimension_match is None:
        raise AuditBlocked(f"OpenFOAM 字段头不完整：{member_name}")
    if class_match.group("class") != "volScalarField":
        raise AuditBlocked(f"目标字段不是 volScalarField：{member_name}")
    if object_match.group("object") != expected_field:
        raise AuditBlocked(
            f"字段 object 与成员名不一致：{member_name}; "
            f"object={object_match.group('object')}"
        )
    dimensions = " ".join(dimension_match.group("dimensions").split())
    if dimensions not in DIMENSION_TO_UNIT:
        raise AuditBlocked(f"未登记的 OpenFOAM dimensions：{member_name}; {dimensions}")

    uniform_match = _UNIFORM_RE.search(text)
    nonuniform_match = _NONUNIFORM_RE.search(text)
    if (uniform_match is None) == (nonuniform_match is None):
        raise AuditBlocked(f"internalField 模式无法唯一识别：{member_name}")
    if uniform_match is not None:
        values = [float(uniform_match.group("value"))]
    else:
        assert nonuniform_match is not None
        expected_count = int(nonuniform_match.group("count"))
        values = [
            float(token)
            for token in _FLOAT_RE.findall(nonuniform_match.group("body"))
        ]
        if len(values) != expected_count:
            raise AuditBlocked(
                f"nonuniform 数量漂移：{member_name}; "
                f"declared={expected_count}, parsed={len(values)}"
            )
        if expected_count != 800:
            raise AuditBlocked(
                f"内部场网格单元数漂移：{member_name}; expected=800, actual={expected_count}"
            )
    if not values or not all(math.isfinite(value) for value in values):
        raise AuditBlocked(f"字段含非有限数值：{member_name}")
    return dimensions, values


def _parse_cell_source(text: str) -> list[tuple[int, str, float]]:
    expected_header = tuple(f"volAverage({field})" for field in CELL_SOURCE_FIELDS)
    header: tuple[str, ...] | None = None
    records: list[tuple[int, str, float]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "Time" in line and "volAverage(" in line:
                tokens = tuple(line.lstrip("# ").split())
                if not tokens or tokens[0] != "Time":
                    raise AuditBlocked("cellSource 表头缺少 Time")
                header = tokens[1:]
            continue
        if header is None:
            raise AuditBlocked("cellSource 数据出现在表头之前")
        tokens = line.split()
        if len(tokens) != len(header) + 1:
            raise AuditBlocked(f"cellSource 列数漂移：{raw_line}")
        time_value = float(tokens[0])
        if not time_value.is_integer():
            raise AuditBlocked(f"cellSource 时刻不是整数秒：{tokens[0]}")
        time_s = int(time_value)
        for field_token, value_token in zip(header, tokens[1:], strict=True):
            field = field_token.removeprefix("volAverage(").removesuffix(")")
            value = float(value_token)
            if not math.isfinite(value):
                raise AuditBlocked(f"cellSource 含非有限数值：time={time_s}; field={field}")
            records.append((time_s, field, value))

    if header != expected_header:
        raise AuditBlocked(
            f"cellSource 五字段漂移：expected={expected_header}, actual={header}"
        )
    if len(records) != EXPECTED_NATIVE_ROWS:
        raise AuditBlocked(
            f"cellSource 数值数漂移：expected={EXPECTED_NATIVE_ROWS}, actual={len(records)}"
        )
    times = sorted({time_s for time_s, _, _ in records})
    if times != list(EXPECTED_SCALAR_TIMES):
        raise AuditBlocked(f"cellSource 时刻漂移：{times}")
    return records


def _fmt(value: float) -> str:
    if value == 0:
        value = 0.0
    return format(value, ".17g")


def _observation_id(source_record_id: str) -> str:
    digest = hashlib.sha256(f"{SOURCE_ID}|{source_record_id}".encode("utf-8")).hexdigest()
    return f"obs_pufoam_{digest[:24]}"


def _base_row(
    *,
    time_s: int,
    field: str,
    aggregation: str,
    value: float,
    unit: str,
    unit_status: str,
    member_name: str,
    record_role: str,
    method_detail: str,
    fidelity_level: str,
    gold_admission_status: str,
    source_validation_status: str,
    potential_weight_ceiling: str,
) -> dict[str, str]:
    source_record_id = (
        f"pufoam_2d_cup|time_s={time_s}|field={field}|aggregation={aggregation}"
    )
    property_field = re.sub(r"[^0-9A-Za-z]+", "_", field).strip("_").lower()
    row = {
        "source_id": SOURCE_ID,
        "source_record_id": source_record_id,
        "observation_id": _observation_id(source_record_id),
        "canonical_structure": "",
        "system_identity": SYSTEM_IDENTITY,
        "structure_identity_status": "process_system_identity_only",
        "global_structure_family_key": STRUCTURE_FAMILY_KEY,
        "simulation_key": SIMULATION_KEY,
        "property_name": f"pufoam_{property_field}_{aggregation}",
        "value": _fmt(value),
        "unit": unit,
        "unit_status": unit_status,
        "method_family": "CFD-PBE-QMOM",
        "method_detail": method_detail,
        "fidelity_level": fidelity_level,
        "temp": "",
        "press": "",
        "gold_admission_status": gold_admission_status,
        "property_admission_status": gold_admission_status,
        "source_validation_status": source_validation_status,
        "record_role": record_role,
        "potential_weight_ceiling": potential_weight_ceiling,
        "current_weight_materialized": "false",
        "training_weight": "",
        "source_locator": (
            f"tar=PUFoam.tar.gz;member={member_name};time_s={time_s};"
            f"field={field};aggregation={aggregation}"
        ),
        "citation_keys": CITATION_KEYS,
    }
    if tuple(row) != GOLD_C_VALUE_COLUMNS:
        raise AuditBlocked("Gold-C 字段顺序或字段集合发生内部漂移")
    return row


@lru_cache(maxsize=1)
def _materialize() -> tuple[tuple[dict[str, str], ...], dict[str, Any]]:
    _verify_archive_identity()
    metadata = _verify_metadata()

    member_count = 0
    regular_file_count = 0
    directory_count = 0
    symlink_count = 0
    special_member_count = 0
    member_names: set[str] = set()
    symlinks: list[dict[str, str]] = []
    result_time_dirs: set[int] = set()
    field_files_by_time: dict[int, set[str]] = defaultdict(set)
    vol_scalar_files_by_time: dict[int, set[str]] = defaultdict(set)
    field_payloads: dict[tuple[int, str], tuple[str, list[float], str]] = {}
    class_counts: Counter[str] = Counter()
    uniform_file_count = 0
    nonuniform_file_count = 0
    cell_source_text: str | None = None
    control_text: str | None = None
    boundary_text: str | None = None
    readme_text: str | None = None

    with tarfile.open(ARCHIVE_PATH, mode="r|gz") as archive:
        for member in archive:
            member_count += 1
            normalized = _normal_member_name(member.name)
            if normalized in member_names:
                raise AuditBlocked(f"归档含重复成员路径：{normalized}")
            member_names.add(normalized)
            link_record = _validate_archive_member(member)

            if member.isfile():
                regular_file_count += 1
            elif member.isdir():
                directory_count += 1
            elif member.issym():
                symlink_count += 1
                assert link_record is not None
                symlinks.append(link_record)
            else:  # pragma: no cover - 类型门禁会先阻止
                special_member_count += 1

            time_dir_match = _RESULT_TIME_DIR_RE.match(member.name)
            if member.isdir() and time_dir_match is not None:
                result_time_dirs.add(int(time_dir_match.group("time")))

            if not member.isfile():
                continue

            if normalized == CELL_SOURCE_MEMBER:
                cell_source_text = _decode_member(archive.extractfile(member), normalized)
                continue
            if normalized == CONTROL_MEMBER:
                control_text = _decode_member(archive.extractfile(member), normalized)
                continue
            if normalized == BOUNDARY_MEMBER:
                boundary_text = _decode_member(archive.extractfile(member), normalized)
                continue
            if normalized == README_MEMBER:
                readme_text = _decode_member(archive.extractfile(member), normalized)
                continue

            field_match = _RESULT_FIELD_RE.match(normalized)
            if field_match is None:
                continue
            time_s = int(field_match.group("time"))
            field = field_match.group("field")
            field_files_by_time[time_s].add(field)
            text = _decode_member(archive.extractfile(member), normalized)
            class_match = _CLASS_RE.search(text)
            if class_match is None:
                raise AuditBlocked(f"结果字段缺少 OpenFOAM class：{normalized}")
            field_class = class_match.group("class")
            class_counts[field_class] += 1
            if field_class != "volScalarField":
                continue
            dimensions, values = _parse_scalar_field(
                text, member_name=normalized, expected_field=field
            )
            if len(values) == 1:
                uniform_file_count += 1
            else:
                nonuniform_file_count += 1
            vol_scalar_files_by_time[time_s].add(field)
            field_payloads[(time_s, field)] = (dimensions, values, normalized)

    if (
        member_count != EXPECTED_MEMBER_COUNT
        or regular_file_count != EXPECTED_REGULAR_FILE_COUNT
        or directory_count != EXPECTED_DIRECTORY_COUNT
        or symlink_count != EXPECTED_SYMLINK_COUNT
        or special_member_count != 0
    ):
        raise AuditBlocked(
            "归档成员计数漂移："
            f"members={member_count}, files={regular_file_count}, "
            f"dirs={directory_count}, symlinks={symlink_count}, "
            f"special={special_member_count}"
        )
    if result_time_dirs != set(EXPECTED_RESULT_TIMES):
        raise AuditBlocked(f"结果时刻目录漂移：{sorted(result_time_dirs)}")
    if set(field_files_by_time) != set(EXPECTED_SCALAR_TIMES):
        raise AuditBlocked("结果字段文件的时刻集合漂移")
    if any(len(fields) != 54 for fields in field_files_by_time.values()):
        raise AuditBlocked("每个结果时刻应有 54 个字段文件")
    if class_counts != {
        "volScalarField": 2_600,
        "surfaceScalarField": 50,
        "volVectorField": 50,
    }:
        raise AuditBlocked(f"OpenFOAM 字段 class 计数漂移：{dict(class_counts)}")
    if any(
        len(fields) != EXPECTED_VOL_SCALAR_FIELDS_PER_TIME
        for fields in vol_scalar_files_by_time.values()
    ):
        raise AuditBlocked("每个时刻应有 52 个 volScalarField")
    scalar_field_sets = {frozenset(fields) for fields in vol_scalar_files_by_time.values()}
    if len(scalar_field_sets) != 1:
        raise AuditBlocked("50 个时刻的 volScalarField 集合不一致")
    if uniform_file_count != EXPECTED_UNIFORM_FIELD_FILES:
        raise AuditBlocked(f"uniform 字段文件数漂移：{uniform_file_count}")
    if nonuniform_file_count != EXPECTED_NONUNIFORM_FIELD_FILES:
        raise AuditBlocked(f"nonuniform 字段文件数漂移：{nonuniform_file_count}")

    if control_text is None or boundary_text is None or readme_text is None:
        raise AuditBlocked("缺少判定单一 2D mixing-cup case 所需的控制或网格证据")
    if not (
        re.search(r"\bstartTime\s+0\s*;", control_text)
        and re.search(r"\bendTime\s+50\s*;", control_text)
        and re.search(r"\bapplication\s+QmomKinetics\s*;", control_text)
        and re.search(r"frontAndBack\s*\{[^}]*\btype\s+empty\s*;", boundary_text, re.DOTALL)
        and "polyurethane foams" in readme_text
        and "n-pentane" in readme_text
        and "QMOM" in readme_text
    ):
        raise AuditBlocked("单一 2D 反应性 PU mixing-cup case 证据不闭合")
    if cell_source_text is None:
        raise AuditBlocked("缺少 cellSource.dat")
    native_records = _parse_cell_source(cell_source_text)

    rows: list[dict[str, str]] = []
    policy_tier_counts: Counter[str] = Counter()
    for time_s, field, value in native_records:
        unit, unit_status = CELL_SOURCE_UNITS[field]
        if field in NATIVE_RESOLVED_FIELDS:
            policy_tier = POLICY_TIER_NATIVE_RESOLVED
            fidelity_level = "reactive_CFD_source_native_model_level_validated"
            gold_admission_status = "admitted_reference"
            source_validation_status = (
                "model_level_validation_reported_not_field_specific"
            )
            potential_weight_ceiling = "0.30"
        else:
            policy_tier = POLICY_TIER_NATIVE_UNRESOLVED
            fidelity_level = "reactive_CFD_source_native_semantics_unresolved"
            gold_admission_status = "conditional_reference"
            source_validation_status = (
                "model_level_validation_reported_field_semantics_unresolved"
            )
            potential_weight_ceiling = "0.10"
        rows.append(
            _base_row(
                time_s=time_s,
                field=field,
                aggregation="volume_average",
                value=value,
                unit=unit,
                unit_status=unit_status,
                member_name=CELL_SOURCE_MEMBER,
                record_role="source_native_volume_average",
                method_detail=(
                    "OpenFOAM 3.0.1 cellSource volAverage over all 800 cells"
                ),
                fidelity_level=fidelity_level,
                gold_admission_status=gold_admission_status,
                source_validation_status=source_validation_status,
                potential_weight_ceiling=potential_weight_ceiling,
            )
        )
        policy_tier_counts[policy_tier] += 1

    derived_row_count = 0
    aggregation_counts: Counter[str] = Counter()
    for time_s in EXPECTED_SCALAR_TIMES:
        for field in sorted(vol_scalar_files_by_time[time_s]):
            dimensions, values, member_name = field_payloads[(time_s, field)]
            unit = DIMENSION_TO_UNIT[dimensions]
            if len(values) == 1:
                statistics_to_write = (("mean", values[0]),)
            else:
                statistics_to_write = (
                    ("mean", statistics.fmean(values)),
                    ("min", min(values)),
                    ("max", max(values)),
                    ("population_std", statistics.pstdev(values)),
                )
            for aggregation, value in statistics_to_write:
                if aggregation == "mean" and field in DUPLICATE_MEAN_FIELDS:
                    continue
                if field in SEMANTICS_UNCLOSED_DERIVED_FIELDS:
                    policy_tier = POLICY_TIER_DERIVED_UNCLOSED
                    unit_status = (
                        "source_declared_dimensions_semantics_unresolved"
                    )
                    fidelity_level = "reactive_CFD_derived_semantics_unresolved"
                    gold_admission_status = "conditional_reference"
                    source_validation_status = (
                        "derived_model_output_field_semantics_unresolved"
                    )
                    potential_weight_ceiling = "0.10"
                else:
                    policy_tier = POLICY_TIER_DERIVED_RESOLVED
                    unit_status = "resolved_from_openfoam_dimensions"
                    fidelity_level = "reactive_CFD_derived_model_output"
                    gold_admission_status = "admitted_reference"
                    source_validation_status = (
                        "derived_model_output_model_level_validation_only"
                    )
                    potential_weight_ceiling = "0.20"
                rows.append(
                    _base_row(
                        time_s=time_s,
                        field=field,
                        aggregation=aggregation,
                        value=value,
                        unit=unit,
                        unit_status=unit_status,
                        member_name=member_name,
                        record_role="derived_spatial_summary",
                        method_detail=(
                            "OpenFOAM 3.0.1 volScalarField internalField; "
                            f"aggregation={aggregation}"
                        ),
                        fidelity_level=fidelity_level,
                        gold_admission_status=gold_admission_status,
                        source_validation_status=source_validation_status,
                        potential_weight_ceiling=potential_weight_ceiling,
                    )
                )
                policy_tier_counts[policy_tier] += 1
                aggregation_counts[aggregation] += 1
                derived_row_count += 1

    if derived_row_count != EXPECTED_DERIVED_ROWS:
        raise AuditBlocked(
            f"派生空间统计行数漂移：expected={EXPECTED_DERIVED_ROWS}, "
            f"actual={derived_row_count}"
        )
    if len(rows) != EXPECTED_TOTAL_ROWS:
        raise AuditBlocked(
            f"Gold-C 行数漂移：expected={EXPECTED_TOTAL_ROWS}, actual={len(rows)}"
        )
    if policy_tier_counts != Counter(EXPECTED_POLICY_TIER_COUNTS):
        raise AuditBlocked(
            "字段级准入分层计数漂移："
            f"expected={EXPECTED_POLICY_TIER_COUNTS}, "
            f"actual={dict(policy_tier_counts)}"
        )
    if len({row["observation_id"] for row in rows}) != len(rows):
        raise AuditBlocked("observation_id 不唯一")
    if len({row["source_record_id"] for row in rows}) != len(rows):
        raise AuditBlocked("source_record_id 不唯一")

    unit_counts = Counter(row["unit"] for row in rows)
    unit_status_counts = Counter(row["unit_status"] for row in rows)
    admission_status_counts = Counter(
        row["gold_admission_status"] for row in rows
    )
    validation_status_counts = Counter(
        row["source_validation_status"] for row in rows
    )
    potential_weight_ceiling_counts = Counter(
        row["potential_weight_ceiling"] for row in rows
    )
    audit_payload: dict[str, Any] = {
        "audit_version": "batch12-pufoam-v2",
        "source_id": SOURCE_ID,
        **metadata,
        "archive": {
            "filename": ARCHIVE_PATH.name,
            "bytes": ARCHIVE_BYTES,
            "sha256": ARCHIVE_SHA256,
            "member_count": member_count,
            "regular_file_count": regular_file_count,
            "directory_count": directory_count,
            "symlink_count": symlink_count,
            "special_member_count": special_member_count,
            "safe_relative_symlinks": sorted(symlinks, key=lambda row: row["member"]),
        },
        "case": {
            "case_count": 1,
            "case_id": SIMULATION_KEY,
            "geometry": "2D mixing-cup",
            "result_time_directory_count": len(result_time_dirs),
            "result_times_s": sorted(result_time_dirs),
            "scalar_result_times_s": list(EXPECTED_SCALAR_TIMES),
            "cells": 800,
            "vol_scalar_field_count_per_time": EXPECTED_VOL_SCALAR_FIELDS_PER_TIME,
            "uniform_field_file_count": uniform_file_count,
            "nonuniform_field_file_count": nonuniform_file_count,
        },
        "materialization": {
            "native_volume_average_count": EXPECTED_NATIVE_ROWS,
            "derived_spatial_statistic_count": derived_row_count,
            "total_gold_c_count": len(rows),
            "aggregation_counts_derived": dict(sorted(aggregation_counts.items())),
            "policy_tier_counts": dict(sorted(policy_tier_counts.items())),
            "gold_admission_status_counts": dict(
                sorted(admission_status_counts.items())
            ),
            "source_validation_status_counts": dict(
                sorted(validation_status_counts.items())
            ),
            "potential_weight_ceiling_counts": dict(
                sorted(potential_weight_ceiling_counts.items())
            ),
            "unit_counts": dict(sorted(unit_counts.items())),
            "unit_status_counts": dict(sorted(unit_status_counts.items())),
            "simulation_key_count": 1,
            "time_points_are_independent_systems": False,
            "current_weight_materialized": False,
        },
        "citations": CITATION_KEYS.split(";"),
    }
    return tuple(rows), audit_payload


def build_gold_c_rows() -> list[dict[str, str]]:
    """返回 9,014 行字段契约固定、顺序确定的 Gold-C 过程观测。"""

    rows, _ = _materialize()
    return [dict(row) for row in rows]


def audit() -> dict[str, Any]:
    """返回可 JSON 序列化且无运行时戳的确定性审计摘要。"""

    _, payload = _materialize()
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=GOLD_C_VALUE_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_checksums(path: Path) -> None:
    input_paths = (ARCHIVE_PATH, OFFICIAL_METADATA_PATH, CROSSREF_METADATA_PATH)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("filename", "bytes", "sha256", "role"),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for input_path in input_paths:
            writer.writerow(
                {
                    "filename": input_path.name,
                    "bytes": input_path.stat().st_size,
                    "sha256": _sha256(input_path),
                    "role": (
                        "frozen_primary_archive"
                        if input_path == ARCHIVE_PATH
                        else "source_metadata"
                    ),
                }
            )


def _source_readme() -> str:
    return f"""# PUFoam 来源说明

本目录保存一个公开的反应性聚氨酯发泡 CFD-PBE-QMOM 参考算例。归档通过
`tarfile` 顺序读取并执行路径安全门禁，没有解压到文件系统。它只包含 **1 个**
2D mixing-cup 计算体系；0--50 s 的 51 个结果目录是同一模拟的时间演化，不能
当作 51 个独立材料体系。

## 纳入 Gold-C 的数据

- `cellSource.dat`：50 × 5 = {EXPECTED_NATIVE_ROWS} 个源生体积平均值；
- 1--50 s 的 52 个 `volScalarField`：{EXPECTED_DERIVED_ROWS} 个可复算的
  mean/min/max/population_std 空间统计；
- 总计 {EXPECTED_TOTAL_ROWS} 行，全部共享 `{SIMULATION_KEY}`；
- 150 个单位闭合的源生体积平均值正式准入，上限 0.30；100 个源生 PBE
  矩条件准入，上限 0.10；
- 4,143 个维度与语义闭合的派生统计正式准入，上限 0.20；4,621 个 PBE
  矩、nodes/weights、Psi 及其他语义未闭合统计条件准入，上限 0.10；
- 所有训练权重保持为空；论文的 12 组实验仅作为模型级验证证据，不宣称每个
  场变量都得到实验直接验证；
- `mZero`、`mOne` 在 `cellSource.dat` 中没有显式单位，保留为
  `source_native_unit_unresolved`，不擅自补单位。

数据集页面声明 GNU GPLv3。该算例适合作为反应、密度、温度、黏度和泡孔演化
的低权重多保真参考，不提供确定的单体结构，因此 `canonical_structure` 留空。

## 参考文献

[1] Karimi, M.; Droghetti, H.; Marchisio, D. L. *PUFoam: a novel open-source
CFD solver for the simulation of expanding and reacting polyurethane foams*.
Mendeley Data, Version 1, 2017. https://doi.org/{SOURCE_DOI}

[2] Karimi, M.; Droghetti, H.; Marchisio, D. L. PUFoam: A novel open-source
CFD solver for the simulation of polyurethane foams. *Computer Physics
Communications* **2017**, *217*, 138--148.
https://doi.org/{PAPER_DOI}
"""


def main() -> None:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    rows = build_gold_c_rows()
    payload = audit()
    _write_tsv(OUTPUT_TSV, rows)
    OUTPUT_AUDIT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_checksums(OUTPUT_CHECKSUMS)
    OUTPUT_README.write_text(_source_readme(), encoding="utf-8")
    print(
        json.dumps(
            {
                "gold_c_rows": len(rows),
                "native_rows": EXPECTED_NATIVE_ROWS,
                "derived_rows": EXPECTED_DERIVED_ROWS,
                "simulation_count": 1,
                "output": str(OUTPUT_TSV),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
