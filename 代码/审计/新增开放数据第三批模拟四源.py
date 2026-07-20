"""离线复算 TPU 数据库第三批四个开放模拟来源。

覆盖来源：反应型粗粒化聚脲固化、NIPU 反应路径 DFT/MD、PCL 软段
构象粗粒化 MD、PTMO/MDI/BDO 聚氨酯冲击 MD。本脚本不联网、不运行
模拟、不创建训练集、不调整训练权重；所有结论均从固定本地输入复算。

审计包含顶层哈希、ZIP CRC/路径/压缩硬门、嵌套 TAR/GZIP、Gaussian
频率与 Gibbs 势垒、Git LFS 指针、强相关时间点、LAMMPS 原子/链/组成/
密度与运行配方。只有运行前后输入哈希完全一致，才原子写入白名单输出。

运行：

    python 代码/审计/新增开放数据第三批模拟四源.py
"""

from __future__ import annotations

import base64
import bz2
import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import struct
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "数据/原始" / "外部数据" / "新增开放数据"
AUDIT_DATE = "2026-07-20"
AUDIT_VERSION = "1.1"

POLYUREA = "Zenodo_反应型粗粒化聚脲固化"
NIPU = "Zenodo_NIPU反应路径DFT与MD"
PCL = "Zenodo_PCL软段构象粗粒化MD"
IMPACT = "Zenodo_PTMO_MDI_BDO聚氨酯冲击MD"
SOURCE_NAMES = (POLYUREA, NIPU, PCL, IMPACT)

METADATA_NAME = "官方API元数据.json"
MANIFEST_NAME = "官方文件清单.tsv"
OUTPUT_NAMES = ("内容审计摘要.json", "文件校验清单.tsv", "计算观测清单.tsv")
OUTPUT_WHITELIST = frozenset(
    DATA_ROOT / source / filename
    for source in SOURCE_NAMES
    for filename in OUTPUT_NAMES
)

ALLOWED_PROVENANCE_HOSTS = frozenset(
    {"zenodo.org", "raw.githubusercontent.com", "api.github.com"}
)
MAX_COMPRESSION_RATIO = 5_000.0
MAX_ZIP_UNCOMPRESSED_BYTES = 1_000_000_000
MAX_TAR_UNCOMPRESSED_BYTES = 100_000_000
HARTREE_TO_KCAL_MOL = 627.509474


@dataclass(frozen=True)
class Fingerprint:
    size: int
    md5: str | None
    sha256: str | None = None


NIPU_FINGERPRINTS = {
    "ReadMeFile.txt": Fingerprint(7_982, "37e4c895cb483cf7ed6267d3a407fe27"),
    "VZ4_001_021_VSCHT_D_0001_v1.lmp": Fingerprint(3_592, "2e881a2d6771d74a1a8a32dd9beb6324"),
    "VZ4_001_021_VSCHT_D_0002_v1.lmp": Fingerprint(3_880, "8d4a8da5fe94c618dd4277fb96aed492"),
    "VZ4_001_021_VSCHT_D_0003_v1.lmp": Fingerprint(3_918, "7abb7c11c18042182867d0b802b4e947"),
    "VZ4_001_021_VSCHT_D_0004_v1.lmp": Fingerprint(4_134, "33e00670d1159f6e87664d719691372e"),
    "VZ4_001_021_VSCHT_D_0005_v1.lmp": Fingerprint(3_761, "c96f7ca044ce4677fe3360b14476fb16"),
    "VZ4_001_021_VSCHT_D_0006_v1.lmp": Fingerprint(23_165, "e8f42303749c25c78a3d042b61c8fc0b"),
    "VZ4_001_021_VSCHT_D_0007_v1.lmp": Fingerprint(24_681, "fe8ec93623850762fe41e8623838b291"),
    "VZ4_001_021_VSCHT_D_0008_v1.lmp": Fingerprint(32_875, "061ee51205e93bc99d1a160bc76202b8"),
    "VZ4_001_021_VSCHT_D_0009_v1.lmp": Fingerprint(33_933, "04e9309eabb5843d274bdbd64f9b1f31"),
    "VZ4_001_021_VSCHT_D_0010_v1.lmp": Fingerprint(33_680, "79670787f5210959a43afe98a7824b09"),
    "VZ4_001_021_VSCHT_D_0011_v1.log": Fingerprint(1_459_677, "2b971fff759102311456006eb0534edf"),
    "VZ4_001_021_VSCHT_D_0012_v1.log": Fingerprint(1_734_740, "b7e7ce81687e32a66e2a79d2269a42fb"),
    "VZ4_001_021_VSCHT_D_0013_v1.log": Fingerprint(508_097, "6cb1cf913b636e147832e69bacc75fc2"),
    "VZ4_001_021_VSCHT_D_0014_v1.log": Fingerprint(1_425_747, "9e7a6cc2f578d359b2c2e845443ae5ec"),
    "VZ4_001_021_VSCHT_D_0015_v1.log": Fingerprint(2_126_237, "9769f6a2527cc50f063c047359808e97"),
    "VZ4_001_021_VSCHT_D_0016_v1.log": Fingerprint(3_971_635, "17b701d62656d4d822d53042353de9f5"),
    "VZ4_001_021_VSCHT_D_0017_v1.log": Fingerprint(490_783, "e1a949ade91046d2b60572bda1afe3cf"),
    "VZ4_001_021_VSCHT_D_0018_v1.log": Fingerprint(2_453_368, "bc1581ef455ce03c5443fbf1c69fae0f"),
    "VZ4_001_021_VSCHT_D_0019_v1.log": Fingerprint(9_042_455, "197b86628789b6fc066782f5e6da50b3"),
    "VZ4_001_021_VSCHT_D_0020_v1.log": Fingerprint(3_829_548, "61c3eef5b10a7c19f977ab404be8a964"),
    "VZ4_001_021_VSCHT_D_0021_v1.log": Fingerprint(1_941_192, "aeb32fc75a81aa004fe2e23db09036c7"),
    "VZ4_001_021_VSCHT_D_0022_v1.log": Fingerprint(21_696_793, "e83bf64900009ee211418aae115c8aea"),
    "VZ4_001_021_VSCHT_D_0023_v1.log": Fingerprint(5_854_923, "63b6f92eb1c6680dd95670a26259b834"),
    "VZ4_001_021_VSCHT_D_0024_v1.log": Fingerprint(24_972_151, "e67c8536235c5dadd6c807e408359e1e"),
    "VZ4_001_021_VSCHT_D_0025_v1.log": Fingerprint(6_859_922, "551f51cc4e9f4395db0c01d8e85fb33a"),
    "VZ4_001_021_VSCHT_D_0026_v1.log": Fingerprint(10_495_783, "17159c25cfb337de0906ef38505e10c1"),
    "VZ4_001_021_VSCHT_D_0027_v1.log": Fingerprint(17_393_206, "4daf38953fbb59d6fdd77c13a557b6e6"),
    "VZ4_001_021_VSCHT_D_0028_v1.log": Fingerprint(6_068_577, "fad130df9f47688eac71f100cb1358df"),
    "VZ4_001_021_VSCHT_D_0029_v1.log": Fingerprint(24_641_381, "72c49676953ddb58d446d42a225e3fc9"),
    "VZ4_001_021_VSCHT_D_0030_v1.log": Fingerprint(8_169_660, "d08dbec57ab475731336e0580af1b0d0"),
}

EXPECTED_SCIENTIFIC_FILES: dict[str, dict[str, Fingerprint]] = {
    POLYUREA: {
        "cg-polyurea-curing-v1.0.zip": Fingerprint(
            7_173_934,
            "35009e635573dff57a75d910afbf2302",
            "dc7771885f03c2729ded304d30bfcd42bf523ac656a92088a7627a26d856b2ea",
        ),
    },
    NIPU: NIPU_FINGERPRINTS,
    PCL: {
        "PCL_Supplementary_material_systematic_CG-v1.0_2.zip": Fingerprint(
            161_897_959,
            "b588fdf8a1afb1e76aaadeb2a53c1310",
            "5a59701e7a09f1f8b7907a0c9de70c86ffca05b4825812479b4ad4ad0a127002",
        ),
    },
    IMPACT: {
        "polyurethane_60nm.data": Fingerprint(
            246_265_627,
            "06a2a113ee25d108fd06057c5667c45e",
            "c518b4f5797e21cf9e79e77ead592ffc4ca12c76814c72085296bdb15ba3d376",
        ),
        "spall_in.in": Fingerprint(
            1_275,
            None,
            "5701606cd81ed341f1e62caf710bbb1cdcc3886643b24a202cd93187267629a3",
        ),
        "polyurethane_60nm.params": Fingerprint(
            31_334,
            None,
            "776dc1f0686b0b8b5b2f03023b104b93327cf6c50fc22437335ee53c14e867db",
        ),
    },
}

EXPECTED_METADATA = {
    POLYUREA: (7_811_383, 7_811_382, "10.5281/zenodo.7811383", "v1.0", "other-open", 1),
    NIPU: (10_817_092, 10_817_091, "10.5281/zenodo.10817092", "version 1", "cc-by-4.0", 31),
    PCL: (17_790_918, 16_944_033, "10.5281/zenodo.17790918", "v1.0_2", "cc-by-4.0", 1),
    IMPACT: (5_099_589, 5_099_588, "10.5281/zenodo.5099589", None, "cc-by-4.0", 3),
}

PINNED_GIT_COMMIT = "46683548c90091a239745f49517a113b8be0268c"
PINNED_GIT_TREE = "4c9397f5f7d8dfadbe51ded155d1c2999d856322"
GIT_REPOSITORY = "nuwan-d/MD_model_JAM-21-1174"
EXPECTED_GIT_TREE = {
    "README.md": ("100644", "blob", "bd60762aa8f347180af9386bee24b73880464cd0", 2_344),
    "md_model.JPG": ("100644", "blob", "d380d343e1af1e3cb49639d9f4046c4203cfc315", 93_832),
    "polyurethane_60nm.params": ("100644", "blob", "07709542b1b54884086152982d1f2774accd424a", 31_334),
    "spall_in.in": ("100644", "blob", "c840e2be4e94b39aaed7a3230da9071fec9d52c9", 1_275),
    "sub.sh": ("100644", "blob", "83deec58f0b5e9ffc5d1cfcf8d4fb0e07f729e2e", 443),
    "x-t_density.JPG": ("100644", "blob", "dc67e2929b7c74864cf559912c825ee0fd6e0a26", 40_139),
    "x_t_density.m": ("100644", "blob", "e85abd0fd6c4ea28bb9005d060e1b1cac21778fe", 1_189),
}


class AuditBlocked(RuntimeError):
    """输入完整性、安全边界或科学语义不满足固定审计协议。"""


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right))
    )


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(attributes & flag) or bool(is_junction())


