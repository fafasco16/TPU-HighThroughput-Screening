"""下载通过候选门禁的开放数据。

下载清单只能来自 ``配置/候选数据源.yaml``，且必须同时通过全量来源
去重、A级评分、许可证据、固定字节数、SHA-256 和主机白名单门禁。脚本不接受
任意URL参数；所有载荷先写 ``.part``，核验后原子晋升。受限 ORA 数据与已经存在
于来源治理配置中的补账来源不会被本脚本下载。

运行：

    python 代码/获取/获取候选数据.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "数据/原始" / "外部数据" / "新增开放数据"
REGISTRY = PROJECT_ROOT / "配置" / "候选数据源.yaml"
SOURCE_SCOPE = PROJECT_ROOT / "配置" / "v0.2来源范围.yaml"
GATE_SCRIPT = PROJECT_ROOT / "代码" / "审计" / "候选数据源门禁.py"
CAPTURE_DATE = "2026-07-21"
USER_AGENT = "TPU-HighThroughput-Screening/0.5 (+research data acquisition)"
ALLOWED_HOSTS = frozenset(
    {
        "ars.els-cdn.com",
        "data.mendeley.com",
        "prod-dcd-datasets-cache-zipfiles.s3.eu-west-1.amazonaws.com",
        "zenodo.org",
    }
)
CANDIDATE_DIRECTORIES = {
    "fisher_2020_pu_shape_memory_raw": "DataInBrief_聚氨酯形状记忆多模态原始数据",
    "mendeley_2026_iir_oh_low_permeability_pu": "第十八批实验_IIR-OH聚氨酯",
    "mendeley_2024_aged_vegetable_puf_simulation": "第十九批模拟_老化植物基PU泡沫",
    "zenodo_3631551_lignin_tpu_blends": "Zenodo_木质素_TPU多模态数据",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AcquisitionBlocked(RuntimeError):
    """候选、URL、路径、载荷或内容未通过失败关闭门禁。"""


@dataclass(frozen=True)
class FileSpec:
    file_id: str
    filename: str
    size: int
    sha256: str
    media_type: str
    url: str


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    directory_name: str
    canonical_identifier: str
    title: str
    version_label: str
    repository: str
    rights_status: str
    license_spdx: str | None
    rights_evidence_url: str
    stable_url: str
    files: tuple[FileSpec, ...]


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("fifth_batch_gate_runtime", GATE_SCRIPT)
    if spec is None or spec.loader is None:
        raise AcquisitionBlocked(f"不能加载候选门禁：{GATE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AcquisitionBlocked(f"YAML根节点必须是映射：{path}")
    return payload


def validate_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise AcquisitionBlocked(f"URL不能解析：{url}") from error
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise AcquisitionBlocked(f"拒绝非HTTPS或非白名单端点：{url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionBlocked(f"URL含用户信息或片段：{url}")
    return host


def _validate_metadata_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise AcquisitionBlocked(f"元数据URL必须是HTTPS：{url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionBlocked(f"元数据URL含用户信息或片段：{url}")


def _safe_component(value: str) -> None:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise AcquisitionBlocked(f"非法路径分量：{value!r}")
    if any(marker in value for marker in ("/", "\\", ":")):
        raise AcquisitionBlocked(f"路径分量越界：{value!r}")
    if value.endswith((".", " ")):
        raise AcquisitionBlocked(f"Windows不安全路径分量：{value!r}")


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(flag and attributes & flag) or bool(is_junction())


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right))
    )


def source_directory(name: str) -> Path:
    _safe_component(name)
    if not DATA_ROOT.is_dir() or _is_reparse_point(DATA_ROOT):
        raise AcquisitionBlocked(f"原始数据根不是普通目录：{DATA_ROOT}")
    root = DATA_ROOT.resolve(strict=True)
    if not _same_path(root, DATA_ROOT):
        raise AcquisitionBlocked(f"原始数据根路径不稳定：{DATA_ROOT}")
    target = DATA_ROOT / name
    if target.exists():
        if not target.is_dir() or _is_reparse_point(target):
            raise AcquisitionBlocked(f"来源目标不是普通目录：{target}")
    else:
        target.mkdir()
    if not _same_path(target.resolve(strict=True).parent, root):
        raise AcquisitionBlocked(f"来源目录逃逸：{target}")
    return target


def load_download_selection(
    registry_path: Path = REGISTRY,
    source_scope_path: Path = SOURCE_SCOPE,
) -> dict[str, CandidateSpec]:
    gate = _load_gate_module()
    report = gate.evaluate_registry(registry_path, source_scope_path)
    if not report["valid"]:
        raise AcquisitionBlocked(
            "候选门禁失败：" + " | ".join(report["errors"])
        )
    eligible = {
        row["candidate_id"]
        for row in report["candidates"]
        if row["download_eligible"]
    }
    unknown = eligible.difference(CANDIDATE_DIRECTORIES)
    if unknown:
        raise AcquisitionBlocked(f"A级候选缺少固定中文目录映射：{sorted(unknown)}")

    registry = _load_yaml(registry_path)
    rows = {
        str(row.get("candidate_id")): row
        for row in registry.get("candidates", [])
        if isinstance(row, dict)
    }
    selection: dict[str, CandidateSpec] = {}
    for candidate_id in sorted(eligible):
        row = rows[candidate_id]
        rights = row["rights"]
        file_specs: list[FileSpec] = []
        for file_row in row["files"]:
            checksum = str(file_row.get("checksum") or "")
            if not checksum.startswith("sha256:"):
                raise AcquisitionBlocked(
                    f"{candidate_id}/{file_row.get('file_id')}未冻结SHA-256"
                )
            sha256 = checksum.removeprefix("sha256:").lower()
            if not SHA256_RE.fullmatch(sha256):
                raise AcquisitionBlocked(
                    f"{candidate_id}/{file_row.get('file_id')}的SHA-256非法"
                )
            size = file_row.get("size_bytes")
            if type(size) is not int or size <= 0:
                raise AcquisitionBlocked(
                    f"{candidate_id}/{file_row.get('file_id')}未冻结字节数"
                )
            filename = str(file_row.get("upstream_name") or "")
            _safe_component(filename)
            url = str(file_row.get("download_url") or "")
            validate_url(url)
            file_specs.append(
                FileSpec(
                    file_id=str(file_row["file_id"]),
                    filename=filename,
                    size=size,
                    sha256=sha256,
                    media_type=str(file_row.get("media_type") or ""),
                    url=url,
                )
            )
        stable_url = str(row["stable_url"])
        rights_evidence_url = str(rights["evidence_url"])
        _validate_metadata_url(stable_url)
        _validate_metadata_url(rights_evidence_url)
        selection[candidate_id] = CandidateSpec(
            candidate_id=candidate_id,
            directory_name=CANDIDATE_DIRECTORIES[candidate_id],
            canonical_identifier=str(row["canonical_identifier"]),
            title=str(row["title"]),
            version_label=str(row["version_label"]),
            repository=str(row["repository"]),
            rights_status=str(rights["status"]),
            license_spdx=(
                str(rights["license_spdx"]) if rights.get("license_spdx") else None
            ),
            rights_evidence_url=rights_evidence_url,
            stable_url=stable_url,
            files=tuple(file_specs),
        )
    return selection


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(req.full_url)
        resolved = urljoin(req.full_url, newurl)
        validate_url(resolved)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _opener():
    return build_opener(SafeRedirectHandler())


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir() or _is_reparse_point(path.parent):
        raise AcquisitionBlocked(f"输出父目录不是普通目录：{path.parent}")
    if path.exists() and (not path.is_file() or _is_reparse_point(path)):
        raise AcquisitionBlocked(f"拒绝覆盖非普通文件：{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_magic(path: Path, spec: FileSpec) -> None:
    with path.open("rb") as handle:
        prefix = handle.read(1024)
    lower = prefix.lstrip().lower()
    if spec.filename.lower().endswith(".xlsx"):
        if not prefix.startswith(b"PK\x03\x04"):
            raise AcquisitionBlocked(f"XLSX魔数不符：{spec.filename}")
    elif spec.filename.lower().endswith(".zip"):
        if not prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            raise AcquisitionBlocked(f"ZIP魔数不符：{spec.filename}")
    elif spec.filename.lower().endswith(".xml"):
        if not lower.startswith(b"<?xml"):
            raise AcquisitionBlocked(f"XML魔数不符：{spec.filename}")
    elif spec.filename.lower().endswith(".txt"):
        if b"<html" in lower[:256] or b"<!doctype html" in lower[:256]:
            raise AcquisitionBlocked(f"文本端点返回HTML：{spec.filename}")


def _verify_file(path: Path, spec: FileSpec) -> None:
    if not path.is_file() or _is_reparse_point(path):
        raise AcquisitionBlocked(f"载荷不是普通文件：{path}")
    if path.stat().st_size != spec.size:
        raise AcquisitionBlocked(
            f"字节数不符：{spec.filename}，期望{spec.size}，实际{path.stat().st_size}"
        )
    actual_sha = _digest(path)
    if actual_sha != spec.sha256:
        raise AcquisitionBlocked(
            f"SHA-256不符：{spec.filename}，期望{spec.sha256}，实际{actual_sha}"
        )
    _validate_magic(path, spec)


def download_file(directory: Path, spec: FileSpec) -> dict[str, Any]:
    target = directory / spec.filename
    partial = directory / f".{spec.filename}.part"
    if target.exists():
        _verify_file(target, spec)
        return {
            "file_id": spec.file_id,
            "filename": spec.filename,
            "size_bytes": spec.size,
            "sha256": spec.sha256,
            "status": "verified_existing",
            "stable_download_url": spec.url,
        }
    if partial.exists() and (not partial.is_file() or _is_reparse_point(partial)):
        raise AcquisitionBlocked(f"临时路径不是普通文件：{partial}")

    request = Request(
        spec.url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        method="GET",
    )
    value = hashlib.sha256()
    total = 0
    try:
        with _opener().open(request, timeout=120) as response, partial.open("wb") as out:
            validate_url(response.geturl())
            declared_length = response.headers.get("Content-Length")
            if declared_length and int(declared_length) != spec.size:
                raise AcquisitionBlocked(
                    f"响应Content-Length漂移：{spec.filename}={declared_length}"
                )
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > spec.size:
                    raise AcquisitionBlocked(f"响应超过冻结字节数：{spec.filename}")
                value.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if total != spec.size or value.hexdigest() != spec.sha256:
            raise AcquisitionBlocked(
                f"下载完整性失败：{spec.filename}，字节={total}，SHA={value.hexdigest()}"
            )
        _validate_magic(partial, spec)
        os.replace(partial, target)
        _verify_file(target, spec)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return {
        "file_id": spec.file_id,
        "filename": spec.filename,
        "size_bytes": spec.size,
        "sha256": spec.sha256,
        "status": "downloaded_and_verified",
        "stable_download_url": spec.url,
    }


def _write_source_evidence(
    directory: Path,
    candidate: CandidateSpec,
    results: list[dict[str, Any]],
) -> None:
    metadata = {
        "capture_date": CAPTURE_DATE,
        "candidate_id": candidate.candidate_id,
        "canonical_identifier": candidate.canonical_identifier,
        "directory_name": candidate.directory_name,
        "license_spdx": candidate.license_spdx,
        "repository": candidate.repository,
        "rights_evidence_url": candidate.rights_evidence_url,
        "rights_status": candidate.rights_status,
        "stable_url": candidate.stable_url,
        "title": candidate.title,
        "version_label": candidate.version_label,
        "files": sorted(results, key=lambda row: row["file_id"]),
        "training_split_created": False,
        "training_weight_materialized": False,
    }
    _atomic_write(
        directory / "来源元数据.json",
        (
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=(
            "file_id",
            "filename",
            "size_bytes",
            "sha256",
            "status",
            "stable_download_url",
        ),
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(sorted(results, key=lambda row: row["file_id"]))
    _atomic_write(directory / "下载清单.tsv", buffer.getvalue().encode("utf-8"))


def acquire(candidate_ids: set[str] | None = None) -> dict[str, Any]:
    selection = load_download_selection()
    requested = set(selection) if candidate_ids is None else set(candidate_ids)
    unknown = requested.difference(selection)
    if unknown:
        raise AcquisitionBlocked(f"请求的候选未通过下载门禁：{sorted(unknown)}")
    summary: dict[str, Any] = {
        "capture_date": CAPTURE_DATE,
        "candidates": [],
        "training_split_created": False,
        "training_weight_materialized": False,
    }
    for candidate_id in sorted(requested):
        candidate = selection[candidate_id]
        directory = source_directory(candidate.directory_name)
        results = [download_file(directory, item) for item in candidate.files]
        _write_source_evidence(directory, candidate, results)
        summary["candidates"].append(
            {
                "candidate_id": candidate_id,
                "directory": candidate.directory_name,
                "file_count": len(results),
                "total_bytes": sum(row["size_bytes"] for row in results),
            }
        )
    summary["candidate_count"] = len(summary["candidates"])
    summary["file_count"] = sum(row["file_count"] for row in summary["candidates"])
    summary["total_bytes"] = sum(row["total_bytes"] for row in summary["candidates"])
    return summary


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="只下载一个固定candidate_id；可重复，不接受URL",
    )
    parser.add_argument("--list", action="store_true", help="只列出通过门禁的固定清单")
    args = parser.parse_args(argv)
    selection = load_download_selection()
    if args.list:
        print(
            json.dumps(
                {
                    key: {
                        "directory": value.directory_name,
                        "file_count": len(value.files),
                        "total_bytes": sum(item.size for item in value.files),
                    }
                    for key, value in sorted(selection.items())
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    requested = set(args.candidate) if args.candidate else None
    summary = acquire(requested)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
