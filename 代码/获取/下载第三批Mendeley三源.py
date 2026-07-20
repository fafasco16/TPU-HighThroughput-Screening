"""从 Mendeley Data 固定 v1 官方端点下载第三批三个 TPU 数据集。

本脚本只接受代码内冻结的数据集 ID、版本、DOI、ZIP 字节数与 SHA256。
下载始终从 Mendeley 的稳定 ``public-api/zip`` 入口发起；允许的唯一跨站
重定向目标是已核验的 Mendeley ZIP 缓存 S3 主机，签名 URL 不会写入清单。
断点文件使用 ``.part``，完整性通过后才原子替换正式归档；官方元数据与
文件清单同样采用原子替换，因此重复运行得到稳定输出。

运行：

    python 代码/获取/下载第三批Mendeley三源.py
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "01_原始数据" / "外部数据" / "新增开放数据"
CAPTURE_DATE = "2026-07-20"
USER_AGENT = "TPU-HighThroughput-Screening/0.3 (+research data acquisition)"
MENDELEY_HOST = "data.mendeley.com"
ZIP_CACHE_HOST = "prod-dcd-datasets-cache-zipfiles.s3.eu-west-1.amazonaws.com"
ALLOWED_REDIRECT_HOSTS = frozenset({MENDELEY_HOST, ZIP_CACHE_HOST})


@dataclass(frozen=True)
class SourceSpec:
    directory: str
    dataset_id: str
    version: int
    doi: str
    archive_name: str
    archive_size: int
    archive_sha256: str

    @property
    def snapshot_url(self) -> str:
        return (
            f"https://{MENDELEY_HOST}/public-api/datasets/"
            f"{self.dataset_id}/snapshot/{self.version}"
        )

    @property
    def versions_url(self) -> str:
        return f"https://{MENDELEY_HOST}/public-api/datasets/{self.dataset_id}/versions"

    @property
    def zip_metadata_url(self) -> str:
        return (
            f"https://{MENDELEY_HOST}/api/datasets-v2/datasets/"
            f"{self.dataset_id}/zip?version={self.version}"
        )

    @property
    def stable_download_url(self) -> str:
        return (
            f"https://{MENDELEY_HOST}/public-api/zip/"
            f"{self.dataset_id}/download/{self.version}"
        )


SOURCES = (
    SourceSpec(
        directory="Mendeley_商业TPU温度疲劳多工况",
        dataset_id="hc6npzvw3m",
        version=1,
        doi="10.17632/hc6npzvw3m.1",
        archive_name="hc6npzvw3m-1.zip",
        archive_size=41_218_060,
        archive_sha256="1a47a26b3c5ac93a7b56ef8e94c2e2b0308a5ceec34ed407435586e1744a65ab",
    ),
    SourceSpec(
        directory="Mendeley_FDM_TPU晶格与基材力学",
        dataset_id="dbzdkz95f8",
        version=1,
        doi="10.17632/dbzdkz95f8.1",
        archive_name="dbzdkz95f8-1.zip",
        archive_size=210_709_465,
        archive_sha256="3cf82a71f83cfa46925556b5e0e9a901e5d184aeeb415ed2d823cca1d4674d3c",
    ),
    SourceSpec(
        directory="Mendeley_TPU实验仿真曲线",
        dataset_id="kysnxmy7xw",
        version=1,
        doi="10.17632/kysnxmy7xw.1",
        archive_name="kysnxmy7xw-1.zip",
        archive_size=4_543_043,
        archive_sha256="3585c67dac25988b651999d4a9b25ca3fb55da1a25b05386fbbf8fa8a87cf55e",
    ),
)


class AcquisitionBlocked(RuntimeError):
    """官方记录、网络边界或本地完整性不满足冻结协议。"""


def is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(flag and attributes & flag) or bool(is_junction())


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right))
    )


def require_safe_component(name: str) -> None:
    if not name or name in {".", ".."}:
        raise AcquisitionBlocked(f"非法路径分量：{name!r}")
    if Path(name).name != name or any(separator in name for separator in ("/", "\\")):
        raise AcquisitionBlocked(f"路径分量越界：{name!r}")
    if name.endswith((".", " ")) or ":" in name:
        raise AcquisitionBlocked(f"Windows 不安全路径分量：{name!r}")


def validate_https_url(url: str, allowed_hosts: frozenset[str]) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in allowed_hosts:
        raise AcquisitionBlocked(f"拒绝非 HTTPS 或非白名单端点：{url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionBlocked(f"端点含用户信息或片段：{url}")
    return host


def ensure_source_directory(spec: SourceSpec) -> Path:
    require_safe_component(spec.directory)
    root = DATA_ROOT
    resolved_root = root.resolve(strict=True)
    if not root.is_dir() or not same_path(root, resolved_root) or is_reparse_point(root):
        raise AcquisitionBlocked(f"新增开放数据根不是普通目录：{root}")
    target = root / spec.directory
    if target.exists():
        if not target.is_dir() or is_reparse_point(target):
            raise AcquisitionBlocked(f"来源目标不是普通目录：{target}")
    else:
        target.mkdir()
    if not same_path(target.resolve(strict=True).parent, resolved_root):
        raise AcquisitionBlocked(f"来源目录逃逸：{target}")
    return target


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    parent = path.parent
    if (
        not parent.is_dir()
        or is_reparse_point(parent)
        or not same_path(parent.resolve(strict=True), parent)
    ):
        raise AcquisitionBlocked(f"输出目录不是普通目录：{parent}")
    if path.exists() and (not path.is_file() or is_reparse_point(path)):
        raise AcquisitionBlocked(f"拒绝覆盖非普通文件：{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not temporary.is_file() or is_reparse_point(temporary):
            raise AcquisitionBlocked(f"原子临时文件异常：{temporary}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class WhitelistRedirectHandler(HTTPRedirectHandler):
    """仅允许 Mendeley 稳定入口跳转到固定 ZIP 缓存主机。"""

    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        absolute = urljoin(req.full_url, newurl)
        source_host = validate_https_url(req.full_url, ALLOWED_REDIRECT_HOSTS)
        target_host = validate_https_url(absolute, ALLOWED_REDIRECT_HOSTS)
        if source_host == MENDELEY_HOST and target_host not in {
            MENDELEY_HOST,
            ZIP_CACHE_HOST,
        }:
            raise AcquisitionBlocked(f"Mendeley 重定向越出白名单：{absolute}")
        if source_host == ZIP_CACHE_HOST and target_host != ZIP_CACHE_HOST:
            raise AcquisitionBlocked(f"S3 重定向越出固定主机：{absolute}")
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def open_request(request: Request, *, timeout: int):
    validate_https_url(request.full_url, ALLOWED_REDIRECT_HOSTS)
    opener = build_opener(WhitelistRedirectHandler())
    response = opener.open(request, timeout=timeout)
    validate_https_url(response.geturl(), ALLOWED_REDIRECT_HOSTS)
    return response


def request_json_capture(url: str, *, attempts: int = 3) -> tuple[Any, dict[str, object]]:
    """返回解析 JSON 及保留精确响应字节的可审计快照。"""
    validate_https_url(url, frozenset({MENDELEY_HOST}))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with open_request(request, timeout=60) as response:
                if (response.geturl() != url) and (
                    validate_https_url(response.geturl(), ALLOWED_REDIRECT_HOSTS)
                    != MENDELEY_HOST
                ):
                    raise AcquisitionBlocked(f"API 被重定向到非 Mendeley 主机：{url}")
                payload = response.read()
                parsed = json.loads(payload.decode("utf-8"))
                return parsed, {
                    "request_url": url,
                    "final_url": response.geturl(),
                    "status": int(getattr(response, "status", response.getcode())),
                    "content_type": response.headers.get("Content-Type"),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "payload_bytes": len(payload),
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                    "payload_base64": base64.b64encode(payload).decode("ascii"),
                }
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise AcquisitionBlocked(f"官方 API 请求失败：{url}: {last_error}")


def validate_official_record(spec: SourceSpec) -> dict[str, Any]:
    snapshot, snapshot_capture = request_json_capture(spec.snapshot_url)
    versions, versions_capture = request_json_capture(spec.versions_url)
    zip_metadata, zip_capture = request_json_capture(spec.zip_metadata_url)
    if not isinstance(snapshot, dict) or not isinstance(zip_metadata, dict):
        raise AcquisitionBlocked(f"官方 API 返回类型异常：{spec.dataset_id}")
    if str(snapshot.get("id")) != spec.dataset_id:
        raise AcquisitionBlocked(f"数据集 ID 漂移：{spec.directory}")
    if int(snapshot.get("version", -1)) != spec.version:
        raise AcquisitionBlocked(f"数据集版本漂移：{spec.directory}")
    if str(snapshot.get("doi", "")).casefold() != spec.doi.casefold():
        raise AcquisitionBlocked(f"数据集 DOI 漂移：{spec.directory}")
    if bool(snapshot.get("is_metadata_only")):
        raise AcquisitionBlocked(f"数据集变成仅元数据记录：{spec.directory}")
    licence = snapshot.get("licence") or {}
    if str(licence.get("short_name", "")).casefold() != "cc by 4.0":
        raise AcquisitionBlocked(f"数据集许可证漂移：{spec.directory}")
    if not isinstance(versions, list) or versions != [
        {
            "version": spec.version,
            "publish_date": str(versions[0].get("publish_date")) if versions else "",
            "available": True,
        }
    ]:
        raise AcquisitionBlocked(f"版本清单不再是唯一可用 v1：{spec.directory}")
    if str(zip_metadata.get("status")) != "FINISH":
        raise AcquisitionBlocked(f"官方 ZIP 尚未完成：{spec.directory}")
    if int(zip_metadata.get("size", -1)) != spec.archive_size:
        raise AcquisitionBlocked(f"官方 ZIP 大小漂移：{spec.directory}")
    if str(zip_metadata.get("sha256_hash", "")).lower() != spec.archive_sha256:
        raise AcquisitionBlocked(f"官方 ZIP SHA256 漂移：{spec.directory}")
    unsigned_zip_url = str(zip_metadata.get("url", ""))
    if validate_https_url(unsigned_zip_url, frozenset({ZIP_CACHE_HOST})) != ZIP_CACHE_HOST:
        raise AcquisitionBlocked(f"官方 ZIP 缓存主机漂移：{spec.directory}")
    if urlsplit(unsigned_zip_url).query:
        raise AcquisitionBlocked(f"官方 ZIP 元数据意外包含临时签名：{spec.directory}")

    owner = snapshot.get("owner") or {}
    contributors = snapshot.get("contributors") or []
    normalized_contributors = [
        {
            "first_name": item.get("first_name"),
            "last_name": item.get("last_name"),
        }
        for item in contributors
        if isinstance(item, dict)
    ]
    return {
        "captured_on": CAPTURE_DATE,
        "raw_api_capture_format": "exact_response_bytes_base64_with_sha256",
        "raw_api_captures": [snapshot_capture, versions_capture, zip_capture],
        "provider": "Mendeley Data",
        "dataset_id": spec.dataset_id,
        "version": spec.version,
        "doi": spec.doi,
        "name": snapshot.get("name"),
        "publish_date": snapshot.get("publish_date"),
        "last_modification_date": snapshot.get("last_modification_date"),
        "owner": owner.get("display_name"),
        "contributors": normalized_contributors,
        "categories": [
            item.get("label")
            for item in (snapshot.get("categories") or [])
            if isinstance(item, dict)
        ],
        "license": {
            "short_name": licence.get("short_name"),
            "full_name": licence.get("full_name"),
            "url": licence.get("url"),
        },
        "official_endpoints": {
            "snapshot": spec.snapshot_url,
            "versions": spec.versions_url,
            "zip_metadata": spec.zip_metadata_url,
            "stable_download": spec.stable_download_url,
        },
        "redirect_policy": {
            "initial_host": MENDELEY_HOST,
            "allowed_zip_cache_host": ZIP_CACHE_HOST,
            "signed_redirect_url_persisted": False,
        },
        "archive": {
            "filename": spec.archive_name,
            "bytes": spec.archive_size,
            "sha256": spec.archive_sha256,
            "zip_status": zip_metadata.get("status"),
            "zip_created_on": zip_metadata.get("created_on"),
            "zip_modified_on": zip_metadata.get("modified_on"),
        },
    }


def verified_existing(path: Path, expected_size: int, expected_sha256: str) -> bool:
    if not path.exists():
        return False
    if not path.is_file() or is_reparse_point(path):
        raise AcquisitionBlocked(f"目标不是普通文件：{path}")
    if path.stat().st_size != expected_size or file_digest(path) != expected_sha256:
        raise AcquisitionBlocked(f"既有正式归档完整性失败，拒绝覆盖：{path}")
    return True


def download_archive(spec: SourceSpec, target: Path, *, attempts: int = 3) -> str:
    validate_https_url(spec.stable_download_url, frozenset({MENDELEY_HOST}))
    if verified_existing(target, spec.archive_size, spec.archive_sha256):
        return "reused_verified"

    partial = target.with_name(target.name + ".part")
    if partial.exists() and (not partial.is_file() or is_reparse_point(partial)):
        raise AcquisitionBlocked(f"断点文件不是普通文件：{partial}")
    if partial.exists() and partial.stat().st_size > spec.archive_size:
        partial.unlink()
    if partial.exists() and partial.stat().st_size == spec.archive_size:
        if file_digest(partial) != spec.archive_sha256:
            partial.unlink()
        else:
            os.replace(partial, target)
            return "resumed_verified"

    last_error: Exception | None = None
    entity_tag: str | None = None
    for attempt in range(1, attempts + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": USER_AGENT, "Accept": "application/zip"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            if entity_tag:
                headers["If-Range"] = entity_tag
        request = Request(spec.stable_download_url, headers=headers)
        try:
            response = open_request(request, timeout=180)
            status = int(getattr(response, "status", response.getcode()))
            final_host = validate_https_url(
                response.geturl(), frozenset({MENDELEY_HOST, ZIP_CACHE_HOST})
            )
            if final_host != ZIP_CACHE_HOST:
                response.close()
                raise AcquisitionBlocked(
                    f"稳定下载入口未落到固定 ZIP 缓存主机：{response.geturl()}"
                )
            if offset and status != 206:
                response.close()
                # 服务端忽略 Range 时，从零安全重启；仅覆盖本脚本专用 .part。
                with partial.open("wb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                offset = 0
                request = Request(spec.stable_download_url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/zip",
                })
                response = open_request(request, timeout=180)
                status = int(getattr(response, "status", response.getcode()))
                final_host = validate_https_url(
                    response.geturl(), frozenset({MENDELEY_HOST, ZIP_CACHE_HOST})
                )
                if final_host != ZIP_CACHE_HOST:
                    response.close()
                    raise AcquisitionBlocked("重新下载未落到固定 ZIP 缓存主机")
            if status not in {200, 206}:
                response.close()
                raise AcquisitionBlocked(f"ZIP 下载 HTTP 状态异常：{status}")
            if not offset and status != 200:
                response.close()
                raise AcquisitionBlocked(f"ZIP 首次下载必须返回200：{status}")
            content_length_text = str(response.headers.get("Content-Length", ""))
            if not content_length_text.isdecimal():
                response.close()
                partial.unlink(missing_ok=True)
                raise AcquisitionBlocked("ZIP 响应缺少精确 Content-Length")
            content_length = int(content_length_text)
            if offset:
                content_range = str(response.headers.get("Content-Range", ""))
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if match is None:
                    response.close()
                    partial.unlink(missing_ok=True)
                    raise AcquisitionBlocked(f"ZIP Content-Range 非法：{content_range!r}")
                start, end, total = (int(value) for value in match.groups())
                if (
                    start != offset
                    or end != spec.archive_size - 1
                    or total != spec.archive_size
                    or content_length != spec.archive_size - offset
                ):
                    response.close()
                    partial.unlink(missing_ok=True)
                    raise AcquisitionBlocked(
                        f"ZIP 断点范围与冻结大小不一致：{content_range!r}; "
                        f"Content-Length={content_length}"
                    )
            elif content_length != spec.archive_size:
                response.close()
                partial.unlink(missing_ok=True)
                raise AcquisitionBlocked(
                    f"ZIP 完整响应大小头漂移：{content_length}/{spec.archive_size}"
                )
            response_tag = str(response.headers.get("ETag", "")).strip()
            if response_tag:
                if entity_tag is not None and response_tag != entity_tag:
                    response.close()
                    partial.unlink(missing_ok=True)
                    raise AcquisitionBlocked("ZIP 断点响应 ETag 漂移")
                entity_tag = response_tag
            mode = "ab" if offset else "wb"
            written = offset
            with response, partial.open(mode) as handle:
                remaining = spec.archive_size - offset
                while True:
                    block = response.read(min(4 * 1024 * 1024, remaining + 1))
                    if not block:
                        break
                    if len(block) > remaining:
                        raise AcquisitionBlocked(f"下载超过冻结大小：{spec.archive_name}")
                    handle.write(block)
                    written += len(block)
                    remaining -= len(block)
                handle.flush()
                os.fsync(handle.fileno())
            if partial.stat().st_size == spec.archive_size:
                break
            last_error = AcquisitionBlocked(
                f"下载未完成：{partial.stat().st_size}/{spec.archive_size}"
            )
        except AcquisitionBlocked:
            partial.unlink(missing_ok=True)
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(2 ** (attempt - 1))
    else:
        raise AcquisitionBlocked(f"ZIP 下载失败：{spec.archive_name}: {last_error}")

    if partial.stat().st_size != spec.archive_size:
        partial.unlink(missing_ok=True)
        raise AcquisitionBlocked(
            f"ZIP 大小不完整：{partial.stat().st_size}/{spec.archive_size}"
        )
    actual_sha256 = file_digest(partial)
    if actual_sha256 != spec.archive_sha256:
        partial.unlink(missing_ok=True)
        raise AcquisitionBlocked(
            f"ZIP SHA256 不匹配：{actual_sha256}/{spec.archive_sha256}"
        )
    os.replace(partial, target)
    return "downloaded_verified"


def render_manifest(spec: SourceSpec) -> bytes:
    columns = [
        "source_directory",
        "provider",
        "dataset_id",
        "version",
        "doi",
        "filename",
        "bytes",
        "sha256",
        "stable_download_url",
        "redirect_cache_host",
        "local_state",
        "local_sha256",
    ]
    row = {
        "source_directory": spec.directory,
        "provider": "Mendeley Data",
        "dataset_id": spec.dataset_id,
        "version": spec.version,
        "doi": spec.doi,
        "filename": spec.archive_name,
        "bytes": spec.archive_size,
        "sha256": spec.archive_sha256,
        "stable_download_url": spec.stable_download_url,
        "redirect_cache_host": ZIP_CACHE_HOST,
        "local_state": "verified_present",
        "local_sha256": spec.archive_sha256,
    }
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def acquire_source(spec: SourceSpec) -> dict[str, object]:
    target_directory = ensure_source_directory(spec)
    normalized = validate_official_record(spec)
    archive_path = target_directory / spec.archive_name
    state = download_archive(spec, archive_path)
    # 下载完成后再次从正式路径复核，元数据和清单只描述已验证的本地状态。
    if not verified_existing(archive_path, spec.archive_size, spec.archive_sha256):
        raise AcquisitionBlocked(f"正式归档复核失败：{archive_path}")
    atomic_write(
        target_directory / "官方API元数据.json",
        (
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
    )
    atomic_write(target_directory / "官方文件清单.tsv", render_manifest(spec))
    print(f"{spec.directory}: {spec.archive_name}: {state}", flush=True)
    return {
        "source": spec.directory,
        "archive": spec.archive_name,
        "bytes": spec.archive_size,
        "sha256": spec.archive_sha256,
        "state": state,
    }


def validate_frozen_manifest() -> None:
    if len(SOURCES) != 3 or len({item.directory for item in SOURCES}) != 3:
        raise AcquisitionBlocked("固定来源数量或目录唯一性错误")
    if len({item.dataset_id for item in SOURCES}) != 3:
        raise AcquisitionBlocked("固定 Mendeley ID 重复")
    for spec in SOURCES:
        require_safe_component(spec.directory)
        require_safe_component(spec.archive_name)
        if spec.version != 1 or not re_fullmatch_dataset_id(spec.dataset_id):
            raise AcquisitionBlocked(f"固定 ID 或版本非法：{spec.directory}")
        if spec.archive_size <= 0 or len(spec.archive_sha256) != 64:
            raise AcquisitionBlocked(f"固定 ZIP 大小或 SHA256 非法：{spec.directory}")
        for url in (
            spec.snapshot_url,
            spec.versions_url,
            spec.zip_metadata_url,
            spec.stable_download_url,
        ):
            validate_https_url(url, frozenset({MENDELEY_HOST}))


def re_fullmatch_dataset_id(value: str) -> bool:
    return len(value) == 10 and value.isascii() and value.isalnum() and value.islower()


def main() -> int:
    validate_frozen_manifest()
    results = [acquire_source(spec) for spec in SOURCES]
    print(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcquisitionBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