def _assert_plain_chain(path: Path, stop: Path) -> None:
    if path != stop and stop not in path.parents:
        raise AuditBlocked(f"路径越出审计根：{path}")
    cursor = path
    while True:
        if _is_reparse_point(cursor):
            raise AuditBlocked(f"拒绝符号链接或重解析点：{cursor}")
        if cursor == stop:
            return
        cursor = cursor.parent


def require_directory(path: Path) -> None:
    resolved = path.resolve(strict=True)
    if not path.is_dir() or not _same_path(resolved, path.absolute()):
        raise AuditBlocked(f"目录缺失、不是普通目录或经链接解析：{path}")
    _assert_plain_chain(path, PROJECT_ROOT)


def require_file(path: Path) -> None:
    resolved = path.resolve(strict=True)
    if not path.is_file() or not _same_path(resolved, path.absolute()):
        raise AuditBlocked(f"文件缺失、不是普通文件或经链接解析：{path}")
    _assert_plain_chain(path, PROJECT_ROOT)


def assert_output_allowed(path: Path) -> None:
    if path not in OUTPUT_WHITELIST:
        raise AuditBlocked(f"拒绝写入白名单以外路径：{path}")
    parent = path.parent
    require_directory(parent)
    if path.exists() or path.is_symlink():
        if _is_reparse_point(path) or not path.is_file():
            raise AuditBlocked(f"拒绝覆盖非普通审计输出：{path}")
        if not _same_path(path.resolve(strict=True), path.absolute()):
            raise AuditBlocked(f"审计输出经链接解析：{path}")


def atomic_write(path: Path, payload: bytes) -> None:
    assert_output_allowed(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".audit.tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if _is_reparse_point(temporary) or not temporary.is_file():
            raise AuditBlocked(f"审计临时输出不是普通文件：{temporary}")
        assert_output_allowed(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _hash_stream(handle: BinaryIO, algorithm: str = "sha256") -> str:
    if algorithm == "md5":
        digest = hashlib.md5(usedforsecurity=False)
    else:
        digest = hashlib.new(algorithm)
    for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    require_file(path)
    with path.open("rb") as handle:
        return _hash_stream(handle, algorithm)


def manifest_digest(items: Iterable[tuple[str, int, str]]) -> str:
    digest = hashlib.sha256()
    for relative, size, checksum in sorted(items):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(checksum.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def read_manifest(source: str) -> dict[str, dict[str, str]]:
    path = DATA_ROOT / source / MANIFEST_NAME
    require_file(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    mapping: dict[str, dict[str, str]] = {}
    for row in rows:
        name = row.get("local_filename", "")
        if not name or name in mapping:
            raise AuditBlocked(f"下载清单本地文件名缺失或重复：{source}/{name}")
        mapping[name] = row
    return mapping


def validate_raw_api_captures(
    metadata: dict[str, object], source: str, record_id: int
) -> None:
    if metadata.get("raw_api_capture_format") != "exact_response_bytes_base64_with_sha256":
        raise AuditBlocked(f"官方API未保存精确响应字节：{source}")
    captures = metadata.get("raw_api_captures")
    expected_count = 4 if source == IMPACT else 2
    if not isinstance(captures, list) or len(captures) != expected_count:
        raise AuditBlocked(f"官方API响应快照数量不符：{source}")
    decoded: dict[str, object] = {}
    for capture in captures:
        if not isinstance(capture, dict):
            raise AuditBlocked(f"官方API响应快照结构错误：{source}")
        request_url = str(capture.get("request_url", ""))
        final_url = str(capture.get("final_url", ""))
        request_parsed = urlsplit(request_url)
        final_parsed = urlsplit(final_url)
        if (
            request_url in decoded
            or request_parsed.scheme != "https"
            or final_parsed.scheme != "https"
            or request_parsed.hostname not in ALLOWED_PROVENANCE_HOSTS
            or request_parsed.hostname != final_parsed.hostname
            or int(capture.get("status", -1)) != 200
        ):
            raise AuditBlocked(f"官方API快照端点或状态异常：{source}/{request_url}")
        try:
            payload = base64.b64decode(str(capture.get("payload_base64", "")), validate=True)
            parsed_payload = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuditBlocked(f"官方API快照载荷不可复核：{source}/{request_url}") from exc
        if (
            len(payload) != int(capture.get("payload_bytes", -1))
            or hashlib.sha256(payload).hexdigest()
            != str(capture.get("payload_sha256", "")).lower()
        ):
            raise AuditBlocked(f"官方API快照字节或SHA256不符：{source}/{request_url}")
        decoded[request_url] = parsed_payload

    record_url = f"https://zenodo.org/api/records/{record_id}"
    record = decoded.get(record_url)
    if not isinstance(record, dict):
        raise AuditBlocked(f"Zenodo记录原始快照缺失：{source}")
    latest_url = str((record.get("links") or {}).get("latest", ""))
    required_urls = {record_url, latest_url}
    latest = decoded.get(latest_url)
    if not isinstance(latest, dict) or int(latest.get("id", -1)) != record_id:
        raise AuditBlocked(f"Zenodo最新版原始快照不闭合：{source}")
    if source == IMPACT:
        required_urls.update(
            {
                f"https://api.github.com/repos/{GIT_REPOSITORY}/commits/{PINNED_GIT_COMMIT}",
                f"https://api.github.com/repos/{GIT_REPOSITORY}/git/trees/{PINNED_GIT_TREE}?recursive=1",
            }
        )
    if set(decoded) != required_urls:
        raise AuditBlocked(f"官方API快照端点集合漂移：{source}")


def validate_metadata_and_manifest(source: str) -> None:
    directory = DATA_ROOT / source
    metadata_path = directory / METADATA_NAME
    require_file(metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    record_id, concept_id, doi, version, license_id, row_count = EXPECTED_METADATA[source]
    validate_raw_api_captures(metadata, source, record_id)
    if (
        metadata.get("record_id") != record_id
        or metadata.get("concept_record_id") != concept_id
        or metadata.get("doi") != doi
        or metadata.get("version") != version
        or metadata.get("license") != license_id
        or metadata.get("latest_record_verified") is not True
        or metadata.get("training_allowed") is not False
    ):
        raise AuditBlocked(f"固定来源元数据漂移：{source}")
    serialized = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    if source == PCL and ("17790918" not in serialized or "17106011" in serialized):
        raise AuditBlocked("PCL 记录不是固定最新版或混入旧记录 17106011")

    manifest = read_manifest(source)
    if len(manifest) != row_count or set(manifest) != set(EXPECTED_SCIENTIFIC_FILES[source]):
        raise AuditBlocked(f"下载清单文件集合漂移：{source}")
    for name, fingerprint in EXPECTED_SCIENTIFIC_FILES[source].items():
        row = manifest[name]
        path = directory / name
        if (
            row.get("source_directory") != source
            or int(row.get("bytes", "-1")) != fingerprint.size
            or row.get("training_allowed") != "false"
            or row.get("local_state") != "verified_present"
        ):
            raise AuditBlocked(f"下载清单字段漂移：{source}/{name}")
        if fingerprint.md5 is not None and row.get("official_md5") != fingerprint.md5:
            raise AuditBlocked(f"下载清单 MD5 漂移：{source}/{name}")
        local_sha256 = row.get("local_sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", local_sha256):
            raise AuditBlocked(f"下载清单本地 SHA256 非法：{source}/{name}")
        if file_hash(path) != local_sha256:
            raise AuditBlocked(f"实际文件与本地 SHA256 清单不一致：{source}/{name}")
        url = row.get("download_url", "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_PROVENANCE_HOSTS:
            raise AuditBlocked(f"来源 URL 越出白名单：{source}/{name}")

    if source == IMPACT:
        auxiliary = metadata.get("auxiliary_git_inputs") or {}
        observed_tree = {
            str(item.get("path", "")): (
                str(item.get("mode", "")),
                str(item.get("type", "")),
                str(item.get("git_sha", "")),
                int(item.get("bytes", -1)),
            )
            for item in (auxiliary.get("tree_entries") or [])
            if isinstance(item, dict)
        }
        if (
            auxiliary.get("repository") != GIT_REPOSITORY
            or auxiliary.get("commit") != PINNED_GIT_COMMIT
            or auxiliary.get("tree") != PINNED_GIT_TREE
            or observed_tree != EXPECTED_GIT_TREE
            or auxiliary.get("license_candidates") != []
            or auxiliary.get("license_status")
            != "no LICENSE/COPYING file detected in pinned tree"
            or auxiliary.get("redistribution_assumption") != "do not assume redistribution permission"
        ):
            raise AuditBlocked("冲击模型 Git 辅助输入许可证或固定提交声明漂移")
        for name in ("spall_in.in", "polyurethane_60nm.params"):
            row = manifest[name]
            if (
                row.get("provider") != "GitHub_raw"
                or row.get("record_id_or_commit") != PINNED_GIT_COMMIT
                or row.get("license") != "no-license-detected"
                or "禁止假定再分发" not in row.get("redistribution_assumption", "")
            ):
                raise AuditBlocked(f"Git 辅助输入再分发边界漂移：{name}")


def scientific_input_snapshot() -> dict[str, tuple[int, str]]:
    snapshot: dict[str, tuple[int, str]] = {}
    for source in SOURCE_NAMES:
        directory = DATA_ROOT / source
        require_directory(directory)
        expected_names = set(EXPECTED_SCIENTIFIC_FILES[source]) | {METADATA_NAME, MANIFEST_NAME}
        actual_names: set[str] = set()
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if _is_reparse_point(path):
                raise AuditBlocked(f"来源根含链接或重解析点：{path}")
            if path in OUTPUT_WHITELIST:
                continue
            if not path.is_file():
                raise AuditBlocked(f"来源根出现未登记目录或特殊对象：{path}")
            if path.name.endswith((".part", ".tmp", ".audit.tmp")):
                raise AuditBlocked(f"来源根出现未完成临时文件：{path}")
            actual_names.add(path.name)
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            snapshot[relative] = (path.stat().st_size, file_hash(path))
        if actual_names != expected_names:
            raise AuditBlocked(
                f"{source}本地输入集合漂移：缺失={sorted(expected_names-actual_names)}，"
                f"多余={sorted(actual_names-expected_names)}"
            )
        validate_metadata_and_manifest(source)
        for name, fingerprint in EXPECTED_SCIENTIFIC_FILES[source].items():
            path = directory / name
            if path.stat().st_size != fingerprint.size:
                raise AuditBlocked(f"固定文件大小不匹配：{source}/{name}")
            if fingerprint.md5 is not None and file_hash(path, "md5") != fingerprint.md5:
                raise AuditBlocked(f"固定文件 MD5 不匹配：{source}/{name}")
            if fingerprint.sha256 is not None and file_hash(path) != fingerprint.sha256:
                raise AuditBlocked(f"固定文件 SHA256 不匹配：{source}/{name}")
    return snapshot


def snapshot_by_source(snapshot: dict[str, tuple[int, str]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for source in SOURCE_NAMES:
        marker = f"/新增开放数据/{source}/"
        rows = [
            (path, size, checksum)
            for path, (size, checksum) in snapshot.items()
            if marker in f"/{path}"
        ]
        result[source] = {
            "文件数": len(rows),
            "总字节数": sum(size for _, size, _ in rows),
            "清单SHA256": manifest_digest(rows),
        }
    return result


def safe_member_name(name: str, container: str) -> str:
    if "\x00" in name or re.match(r"^[A-Za-z]:", name):
        raise AuditBlocked(f"{container}含危险成员路径：{name!r}")
    candidate = PurePosixPath(name.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AuditBlocked(f"{container}含危险成员路径：{name!r}")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise AuditBlocked(f"{container}含空成员路径：{name!r}")
    return normalized


def audit_zip(
    source: str,
    path: Path,
    expected_entries: int,
    expected_files: int,
    expected_uncompressed: int,
) -> tuple[zipfile.ZipFile, list[dict[str, object]]]:
    require_file(path)
    archive = zipfile.ZipFile(path)
    try:
        bad = archive.testzip()
        if bad is not None:
            raise AuditBlocked(f"ZIP CRC 失败：{path.name}/{bad}")
        infos = archive.infolist()
        normalized = [safe_member_name(info.filename, "ZIP") for info in infos]
        duplicates = [name for name, count in Counter(normalized).items() if count > 1]
        if duplicates:
            raise AuditBlocked(f"ZIP 含重复成员名：{path.name}/{duplicates}")
        file_count = 0
        total_uncompressed = 0
        rows: list[dict[str, object]] = []
        for name, info in zip(normalized, infos):
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise AuditBlocked(f"ZIP 含符号链接：{path.name}/{name}")
            if info.flag_bits & 0x1:
                raise AuditBlocked(f"ZIP 含加密成员：{path.name}/{name}")
            is_directory = info.is_dir() or name.endswith("/")
            if not is_directory:
                file_count += 1
                total_uncompressed += info.file_size
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > MAX_COMPRESSION_RATIO:
                    raise AuditBlocked(f"ZIP 成员压缩比超过上限：{path.name}/{name}")
            rows.append(
                {
                    "来源": source,
                    "容器或目录": path.name,
                    "成员": name,
                    "字节": info.file_size,
                    "压缩字节": info.compress_size,
                    "CRC32": f"{info.CRC:08x}",
                    "SHA256": "",
                    "角色": "目录" if is_directory else "ZIP成员",
                    "状态": "通过",
                }
            )
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise AuditBlocked(f"ZIP 总解压量超过硬上限：{path.name}")
        if (len(infos), file_count, total_uncompressed) != (
            expected_entries,
            expected_files,
            expected_uncompressed,
        ):
            raise AuditBlocked(
                f"ZIP 固定形状漂移：{path.name}="
                f"{(len(infos), file_count, total_uncompressed)}"
            )
        return archive, rows
    except Exception:
        archive.close()
        raise


def audit_tar_bytes(
    source: str,
    container_name: str,
    payload: bytes,
    expected_members: int,
    expected_files: int,
    expected_uncompressed: int,
) -> tuple[tarfile.TarFile, list[dict[str, object]]]:
    archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz")
    try:
        members = archive.getmembers()
        normalized = [safe_member_name(member.name, "TAR") for member in members]
        duplicates = [name for name, count in Counter(normalized).items() if count > 1]
        if duplicates:
            raise AuditBlocked(f"TAR 含重复成员名：{container_name}/{duplicates}")
        rows: list[dict[str, object]] = []
        file_count = 0
        total = 0
        for name, member in zip(normalized, members):
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise AuditBlocked(f"TAR 含链接或特殊成员：{container_name}/{name}")
            if member.isfile():
                file_count += 1
                total += member.size
            elif not member.isdir():
                raise AuditBlocked(f"TAR 含不支持成员类型：{container_name}/{name}")
            rows.append(
                {
                    "来源": source,
                    "容器或目录": container_name,
                    "成员": name,
                    "字节": member.size,
                    "压缩字节": "",
                    "CRC32": "",
                    "SHA256": "",
                    "角色": "目录" if member.isdir() else "TAR成员",
                    "状态": "通过",
                }
            )
        if total > MAX_TAR_UNCOMPRESSED_BYTES:
            raise AuditBlocked(f"TAR 总解压量超过硬上限：{container_name}")
        if (len(members), file_count, total) != (
            expected_members,
            expected_files,
            expected_uncompressed,
        ):
            raise AuditBlocked(
                f"TAR 固定形状漂移：{container_name}="
                f"{(len(members), file_count, total)}"
            )
        return archive, rows
    except Exception:
        archive.close()
        raise


def top_level_file_rows(source: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, fingerprint in sorted(EXPECTED_SCIENTIFIC_FILES[source].items()):
        path = DATA_ROOT / source / name
        rows.append(
            {
                "来源": source,
                "容器或目录": "本地来源目录",
                "成员": name,
                "字节": fingerprint.size,
                "压缩字节": "",
                "CRC32": "",
                "SHA256": file_hash(path),
                "角色": "固定科学输入" if name not in {"spall_in.in", "polyurethane_60nm.params"} else "固定提交辅助输入",
                "状态": "通过",
            }
        )
    return rows


def source_reference(source: str) -> str:
    metadata = json.loads((DATA_ROOT / source / METADATA_NAME).read_text(encoding="utf-8"))
    creators = ", ".join(
        str(item.get("name")) for item in metadata.get("creators") or [] if item.get("name")
    )
    year = str(metadata.get("publication_date") or "")[:4]
    title = str(metadata.get("title") or "")
    version = metadata.get("version")
    version_text = f" ({version})" if version else ""
    return f"{creators}. {title}{version_text}. Zenodo, {year}. https://doi.org/{metadata['doi']}"


def governance(note: str) -> dict[str, object]:
    return {
        "training_allowed": False,
        "training_split_created": False,
        "training_weight_materialized": False,
        "simulation_weight_adjusted": False,
        "independent_material_identity_created": False,
        "policy": "模拟保真度单独分层；只有与实验身份和端点对齐后才可在后续治理中赋权。",
        "note": note,
    }


def _single_name(names: Iterable[str], suffix: str, label: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    if len(matches) != 1:
        raise AuditBlocked(f"{label}成员数量不是1：{suffix} -> {matches}")
    return matches[0]


def _extract_tar_regular_file(archive: tarfile.TarFile) -> tuple[str, bytes]:
    files = [member for member in archive.getmembers() if member.isfile()]
    if len(files) != 1:
        raise AuditBlocked("嵌套 LAMMPS TAR 不只含一个普通文件")
    handle = archive.extractfile(files[0])
    if handle is None:
        raise AuditBlocked("无法读取嵌套 LAMMPS 文件")
    with handle:
        return files[0].name, handle.read()


def parse_lammps_topology(payload: bytes) -> dict[str, object]:
    text = payload.decode("ascii")
    counts: dict[str, int] = {}
    for label in ("atoms", "bonds", "angles"):
        match = re.search(rf"^\s*(\d+)\s+{label}\s*$", text, re.MULTILINE)
        if match is None:
            raise AuditBlocked(f"LAMMPS 拓扑缺 {label} 计数")
        counts[label] = int(match.group(1))
    for label in ("atom types", "bond types", "angle types"):
        match = re.search(rf"^\s*(\d+)\s+{re.escape(label)}\s*$", text, re.MULTILINE)
        if match is None:
            raise AuditBlocked(f"LAMMPS 拓扑缺 {label} 计数")
        counts[label] = int(match.group(1))

    lines = text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip().startswith("Atoms"))
    except StopIteration as exc:
        raise AuditBlocked("LAMMPS 拓扑缺 Atoms 段") from exc
    atom_ids: set[int] = set()
    molecule_counts: Counter[int] = Counter()
    type_counts: Counter[int] = Counter()
    started = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            if started and len(atom_ids) >= counts["atoms"]:
                break
            continue
        tokens = stripped.split()
        if not tokens[0].isdigit():
            if started:
                break
            continue
        if len(tokens) < 3:
            raise AuditBlocked("LAMMPS Atoms 行字段不足")
        started = True
        atom_id, molecule_id, atom_type = map(int, tokens[:3])
        if atom_id in atom_ids:
            raise AuditBlocked(f"LAMMPS 原子 ID 重复：{atom_id}")
        atom_ids.add(atom_id)
        molecule_counts[molecule_id] += 1
        type_counts[atom_type] += 1
    if len(atom_ids) != counts["atoms"]:
        raise AuditBlocked(f"LAMMPS Atoms 复算数量不符：{len(atom_ids)}/{counts['atoms']}")
    return {
        "counts": counts,
        "atom_id_min": min(atom_ids),
        "atom_id_max": max(atom_ids),
        "molecule_count": len(molecule_counts),
        "molecule_size_distribution": dict(sorted(Counter(molecule_counts.values()).items())),
        "atom_type_counts": dict(sorted(type_counts.items())),
    }


def audit_polyurea() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    path = DATA_ROOT / POLYUREA / "cg-polyurea-curing-v1.0.zip"
    archive, file_rows = audit_zip(POLYUREA, path, 9, 8, 7_183_071)
    try:
        names = [info.filename.replace("\\", "/") for info in archive.infolist()]
        roots = {PurePosixPath(name).parts[0] for name in names}
        if roots != {"liuminghao0830-cg-polyurea-curing-7f9975d"}:
            raise AuditBlocked(f"聚脲归档固定 Git 根漂移：{roots}")
        license_name = _single_name(names, "/LICENSE", "聚脲许可证")
        license_payload = archive.read(license_name)
        license_sha = hashlib.sha256(license_payload).hexdigest()
        if license_sha != "f7a8cba9567929ada1483306a178a1d6f50a5517471ab7cd61d051426eed848c":
            raise AuditBlocked("聚脲归档内 MIT LICENSE 哈希漂移")
        if b"MIT License" not in license_payload:
            raise AuditBlocked("聚脲归档内许可证不再是 MIT")

        lammps_container = _single_name(names, ".lammps.gz", "聚脲初始拓扑")
        lammps_tar, nested_rows = audit_tar_bytes(
            POLYUREA,
            PurePosixPath(lammps_container).name,
            archive.read(lammps_container),
            1,
            1,
            16_303_200,
        )
        try:
            lammps_name, lammps_payload = _extract_tar_regular_file(lammps_tar)
        finally:
            lammps_tar.close()
        topology = parse_lammps_topology(lammps_payload)
        expected_counts = {
            "atoms": 80_621,
            "bonds": 72_820,
            "angles": 65_019,
            "atom types": 7,
            "bond types": 8,
            "angle types": 8,
        }
        if topology["counts"] != expected_counts:
            raise AuditBlocked(f"聚脲初始拓扑计数漂移：{topology['counts']}")
        if (
            topology["molecule_count"] != 7_801
            or topology["molecule_size_distribution"] != {4: 4_000, 17: 3_800, 21: 1}
            or topology["atom_type_counts"]
            != {1: 1, 2: 8_002, 3: 7_602, 4: 7_601, 5: 8_001, 6: 49_413, 7: 1}
        ):
            raise AuditBlocked("聚脲初始拓扑分子或原子类型复算漂移")
        nested_rows[0]["SHA256"] = hashlib.sha256(lammps_payload).hexdigest()
        nested_rows[0]["成员"] = lammps_name
        nested_rows[0]["角色"] = "反应型粗粒化初始拓扑"

        potential_name = _single_name(names, "/potentials.tar.gz", "聚脲势函数")
        potential_tar, potential_rows = audit_tar_bytes(
            POLYUREA,
            PurePosixPath(potential_name).name,
            archive.read(potential_name),
            37,
            36,
            1_265_248,
        )
        try:
            potential_files = [member.name for member in potential_tar.getmembers() if member.isfile()]
        finally:
            potential_tar.close()
        if len(potential_files) != 36:
            raise AuditBlocked("聚脲势函数普通文件数不是 36")
        for row in potential_rows:
            if row["角色"] == "TAR成员":
                row["角色"] = "粗粒化势函数"

        input_name = _single_name(names, "/in.lammps", "聚脲运行输入")
        input_text = archive.read(input_name).decode("ascii")
        timestep_matches = re.findall(r"^\s*timestep\s+([0-9.]+)", input_text, re.MULTILINE)
        runs = [int(value) for value in re.findall(r"^\s*run\s+(\d+)", input_text, re.MULTILINE)]
        seeds = sorted({int(value) for value in re.findall(r"\b(?:create\s+300\.0|prob\s+0\.50)\s+(\d+)\b", input_text)})
        if timestep_matches != ["10"] or runs != [500, 500, 20_000_000] or seeds != [1_234, 12_345]:
            raise AuditBlocked("聚脲 LAMMPS timestep/run/seed 配方漂移")
        main_duration_ns = runs[-1] * float(timestep_matches[0]) / 1_000_000.0
        if main_duration_ns != 200.0:
            raise AuditBlocked("聚脲主固化模拟时长不再是 200 ns")
        expected_outputs = {
            "bonding-unwrap.lammpstrj",
            "bonding-wrap.lammpstrj",
            "local_bond.lammpstrj",
            "post-bonding-200ns.lammps",
        }
        archived_basenames = {PurePosixPath(name).name for name in names}
        distributed_outputs = sorted(expected_outputs & archived_basenames)
        if distributed_outputs:
            raise AuditBlocked(f"聚脲归档意外包含运行输出：{distributed_outputs}")
    finally:
        archive.close()

    file_rows = top_level_file_rows(POLYUREA) + file_rows + nested_rows + potential_rows
    observations = [
        {
            "来源": POLYUREA,
            "体系或路径": "3800个V形单体+4000个异氰酸酯+1条预聚脲",
            "记录层级": "单一模拟配方/初始拓扑",
            "观测或计算": "反应型粗粒化固化主运行",
            "数值": 200.0,
            "单位": "ns",
            "相关性": "单一运行；未分发轨迹或终态输出",
            "独立材料数": 0,
            "准入状态": "方法与候选生成可用；不可作为实验性能标签",
            "训练权重状态": "未赋权",
            "备注": "80621原子、7801分子、36个势函数文件；原子和时间步不计材料。",
        }
    ]
    summary = {
        "来源": POLYUREA,
        "引用": source_reference(POLYUREA),
        "审计版本": AUDIT_VERSION,
        "归档": {"条目": 9, "普通文件": 8, "未压缩字节": 7_183_071, "CRC": "通过"},
        "归档内许可证": {"类型": "MIT", "SHA256": license_sha},
        "固定Git快照": "7f9975d4e5692b49e84448a9d0f5a6a604cec00f",
        "初始拓扑": topology,
        "势函数普通文件数": 36,
        "主运行": {"timestep_fs": 10.0, "steps": 20_000_000, "duration_ns": 200.0, "seeds": seeds},
        "已分发运行输出": [],
        "独立材料数": 0,
        "数据定位": "一个体系的初始拓扑与可复现实验配方，不是高通量性能标签库。",
        "治理": governance("不得把 80621 个原子、时间步或未来轨迹帧拆成独立材料。"),
    }
    return summary, file_rows, observations


def _lammps_data_atom_count(text: str) -> int:
    match = re.search(r"^\s*(\d+)\s+atoms\s*$", text, re.MULTILINE)
    if match is None:
        raise AuditBlocked("NIPU LAMMPS data 文件缺原子计数")
    return int(match.group(1))


def _gaussian_profile(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="latin-1")
    gibbs_values = [
        float(value.replace("D", "E").replace("d", "e"))
        for value in re.findall(
            r"Sum of electronic and thermal Free Energies=\s*([-+0-9.DEded]+)", text
        )
    ]
    frequencies: list[float] = []
    for match in re.finditer(r"^\s*Frequencies\s+--\s+(.+)$", text, re.MULTILINE):
        frequencies.extend(float(value.replace("D", "E")) for value in match.group(1).split())
    if not gibbs_values or not frequencies or "SCF Done:" not in text:
        raise AuditBlocked(f"Gaussian 日志缺 Gibbs、频率或 SCF：{path.name}")
    lowered = text.casefold()
    compact = re.sub(r"\s+", "", lowered)
    if (
        "b3lyp/6-311+g(d,p)" not in compact
        or "empiricaldispersion=gd3bj" not in compact
        or "normal termination" not in lowered
        or "error termination" in lowered
    ):
        raise AuditBlocked(f"Gaussian 方法或终止状态漂移：{path.name}")
    return {
        "gibbs_hartree": gibbs_values[-1],
        "frequency_count": len(frequencies),
        "negative_frequency_count": sum(value < 0 for value in frequencies),
        "most_negative_cm-1": min(frequencies),
        "normal_termination_count": lowered.count("normal termination"),
    }


NIPU_GIBBS = {
    11: -1148.392085, 12: -1148.323975, 13: -1148.391251, 14: -1148.375331, 15: -1148.417225,
    16: -1571.660054, 17: -1571.597147, 18: -1571.660735, 19: -1571.623851, 20: -1571.672760,
    21: -2455.277064, 22: -2455.248626, 23: -2455.276067, 24: -2455.245010, 25: -2455.288455,
    26: -5155.292183, 27: -5155.208585, 28: -5155.260215, 29: -5155.244667, 30: -5155.305929,
}
NIPU_TS_IDS = frozenset({12, 14, 17, 19, 22, 24, 27, 29})
NIPU_PATHWAYS = {
    "Cl": (11, 12, 13, 14, 15),
    "Cl+1BMIM": (16, 17, 18, 19, 20),
    "2Cl+2BMIM": (21, 22, 23, 24, 25),
    "ZnCl4+2BMIM": (26, 27, 28, 29, 30),
}
NIPU_EXPECTED_BARRIERS = {
    "Cl": (42.740, 9.990),
    "Cl+1BMIM": (39.475, 23.145),
    "2Cl+2BMIM": (17.845, 19.489),
    "ZnCl4+2BMIM": (52.459, 9.757),
}


def audit_nipu() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    directory = DATA_ROOT / NIPU
    input_profiles: list[dict[str, object]] = []
    absolute_paths: list[str] = []
    for index in range(1, 6):
        name = f"VZ4_001_021_VSCHT_D_{index:04d}_v1.lmp"
        text = (directory / name).read_text(encoding="latin-1")
        nsteps_match = re.search(r"^\s*variable\s+nsteps\s+equal\s+(\d+)", text, re.MULTILINE)
        temperature_match = re.search(r"^\s*variable\s+TK\s+equal\s+(\d+)", text, re.MULTILINE)
        seed_match = re.search(r"^\s*velocity\s+all\s+create\s+\$\{TK\}\s+(\d+)", text, re.MULTILINE)
        if not (nsteps_match and temperature_match and seed_match):
            raise AuditBlocked(f"NIPU LAMMPS 输入缺 nsteps/TK/seed：{name}")
        file_paths = sorted(set(re.findall(r"(?<!\S)(/(?:home|storage)/\S+)", text)))
        if len(file_paths) < 3:
            raise AuditBlocked(f"NIPU LAMMPS 输入未复算出绝对路径：{name}")
        absolute_paths.extend(file_paths)
        input_profiles.append(
            {
                "文件": name,
                "nsteps": int(nsteps_match.group(1)),
                "temperature_K": int(temperature_match.group(1)),
                "seed": int(seed_match.group(1)),
                "absolute_paths": file_paths,
            }
        )
    if [profile["nsteps"] for profile in input_profiles] != [2_000_000_000, 1_000_000_000, 1_000_000_000, 1_000_000_000, 2_000_000_000]:
        raise AuditBlocked("NIPU LAMMPS nsteps 漂移")
    if [profile["temperature_K"] for profile in input_profiles] != [500, 500, 500, 500, 400]:
        raise AuditBlocked("NIPU LAMMPS 温度漂移")
    if {profile["seed"] for profile in input_profiles} != {12_345}:
        raise AuditBlocked("NIPU LAMMPS 随机种子漂移")

    data_atom_counts: dict[str, int] = {}
    for index in range(6, 11):
        name = f"VZ4_001_021_VSCHT_D_{index:04d}_v1.lmp"
        data_atom_counts[name] = _lammps_data_atom_count((directory / name).read_text(encoding="latin-1"))

    profiles: dict[int, dict[str, object]] = {}
    observations: list[dict[str, object]] = []
    state_names = ("R", "TS1", "I", "TS2", "P")
    path_by_id = {
        identifier: (pathway, state)
        for pathway, identifiers in NIPU_PATHWAYS.items()
        for identifier, state in zip(identifiers, state_names)
    }
    for identifier in range(11, 31):
        name = f"VZ4_001_021_VSCHT_D_{identifier:04d}_v1.log"
        profile = _gaussian_profile(directory / name)
        profiles[identifier] = profile
        if not math.isclose(float(profile["gibbs_hartree"]), NIPU_GIBBS[identifier], abs_tol=5e-7):
            raise AuditBlocked(f"NIPU Gaussian 最终 Gibbs 漂移：{name}")
        expected_negative = 1 if identifier in NIPU_TS_IDS else 0
        if profile["negative_frequency_count"] != expected_negative:
            raise AuditBlocked(f"NIPU Gaussian 虚频分类漂移：{name}")
        expected_frequency_count = 69 if identifier <= 15 else 144 if identifier <= 20 else 222 if identifier <= 25 else 231
        if profile["frequency_count"] != expected_frequency_count:
            raise AuditBlocked(f"NIPU Gaussian 频率数漂移：{name}")
        pathway, state = path_by_id[identifier]
        observations.append(
            {
                "来源": NIPU,
                "体系或路径": pathway,
                "记录层级": "Gaussian驻点计算",
                "观测或计算": state,
                "数值": profile["gibbs_hartree"],
                "单位": "Hartree",
                "相关性": "同一路径的五个依赖驻点",
                "独立材料数": 0,
                "准入状态": "反应机理特征；不可视为独立材料",
                "训练权重状态": "未赋权",
                "备注": f"负频数={expected_negative}; B3LYP-D3(BJ)/6-311+G(d,p)",
            }
        )

    barriers: dict[str, dict[str, float]] = {}
    for pathway, identifiers in NIPU_PATHWAYS.items():
        reactant, ts1, intermediate, ts2, _product = identifiers
        barrier1 = (float(profiles[ts1]["gibbs_hartree"]) - float(profiles[reactant]["gibbs_hartree"])) * HARTREE_TO_KCAL_MOL
        barrier2 = (float(profiles[ts2]["gibbs_hartree"]) - float(profiles[intermediate]["gibbs_hartree"])) * HARTREE_TO_KCAL_MOL
        expected1, expected2 = NIPU_EXPECTED_BARRIERS[pathway]
        if not math.isclose(barrier1, expected1, abs_tol=0.001) or not math.isclose(barrier2, expected2, abs_tol=0.001):
            raise AuditBlocked(f"NIPU Gibbs 势垒复算漂移：{pathway}={(barrier1, barrier2)}")
        barriers[pathway] = {
            "TS1_minus_R_kcal_mol": round(barrier1, 6),
            "TS2_minus_I_kcal_mol": round(barrier2, 6),
        }
        for label, value in (("TS1-R", barrier1), ("TS2-I", barrier2)):
            observations.append(
                {
                    "来源": NIPU,
                    "体系或路径": pathway,
                    "记录层级": "路径派生势垒",
                    "观测或计算": label,
                    "数值": round(value, 6),
                    "单位": "kcal/mol",
                    "相关性": "由同一路径驻点差分得到",
                    "独立材料数": 0,
                    "准入状态": "机理排序特征；需实验映射后赋权",
                    "训练权重状态": "未赋权",
                    "备注": "由实际日志最终Gibbs自由能复算。",
                }
            )

    file_rows = top_level_file_rows(NIPU)
    summary = {
        "来源": NIPU,
        "引用": source_reference(NIPU),
        "审计版本": AUDIT_VERSION,
        "文件构成": {"LAMMPS输入": 5, "LAMMPS初始数据": 5, "Gaussian完整日志": 20, "说明文件": 1},
        "LAMMPS输入": input_profiles,
        "LAMMPS初始数据原子数": data_atom_counts,
        "LAMMPS绝对路径数": len(absolute_paths),
        "LAMMPS可移植性": "五个输入均含/home或/storage绝对路径，需参数化后才能复算。",
        "Gaussian方法": "B3LYP-D3(BJ)/6-311+G(d,p)",
        "Gaussian驻点": {"总数": 20, "稳定点": 12, "一阶鞍点": 8, "错误终止": 0},
        "反应路径数": 4,
        "Gibbs势垒": barriers,
        "独立材料数": 0,
        "数据定位": "20个驻点属于4条反应路径；驻点、虚频和SCF循环不是20种独立材料。",
        "治理": governance("保留路径组和状态层级；LAMMPS绝对路径是复算阻断项，禁止直接运行。"),
    }
    return summary, file_rows, observations


def _numeric_xvg_rows(payload: bytes, name: str) -> int:
    count = 0
    for raw_line in payload.decode("latin-1").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "@")):
            continue
        tokens = line.split()
        try:
            [float(token) for token in tokens]
        except ValueError as exc:
            raise AuditBlocked(f"PCL XVG 出现非数值数据行：{name}/{line[:80]}") from exc
        count += 1
    return count


def _audit_bzip2_trr(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, object]:
    """流式解析全部 TRR 帧，同时触发 BZip2 与外层 ZIP 的完整性检查。"""
    digest = hashlib.sha256()
    total = 0
    frame_count = 0
    natoms_value: int | None = None
    precision_value: int | None = None
    first_step: int | None = None
    last_step: int | None = None
    first_time_ps: float | None = None
    last_time_ps: float | None = None
    try:
        with archive.open(info) as compressed, bz2.BZ2File(compressed) as trajectory:
            def read_exact(size: int, *, allow_clean_eof: bool = False) -> bytes | None:
                nonlocal total
                chunks: list[bytes] = []
                remaining = size
                while remaining:
                    block = trajectory.read(remaining)
                    if not block:
                        if allow_clean_eof and remaining == size:
                            return None
                        raise AuditBlocked(
                            f"PCL TRR 帧被截断：{info.filename}; need={size}; got={size-remaining}"
                        )
                    chunks.append(block)
                    digest.update(block)
                    total += len(block)
                    remaining -= len(block)
                return b"".join(chunks)

            while True:
                magic_bytes = read_exact(4, allow_clean_eof=True)
                if magic_bytes is None:
                    break
                if struct.unpack(">i", magic_bytes)[0] != 1993:
                    raise AuditBlocked(f"PCL TRR 帧 magic 漂移：{info.filename}/{frame_count}")
                version_buflen = struct.unpack(">i", read_exact(4))[0]
                version_length = struct.unpack(">i", read_exact(4))[0]
                padded_length = (version_length + 3) // 4 * 4
                version_payload = read_exact(padded_length)
                if (
                    version_buflen != 13
                    or version_length != 12
                    or version_payload[:version_length] != b"GMX_trn_file"
                ):
                    raise AuditBlocked(f"PCL TRR 版本头漂移：{info.filename}/{frame_count}")
                sizes = struct.unpack(">10i", read_exact(40))
                if any(size < 0 or size > 256_000_000 for size in sizes):
                    raise AuditBlocked(f"PCL TRR 块大小越界：{info.filename}/{frame_count}")
                natoms, step, _nre = struct.unpack(">3i", read_exact(12))
                if natoms <= 0 or step < 0:
                    raise AuditBlocked(f"PCL TRR 原子数或步号非法：{info.filename}/{frame_count}")
                box_size, x_size, velocity_size, force_size = (
                    sizes[2], sizes[7], sizes[8], sizes[9]
                )
                precision_candidates: set[int] = set()
                if box_size:
                    if box_size % 9:
                        raise AuditBlocked(f"PCL TRR box块形状非法：{info.filename}/{frame_count}")
                    precision_candidates.add(box_size // 9)
                if x_size:
                    denominator = natoms * 3
                    if x_size % denominator:
                        raise AuditBlocked(f"PCL TRR坐标块形状非法：{info.filename}/{frame_count}")
                    precision_candidates.add(x_size // denominator)
                if precision_candidates not in ({4}, {8}):
                    raise AuditBlocked(
                        f"PCL TRR浮点精度不一致：{info.filename}/{frame_count}={precision_candidates}"
                    )
                precision = next(iter(precision_candidates))
                if (
                    box_size != 9 * precision
                    or x_size != natoms * 3 * precision
                    or velocity_size != 0
                    or force_size != 0
                ):
                    raise AuditBlocked(f"PCL TRR帧负载形状漂移：{info.filename}/{frame_count}")
                real_format = ">f" if precision == 4 else ">d"
                time_ps = float(struct.unpack(real_format, read_exact(precision))[0])
                _lambda = struct.unpack(real_format, read_exact(precision))[0]
                if not math.isfinite(time_ps):
                    raise AuditBlocked(f"PCL TRR时间非有限：{info.filename}/{frame_count}")
                for block_size in sizes:
                    read_exact(block_size)

                if natoms_value is None:
                    natoms_value = natoms
                    precision_value = precision
                    first_step = step
                    first_time_ps = time_ps
                elif natoms != natoms_value or precision != precision_value:
                    raise AuditBlocked(f"PCL TRR帧原子数/精度漂移：{info.filename}/{frame_count}")
                if last_step is not None and (step <= last_step or time_ps <= float(last_time_ps)):
                    raise AuditBlocked(f"PCL TRR步号或时间不单调：{info.filename}/{frame_count}")
                last_step = step
                last_time_ps = time_ps
                frame_count += 1
    except (OSError, EOFError) as exc:
        raise AuditBlocked(f"PCL BZip2/TRR 解压完整性失败：{info.filename}") from exc
    if frame_count == 0 or None in {
        natoms_value, precision_value, first_step, last_step, first_time_ps, last_time_ps,
    }:
        raise AuditBlocked(f"PCL TRR没有完整帧：{info.filename}")
    return {
        "path": info.filename.replace("\\", "/"),
        "compressed_payload_bytes": info.file_size,
        "trr_bytes": total,
        "trr_sha256": digest.hexdigest(),
        "frame_count": frame_count,
        "natoms": natoms_value,
        "real_precision_bytes": precision_value,
        "first_step": first_step,
        "last_step": last_step,
        "first_time_ps": round(float(first_time_ps), 6),
        "last_time_ps": round(float(last_time_ps), 6),
        "velocity_payload_present": False,
        "force_payload_present": False,
    }


def _pcl_run_protocol(
    archive: zipfile.ZipFile, trajectory_path: str, trajectory: dict[str, object]
) -> dict[str, object]:
    parent = str(PurePosixPath(trajectory_path).parent)
    mdp_name = f"{parent}/mdout.mdp"
    log_name = f"{parent}/md.log"
    names = set(archive.namelist())
    if mdp_name not in names or log_name not in names:
        raise AuditBlocked(f"PCL真实TRR缺配套mdout.mdp或md.log：{trajectory_path}")
    mdp = archive.read(mdp_name).decode("latin-1")
    log = archive.read(log_name).decode("latin-1", errors="replace")

    def setting(name: str) -> str:
        values = re.findall(rf"^\s*{re.escape(name)}\s*=\s*([^;\s]+)", mdp, re.MULTILINE)
        if len(values) != 1:
            raise AuditBlocked(f"PCL mdout设置缺失或重复：{mdp_name}/{name}={values}")
        return values[0]

    dt_ps = float(setting("dt"))
    nsteps = int(setting("nsteps"))
    tau_t_ps = float(setting("tau_t"))
    ref_t_k = float(setting("ref_t"))
    finished = "Finished mdrun on rank 0" in log
    term_signal = "Received the TERM signal" in log
    if finished == term_signal:
        raise AuditBlocked(
            f"PCL运行结束证据必须且只能是Finished或TERM之一：{log_name}"
        )
    terminal_step: int | None = None
    if term_signal:
        terminal_matches = re.findall(
            r"Received the TERM signal.*?Step\s+Time\s+Lambda\s+(\d+)",
            log,
            re.DOTALL,
        )
        if len(terminal_matches) != 1:
            raise AuditBlocked(f"PCL TERM终止步无法唯一解析：{log_name}")
        terminal_step = int(terminal_matches[0])
    last_step = int(trajectory["last_step"])
    last_time_ps = float(trajectory["last_time_ps"])
    if not math.isclose(last_time_ps, last_step * dt_ps, abs_tol=0.01):
        raise AuditBlocked(
            f"PCL TRR时间与mdout步长不一致：{trajectory_path}="
            f"{last_time_ps}/{last_step * dt_ps}"
        )
    if finished and last_step != nsteps:
        raise AuditBlocked(f"PCL Finished运行末帧不等于nsteps：{trajectory_path}")
    if terminal_step is not None and last_step > terminal_step:
        raise AuditBlocked(f"PCL TRR末帧晚于TERM记录：{trajectory_path}")
    if "tau0.5" in PurePosixPath(trajectory_path).parts and not (
        math.isclose(dt_ps, 0.0005) and math.isclose(tau_t_ps, 0.1)
    ):
        raise AuditBlocked(f"PCL tau0.5目录的实际dt/tau_t语义漂移：{trajectory_path}")
    return {
        "mdout_path": mdp_name,
        "log_path": log_name,
        "dt_ps": dt_ps,
        "dt_fs": dt_ps * 1_000,
        "nsteps_declared": nsteps,
        "tau_t_ps": tau_t_ps,
        "ref_t_K": ref_t_k,
        "completion_status": (
            "finished"
            if finished
            else (
                "terminated_after_continuation_beyond_declared_nsteps"
                if last_step > nsteps
                else "terminated_before_declared_nsteps"
            )
        ),
        "log_terminal_step": terminal_step,
        "path_semantics_note": (
            "目录tau0.5实际表示dt=0.5 fs，tau_t=0.1 ps"
            if "tau0.5" in PurePosixPath(trajectory_path).parts
            else (
                "无300K子目录但mdout明确ref_t=600 K"
                if math.isclose(ref_t_k, 600.0)
                else "温度与步长由mdout.mdp而非目录名裁决"
            )
        ),
    }


def audit_pcl() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    path = DATA_ROOT / PCL / "PCL_Supplementary_material_systematic_CG-v1.0_2.zip"
    archive, archive_rows = audit_zip(PCL, path, 376, 346, 205_175_838)
    try:
        infos = archive.infolist()
        names = [info.filename.replace("\\", "/") for info in infos]
        roots = {PurePosixPath(name).parts[0] for name in names}
        if roots != {"pbacova-PCL_Supplementary_material_systematic_CG-446ebad"}:
            raise AuditBlocked(f"PCL 固定 Git 根漂移：{roots}")

        pointer_pattern = re.compile(
            rb"\Aversion https://git-lfs\.github\.com/spec/v1\r?\n"
            rb"oid sha256:([0-9a-f]{64})\r?\nsize (\d+)\r?\n?\Z"
        )
        pointers: list[dict[str, object]] = []
        real_trajectories: list[dict[str, object]] = []
        polystat_rows: dict[str, int] = {}
        for info, name in zip(infos, names):
            if info.is_dir():
                continue
            if info.file_size <= 256:
                payload = archive.read(info)
                match = pointer_pattern.fullmatch(payload)
                if match:
                    pointers.append(
                        {
                            "path": name,
                            "oid_sha256": match.group(1).decode("ascii"),
                            "declared_bytes": int(match.group(2)),
                            "pointer_bytes": info.file_size,
                        }
                    )
            if name.endswith("/polystat.xvg"):
                polystat_rows[name] = _numeric_xvg_rows(archive.read(info), name)

        trajectory_infos = [
            (info, name)
            for info, name in zip(infos, names)
            if not info.is_dir() and name.endswith("/traj.trr.bz2")
        ]
        pointer_paths = {str(item["path"]) for item in pointers}
        for info, name in trajectory_infos:
            if name not in pointer_paths:
                real_trajectories.append(_audit_bzip2_trr(archive, info))

        root = "pbacova-PCL_Supplementary_material_systematic_CG-446ebad"
        expected_real_trajectories = {
            f"{root}/100mer/vacuum/300K/traj.trr.bz2": (
                21_788_618, 26_781_636,
                "1560def0ab47d2f92cec73b5284fedb8cf3fed5671726456b2b161379bc48323",
            ),
            f"{root}/10mer/solvated/tau0.5/traj.trr.bz2": (
                3_609_506, 4_018_992,
                "be2259c019a0c1fb4c97ba389ad374a21252362071192ca6f786f57d64bb269e",
            ),
            f"{root}/10mer/solvated/traj.trr.bz2": (
                71_426_079, 79_623_792,
                "063ec01fd2bf3be99edbfc623d5cf149e3062738e8969eef173c934083cc8c36",
            ),
            f"{root}/10mer/vacuum/traj.trr.bz2": (
                1_782_949, 2_318_316,
                "aad6ebaa33c3e77eaec1d96fe8cb892c5dc6f57d7e0d98befa758014e6ea4605",
            ),
            f"{root}/30mer/vacuum/traj.trr.bz2": (
                4_928_703, 6_642_636,
                "1cba850cd6649aa6cf56dc065f73addbb520d3777e4d92822c7db24e4f3adb3b",
            ),
            f"{root}/50mer/solvated/tau0.5/traj.trr.bz2": (
                17_892_779, 19_995_576,
                "5832998d22a233ef88e19299a9bbf5f6ac54beb264272c808950e4bfba82940c",
            ),
            f"{root}/50mer/vacuum/300K/traj.trr.bz2": (
                6_596_893, 8_863_404,
                "1eedca215251f444aaa8081e55079e428130bcc473ff417e7cfc922ad359cec2",
            ),
            f"{root}/50mer/vacuum/traj.trr.bz2": (
                1_112_689, 1_654_356,
                "8ff7ec5fbb9858bffa5bc7fe42578cf5e51e428a2d46e340d434b146c58bb1f0",
            ),
        }
        observed_real = {
            str(item["path"]): (
                int(item["compressed_payload_bytes"]),
                int(item["trr_bytes"]),
                str(item["trr_sha256"]),
            )
            for item in real_trajectories
        }
        if len(trajectory_infos) != 18 or observed_real != expected_real_trajectories:
            raise AuditBlocked(
                f"PCL真实/指针轨迹集合或载荷漂移：total={len(trajectory_infos)}; "
                f"real={observed_real}"
            )
        for trajectory in real_trajectories:
            trajectory.update(
                _pcl_run_protocol(archive, str(trajectory["path"]), trajectory)
            )
        expected_run_profiles = {
            f"{root}/100mer/vacuum/300K/traj.trr.bz2": (
                1_803, 1_231, 123_000_000, 123_000.0, 0.001, 100_000_000,
                0.01, 300.0, "terminated_after_continuation_beyond_declared_nsteps",
                123_091_074,
            ),
            f"{root}/10mer/solvated/tau0.5/traj.trr.bz2": (
                3_306, 101, 2_000_000, 1_000.0, 0.0005, 2_000_000,
                0.1, 300.0, "finished", None,
            ),
            f"{root}/10mer/solvated/traj.trr.bz2": (
                3_306, 2_001, 100_000_000, 100_000.0, 0.001, 100_000_000,
                0.1, 300.0, "finished", None,
            ),
            f"{root}/10mer/vacuum/traj.trr.bz2": (
                183, 1_001, 100_000_000, 100_000.0, 0.001, 100_000_000,
                0.01, 300.0, "finished", None,
            ),
            f"{root}/30mer/vacuum/traj.trr.bz2": (
                543, 1_001, 100_000_000, 100_000.0, 0.001, 100_000_000,
                0.01, 300.0, "finished", None,
            ),
            f"{root}/50mer/solvated/tau0.5/traj.trr.bz2": (
                16_488, 101, 2_000_000, 1_000.0, 0.0005, 2_000_000,
                0.1, 300.0, "finished", None,
            ),
            f"{root}/50mer/vacuum/300K/traj.trr.bz2": (
                903, 809, 80_800_000, 80_800.0, 0.001, 100_000_000,
                0.01, 300.0, "terminated_before_declared_nsteps", 80_855_642,
            ),
            f"{root}/50mer/vacuum/traj.trr.bz2": (
                903, 151, 15_000_000, 15_000.0, 0.001, 100_000_000,
                2.0, 600.0, "terminated_before_declared_nsteps", 15_040_904,
            ),
        }
        observed_run_profiles = {
            str(item["path"]): (
                int(item["natoms"]), int(item["frame_count"]),
                int(item["last_step"]), float(item["last_time_ps"]),
                float(item["dt_ps"]), int(item["nsteps_declared"]),
                float(item["tau_t_ps"]), float(item["ref_t_K"]),
                str(item["completion_status"]), item["log_terminal_step"],
            )
            for item in real_trajectories
        }
        if observed_run_profiles != expected_run_profiles:
            raise AuditBlocked(f"PCL真实运行协议/帧复算漂移：{observed_run_profiles}")
        if (
            sum(int(item["frame_count"]) for item in real_trajectories) != 6_396
            or any(int(item["first_step"]) != 0 for item in real_trajectories)
            or any(float(item["first_time_ps"]) != 0.0 for item in real_trajectories)
            or any(int(item["real_precision_bytes"]) != 4 for item in real_trajectories)
        ):
            raise AuditBlocked("PCL真实TRR总帧数、起点或精度漂移")

        if len(pointers) != 10 or sum(int(item["declared_bytes"]) for item in pointers) != 2_313_207_356:
            raise AuditBlocked("PCL Git LFS 指针数或声明总字节复算漂移")
        if len({item["oid_sha256"] for item in pointers}) != 10:
            raise AuditBlocked("PCL Git LFS 指针 OID 不唯一")
        if len(polystat_rows) != 12 or sum(polystat_rows.values()) != 11_277:
            raise AuditBlocked("PCL polystat 强相关时间点复算漂移")

        rg_name = _single_name(names, "/data_fig_S1/rg_data_solvated", "PCL Rg 聚合值")
        rg_values: list[tuple[int, float, float]] = []
        for line in archive.read(rg_name).decode("ascii").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            tokens = stripped.split()
            if len(tokens) != 3:
                raise AuditBlocked(f"PCL Rg 聚合值字段数漂移：{stripped}")
            rg_values.append((int(tokens[0]), float(tokens[1]), float(tokens[2])))
        expected_rg = [
            (10, 0.692, 0.093),
            (30, 0.928, 0.045),
            (50, 1.071, 0.029),
            (100, 1.319, 0.022),
            (125, 1.413, 0.020),
        ]
        if rg_values != expected_rg:
            raise AuditBlocked(f"PCL 五个 Rg 聚合值漂移：{rg_values}")

        row_by_member = {str(row["成员"]): row for row in archive_rows}
        for pointer in pointers:
            row_by_member[str(pointer["path"])]["角色"] = "Git LFS指针（非轨迹载荷）"
        for trajectory in real_trajectories:
            row_by_member[str(trajectory["path"])]["角色"] = (
                "BZip2压缩GROMACS_TRR真实轨迹（尚未映射实验QoI）"
            )
        for name in polystat_rows:
            row_by_member[name]["角色"] = "强相关构象时间序列"
        row_by_member[rg_name]["角色"] = "Rg聚合值"
    finally:
        archive.close()

    observations: list[dict[str, object]] = []
    for chain_length, mean_rg, sd_rg in rg_values:
        observations.append(
            {
                "来源": PCL,
                "体系或路径": f"PCL_{chain_length}mer_solvated",
                "记录层级": "Rg聚合统计",
                "观测或计算": "radius_of_gyration_mean",
                "数值": mean_rg,
                "单位": "nm",
                "相关性": "由同一链长构象时间序列聚合",
                "独立材料数": 0,
                "准入状态": "软段构象描述符；不是材料级性能标签",
                "训练权重状态": "未赋权",
                "备注": f"标准差={sd_rg} nm；五个链长仅是同一PCL模型的条件。",
            }
        )
    for name, count in sorted(polystat_rows.items()):
        observations.append(
            {
                "来源": PCL,
                "体系或路径": str(PurePosixPath(name).parent),
                "记录层级": "强相关MD时间序列",
                "观测或计算": "polystat_numeric_rows",
                "数值": count,
                "单位": "timepoints",
                "相关性": "同一轨迹内强相关；不得随机拆分",
                "独立材料数": 0,
                "准入状态": "仅可按整条运行/链长分组使用",
                "训练权重状态": "未赋权",
                "备注": "时间点仅作轨迹内部采样，不计独立样本。",
            }
        )
    for trajectory in sorted(real_trajectories, key=lambda item: str(item["path"])):
        parent = PurePosixPath(str(trajectory["path"])).parent
        observations.append(
            {
                "来源": PCL,
                "体系或路径": str(parent),
                "记录层级": "真实GROMACS_TRR运行载荷",
                "观测或计算": "decompressed_trr_bytes",
                "数值": int(trajectory["trr_bytes"]),
                "单位": "bytes",
                "相关性": "同一运行内全部帧强相关；整个TRR是一个运行家族",
                "独立材料数": 0,
                "准入状态": "真实模拟输出；完成协议/终止/收敛与实验映射前不赋权",
                "训练权重状态": "未赋权",
                "备注": (
                    f"BZip2与{trajectory['frame_count']}帧TRR/XDR全解析通过；"
                    f"natoms={trajectory['natoms']}；ref_t={trajectory['ref_t_K']} K；"
                    f"dt={trajectory['dt_fs']} fs；状态={trajectory['completion_status']}；"
                    f"载荷SHA256={trajectory['trr_sha256']}"
                ),
            }
        )

    file_rows = top_level_file_rows(PCL) + archive_rows
    summary = {
        "来源": PCL,
        "引用": source_reference(PCL),
        "审计版本": AUDIT_VERSION,
        "固定最新版": {"record_id": 17_790_918, "version": "v1.0_2", "stale_record_rejected": 17_106_011},
        "固定Git快照": "446ebadb9ba937d393b6cd7d727256c90e15f24e",
        "归档": {"条目": 376, "普通文件": 346, "未压缩字节": 205_175_838, "CRC": "通过"},
        "Git_LFS": {
            "指针文件数": len(pointers),
            "声明载荷总字节": sum(int(item["declared_bytes"]) for item in pointers),
            "轨迹载荷状态": (
                "18条TRR路径中10条在Zenodo归档内仅含LFS指针且当前本地未取得载荷；"
                "另8条含真实BZip2/TRR载荷"
            ),
            "指针": sorted(pointers, key=lambda item: str(item["path"])),
        },
        "真实TRR轨迹": {
            "运行家族数": len(real_trajectories),
            "总帧数": sum(int(item["frame_count"]) for item in real_trajectories),
            "压缩载荷总字节": sum(
                int(item["compressed_payload_bytes"]) for item in real_trajectories
            ),
            "解压TRR总字节": sum(int(item["trr_bytes"]) for item in real_trajectories),
            "BZip2完整性": "通过",
            "TRR_XDR全帧": "逐帧1993/GMX_trn_file、块边界、原子数、float32精度、步号与时间单调性通过",
            "正常完成运行数": sum(
                item["completion_status"] == "finished" for item in real_trajectories
            ),
            "TERM终止运行数": sum(
                item["completion_status"] != "finished" for item in real_trajectories
            ),
            "运行": sorted(real_trajectories, key=lambda item: str(item["path"])),
        },
        "polystat": {"文件数": len(polystat_rows), "强相关数值时间点": sum(polystat_rows.values())},
        "Rg聚合值": [
            {"chain_length_mer": length, "mean_nm": mean, "sd_nm": sd}
            for length, mean, sd in rg_values
        ],
        "独立材料数": 0,
        "数据定位": (
            "五个链长的构象聚合、12条polystat时间序列、8条真实TRR运行，"
            "以及10条在Zenodo归档内未内嵌且当前本地未取得载荷的LFS指针；"
            "链长、时间点、帧和指针都不是独立材料。"
        ),
        "治理": governance("训练划分必须按整条运行、链长、环境和协议分组；当前仅登记，不生成训练样本。"),
    }
    return summary, file_rows, observations


def _parse_parameter_masses(path: Path) -> tuple[dict[int, float], dict[int, str], dict[int, str]]:
    text = path.read_text(encoding="latin-1")
    masses: dict[int, float] = {}
    labels: dict[int, str] = {}
    elements: dict[int, str] = {}
    for match in re.finditer(r"^\s*mass\s+(\d+)\s+([0-9.]+)\s+#\s*(\S+)\s*$", text, re.MULTILINE):
        atom_type = int(match.group(1))
        label = match.group(3)
        if label.startswith("c"):
            element = "C"
        elif label.startswith("h"):
            element = "H"
        elif label.startswith("n"):
            element = "N"
        elif label.startswith("o"):
            element = "O"
        else:
            raise AuditBlocked(f"冲击模型参数原子标签无法映射元素：{label}")
        masses[atom_type] = float(match.group(2))
        labels[atom_type] = label
        elements[atom_type] = element
    if set(masses) != set(range(1, 11)):
        raise AuditBlocked(f"冲击模型参数质量类型漂移：{sorted(masses)}")
    return masses, labels, elements


def _parse_impact_data(path: Path) -> dict[str, object]:
    header_counts: dict[str, int] = {}
    bounds: dict[str, tuple[float, float]] = {}
    data_masses: dict[int, float] = {}
    atom_ids: set[int] = set()
    molecule_counts: Counter[int] = Counter()
    type_counts: Counter[int] = Counter()
    charge_sum = 0.0
    coordinate_min = [math.inf, math.inf, math.inf]
    coordinate_max = [-math.inf, -math.inf, -math.inf]
    in_masses = False
    in_atoms = False
    with path.open("r", encoding="ascii", newline="") as handle:
        for line in handle:
            stripped = line.strip()
            if not in_atoms:
                count_match = re.fullmatch(
                    r"(\d+)\s+(atoms|atom types|bonds|bond types|angles|angle types|dihedrals|dihedral types|impropers|improper types)",
                    stripped,
                )
                if count_match:
                    header_counts[count_match.group(2)] = int(count_match.group(1))
                    continue
                bound_match = re.fullmatch(
                    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([xyz])lo\s+\3hi", stripped
                )
                if bound_match:
                    bounds[bound_match.group(3)] = (float(bound_match.group(1)), float(bound_match.group(2)))
                    continue
                if stripped == "Masses":
                    in_masses = True
                    continue
                if stripped.startswith("Atoms"):
                    in_masses = False
                    in_atoms = True
                    continue
                if in_masses and stripped:
                    tokens = stripped.split()
                    if len(tokens) == 2 and tokens[0].isdigit():
                        data_masses[int(tokens[0])] = float(tokens[1])
                continue

            if len(atom_ids) >= header_counts.get("atoms", -1):
                break
            if not stripped:
                continue
            tokens = stripped.split()
            if len(tokens) < 7 or not tokens[0].isdigit():
                raise AuditBlocked(f"冲击模型 Atoms 行字段异常：{stripped[:100]}")
            atom_id = int(tokens[0])
            molecule_id = int(tokens[1])
            atom_type = int(tokens[2])
            if atom_id in atom_ids:
                raise AuditBlocked(f"冲击模型原子 ID 重复：{atom_id}")
            atom_ids.add(atom_id)
            molecule_counts[molecule_id] += 1
            type_counts[atom_type] += 1
            charge_sum += float(tokens[3])
            for axis, value in enumerate(map(float, tokens[4:7])):
                coordinate_min[axis] = min(coordinate_min[axis], value)
                coordinate_max[axis] = max(coordinate_max[axis], value)

    if len(atom_ids) != header_counts.get("atoms"):
        raise AuditBlocked(f"冲击模型原子复算数量不符：{len(atom_ids)}/{header_counts.get('atoms')}")
    return {
        "header_counts": header_counts,
        "bounds": bounds,
        "data_masses": data_masses,
        "atom_id_min": min(atom_ids),
        "atom_id_max": max(atom_ids),
        "molecule_counts": molecule_counts,
        "type_counts": type_counts,
        "charge_sum": charge_sum,
        "coordinate_min": coordinate_min,
        "coordinate_max": coordinate_max,
    }


def audit_impact() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    directory = DATA_ROOT / IMPACT
    parameter_masses, type_labels, elements = _parse_parameter_masses(directory / "polyurethane_60nm.params")
    profile = _parse_impact_data(directory / "polyurethane_60nm.data")
    expected_header = {
        "atoms": 607_104,
        "atom types": 10,
        "bonds": 616_284,
        "bond types": 15,
        "angles": 1_133_424,
        "angle types": 27,
        "dihedrals": 1_526_940,
        "dihedral types": 35,
        "impropers": 651_168,
        "improper types": 21,
    }
    if profile["header_counts"] != expected_header:
        raise AuditBlocked(f"冲击模型拓扑头计数漂移：{profile['header_counts']}")
    if profile["atom_id_min"] != 1 or profile["atom_id_max"] != 607_104:
        raise AuditBlocked("冲击模型原子 ID 范围漂移")
    molecule_counts: Counter[int] = profile["molecule_counts"]  # type: ignore[assignment]
    type_counts: Counter[int] = profile["type_counts"]  # type: ignore[assignment]
    if len(molecule_counts) != 612 or set(molecule_counts) != set(range(1, 613)) or set(molecule_counts.values()) != {992}:
        raise AuditBlocked("冲击模型链数、链ID或每链原子数漂移")
    expected_type_counts = {
        1: 9_180, 2: 612, 3: 9_792, 4: 9_792, 5: 9_792,
        6: 58_752, 7: 326_808, 8: 143_208, 9: 9_180, 10: 29_988,
    }
    if dict(sorted(type_counts.items())) != expected_type_counts:
        raise AuditBlocked("冲击模型原子类型计数漂移")
    data_masses: dict[int, float] = profile["data_masses"]  # type: ignore[assignment]
    if set(data_masses) != set(parameter_masses):
        raise AuditBlocked("冲击模型 data/params 质量类型集合不一致")
    for atom_type in data_masses:
        if not math.isclose(data_masses[atom_type], parameter_masses[atom_type], abs_tol=0.0002):
            raise AuditBlocked(f"冲击模型 data/params 质量不一致：type {atom_type}")

    element_totals: Counter[str] = Counter()
    total_mass_amu = 0.0
    for atom_type, count in type_counts.items():
        element_totals[elements[atom_type]] += count
        total_mass_amu += count * data_masses[atom_type]
    if any(count % 612 for count in element_totals.values()):
        raise AuditBlocked("冲击模型元素总数不能整除链数")
    per_chain = {element: count // 612 for element, count in element_totals.items()}
    if per_chain != {"C": 346, "H": 550, "N": 16, "O": 80}:
        raise AuditBlocked(f"冲击模型每链组成漂移：{per_chain}")
    formula = f"C{per_chain['C']}H{per_chain['H']}N{per_chain['N']}O{per_chain['O']}"
    per_chain_mass = total_mass_amu / 612

    coordinate_min: list[float] = profile["coordinate_min"]  # type: ignore[assignment]
    coordinate_max: list[float] = profile["coordinate_max"]  # type: ignore[assignment]
    expected_min = [-599.4472309624, 0.3318260376, 0.3320666418]
    expected_max = [29.2184523958, 101.3905853236, 101.3903342344]
    if any(not math.isclose(value, expected, abs_tol=5e-10) for value, expected in zip(coordinate_min, expected_min)):
        raise AuditBlocked(f"冲击模型占据坐标最小值漂移：{coordinate_min}")
    if any(not math.isclose(value, expected, abs_tol=5e-10) for value, expected in zip(coordinate_max, expected_max)):
        raise AuditBlocked(f"冲击模型占据坐标最大值漂移：{coordinate_max}")
    spans = [high - low for low, high in zip(coordinate_min, coordinate_max)]
    occupied_volume_a3 = math.prod(spans)
    occupied_density = total_mass_amu * 1.66053906660 / occupied_volume_a3
    bounds: dict[str, tuple[float, float]] = profile["bounds"]  # type: ignore[assignment]
    if set(bounds) != {"x", "y", "z"}:
        raise AuditBlocked("冲击模型盒边界缺失")
    box_volume_a3 = math.prod(high - low for low, high in bounds.values())
    box_average_density = total_mass_amu * 1.66053906660 / box_volume_a3
    if not math.isclose(occupied_density, 0.98363038137, abs_tol=1e-10):
        raise AuditBlocked(f"冲击模型占据区密度复算漂移：{occupied_density}")

    input_text = (directory / "spall_in.in").read_text(encoding="ascii")
    timestep_match = re.search(r"^\s*variable\s+tstp\s+equal\s+([0-9.]+)", input_text, re.MULTILINE)
    seed_match = re.search(r"^\s*fix\s+2\s+all\s+langevin\s+\S+\s+\S+\s+\S+\s+(\d+)", input_text, re.MULTILINE)
    velocity_match = re.search(r"^\s*fix\s+move_pist\s+piston\s+move\s+linear\s+([0-9.]+)", input_text, re.MULTILINE)
    runs = [int(value) for value in re.findall(r"^\s*run\s+(\d+)\s*$", input_text, re.MULTILINE)]
    if not (timestep_match and seed_match and velocity_match):
        raise AuditBlocked("冲击模型 spall 输入缺 timestep/seed/piston velocity")
    timestep_fs = float(timestep_match.group(1))
    piston_angstrom_per_fs = float(velocity_match.group(1))
    if timestep_fs != 0.1 or int(seed_match.group(1)) != 90_429_997 or piston_angstrom_per_fs != 0.02 or runs != [100_000, 100_000, 400_000]:
        raise AuditBlocked("冲击模型单一运行配方漂移")
    if 'read_data \t"polyurethane_60nm.data"' not in input_text and 'read_data  \t"polyurethane_60nm.data"' not in input_text:
        if not re.search(r"^\s*read_data\s+\"polyurethane_60nm\.data\"", input_text, re.MULTILINE):
            raise AuditBlocked("冲击模型 input/data 连接漂移")
    if not re.search(r"^\s*include\s+\"polyurethane_60nm\.params\"", input_text, re.MULTILINE):
        raise AuditBlocked("冲击模型 input/params 连接漂移")
    expected_outputs = ("density.out", "shock_movie.lammpstrj")
    present_outputs = [name for name in expected_outputs if (directory / name).exists()]
    if present_outputs:
        raise AuditBlocked(f"冲击模型目录意外包含运行输出：{present_outputs}")

    observations = [
        {
            "来源": IMPACT,
            "体系或路径": "PTMO-MDI-BDO polyurethane 60nm slab",
            "记录层级": "单一全原子模型与冲击配方",
            "观测或计算": "occupied_slab_density",
            "数值": round(occupied_density, 12),
            "单位": "g/cm3",
            "相关性": "由607104原子同一模型的质量与占据边界复算",
            "独立材料数": 0,
            "准入状态": "冲击模拟候选基线；无分发输出，不能作冲击性能标签",
            "训练权重状态": "未赋权",
            "备注": f"612链，每链{formula}；单一运行，2.0 km/s活塞速度。",
        }
    ]
    summary = {
        "来源": IMPACT,
        "引用": source_reference(IMPACT),
        "审计版本": AUDIT_VERSION,
        "固定Git辅助输入": {
            "commit": PINNED_GIT_COMMIT,
            "files": ["spall_in.in", "polyurethane_60nm.params"],
            "LICENSE": "未检测到；禁止假定再分发权限",
        },
        "拓扑计数": expected_header,
        "链": {"链数": 612, "每链原子数": 992, "每链分子式": formula, "每链质量_Da": round(per_chain_mass, 8)},
        "原子类型": [
            {
                "type": atom_type,
                "label": type_labels[atom_type],
                "element": elements[atom_type],
                "count": type_counts[atom_type],
                "mass_amu": data_masses[atom_type],
            }
            for atom_type in sorted(type_counts)
        ],
        "总质量_amu": round(total_mass_amu, 8),
        "净电荷_e": round(float(profile["charge_sum"]), 10),
        "占据边界_A": {"min": coordinate_min, "max": coordinate_max, "span": spans},
        "密度": {
            "占据区包围盒_g_cm3": round(occupied_density, 12),
            "完整模拟盒平均_g_cm3": round(box_average_density, 12),
            "解释": "完整盒沿x含冲击传播真空/余量；材料体密度采用占据区包围盒。",
        },
        "单一运行配方": {
            "timestep_fs": timestep_fs,
            "equilibration_steps": runs[0],
            "shock_steps": runs[1],
            "post_shock_steps": runs[2],
            "durations_ps": [run * timestep_fs / 1_000 for run in runs],
            "langevin_seed": int(seed_match.group(1)),
            "piston_A_per_fs": piston_angstrom_per_fs,
            # units real 中速度单位为 Å/fs；1 Å/fs = 100 km/s。
            "piston_km_s": piston_angstrom_per_fs * 100.0,
        },
        "已分发运行输出": [],
        "独立材料数": 0,
        "数据定位": "607104个原子属于612条同配方链构成的单一冲击模型；没有密度/轨迹输出。",
        "治理": governance("只能将整个模型视为一个模拟家族；原子和链不得拆成材料级训练样本。"),
    }
    return summary, top_level_file_rows(IMPACT), observations


FILE_COLUMNS = ["来源", "容器或目录", "成员", "字节", "压缩字节", "CRC32", "SHA256", "角色", "状态"]
OBSERVATION_COLUMNS = [
    "来源",
    "体系或路径",
    "记录层级",
    "观测或计算",
    "数值",
    "单位",
    "相关性",
    "独立材料数",
    "准入状态",
    "训练权重状态",
    "备注",
]


def render_tsv(rows: list[dict[str, object]], columns: list[str]) -> bytes:
    if not rows:
        raise AuditBlocked("拒绝渲染空 TSV")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=columns,
        delimiter="\t",
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def main() -> int:
    before = scientific_input_snapshot()
    source_snapshots = snapshot_by_source(before)
    audits = {
        POLYUREA: audit_polyurea(),
        NIPU: audit_nipu(),
        PCL: audit_pcl(),
        IMPACT: audit_impact(),
    }
    after = scientific_input_snapshot()
    if before != after:
        raise AuditBlocked("审计期间科学输入发生变化，拒绝写出")

    rendered_outputs: list[tuple[Path, bytes]] = []
    for source in SOURCE_NAMES:
        summary, file_rows, observation_rows = audits[source]
        summary["审计日期"] = AUDIT_DATE
        summary["科学输入快照"] = source_snapshots[source]
        summary["科学输入运行前后不变"] = True
        summary["训练状态"] = {
            "training_allowed": False,
            "training_split_created": False,
            "training_weight_materialized": False,
            "simulation_weight_adjusted": False,
        }
        summary_payload = (
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
        file_payload = render_tsv(
            sorted(file_rows, key=lambda row: (str(row["容器或目录"]), str(row["成员"]))),
            FILE_COLUMNS,
        )
        observation_payload = render_tsv(
            sorted(
                observation_rows,
                key=lambda row: (str(row["体系或路径"]), str(row["记录层级"]), str(row["观测或计算"])),
            ),
            OBSERVATION_COLUMNS,
        )
        rendered_outputs.extend(
            [
                (DATA_ROOT / source / "内容审计摘要.json", summary_payload),
                (DATA_ROOT / source / "文件校验清单.tsv", file_payload),
                (DATA_ROOT / source / "计算观测清单.tsv", observation_payload),
            ]
        )

    if {path for path, _ in rendered_outputs} != set(OUTPUT_WHITELIST):
        raise AuditBlocked("渲染输出集合与原子白名单不一致")
    for path, payload in rendered_outputs:
        atomic_write(path, payload)

    output_hashes = {
        path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(payload).hexdigest()
        for path, payload in rendered_outputs
    }
    print(
        json.dumps(
            {
                "status": "pass",
                "sources": list(SOURCE_NAMES),
                "outputs": len(rendered_outputs),
                "output_hashes": dict(sorted(output_hashes.items())),
                "training_allowed": False,
                "training_split_created": False,
                "training_weight_materialized": False,
                "simulation_weight_adjusted": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
