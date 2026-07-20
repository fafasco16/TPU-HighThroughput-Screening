"""从官方固定端点下载第四批精选 TPU/PU 开放数据。

入选内容包括一个 Mendeley Data 实验数据集和八个 ACS Figshare 支持信息。
下载器冻结记录 ID、版本、DOI、文件名、字节数和官方散列；所有下载先写
``.part``，经散列和容器魔数检查后再原子替换。短期 S3 签名重定向只在内存
中使用，绝不写入元数据或清单。

运行：

    python 代码/获取/下载第四批精选开放数据.py
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
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "01_原始数据" / "外部数据" / "新增开放数据"
CAPTURE_DATE = "2026-07-20"
USER_AGENT = "TPU-HighThroughput-Screening/0.4 (+research data acquisition)"

MENDELEY_HOST = "data.mendeley.com"
MENDELEY_CACHE_HOST = "prod-dcd-datasets-cache-zipfiles.s3.eu-west-1.amazonaws.com"
FIGSHARE_API_HOST = "api.figshare.com"
FIGSHARE_DOWNLOAD_HOST = "ndownloader.figshare.com"
FIGSHARE_CACHE_HOST = "s3-eu-west-1.amazonaws.com"
CROSSREF_HOST = "api.crossref.org"
ALLOWED_HOSTS = frozenset(
    {
        MENDELEY_HOST,
        MENDELEY_CACHE_HOST,
        FIGSHARE_API_HOST,
        FIGSHARE_DOWNLOAD_HOST,
        FIGSHARE_CACHE_HOST,
        CROSSREF_HOST,
    }
)


@dataclass(frozen=True)
class MendeleySpec:
    directory: str
    dataset_id: str
    version: int
    doi: str
    archive_name: str
    size: int
    sha256: str

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


@dataclass(frozen=True)
class FigshareSpec:
    directory: str
    article_id: int
    supplement_doi: str
    resource_doi: str
    file_id: int
    filename: str
    size: int
    md5: str
    sha256: str

    @property
    def api_url(self) -> str:
        return f"https://{FIGSHARE_API_HOST}/v2/articles/{self.article_id}"

    @property
    def stable_download_url(self) -> str:
        return f"https://{FIGSHARE_DOWNLOAD_HOST}/files/{self.file_id}"

    @property
    def crossref_url(self) -> str:
        return f"https://{CROSSREF_HOST}/works/{quote(self.resource_doi, safe='')}"


MENDELEY = MendeleySpec(
    directory="Mendeley_TPU压缩打印DOE",
    dataset_id="7zcd9bmmg5",
    version=1,
    doi="10.17632/7zcd9bmmg5.1",
    archive_name="7zcd9bmmg5-1.zip",
    size=1_717_731,
    sha256="0b26707846f5cd23d2f843eb30d90ad24e548fce277a2cbffa5555348d226397",
)

FIGSHARE_SOURCES = (
    FigshareSpec(
        "ACS_Figshare_TPU退火硬段聚集",
        28_906_446,
        "10.1021/acs.macromol.5c00142.s001",
        "10.1021/acs.macromol.5c00142",
        54_105_219,
        "ma5c00142_si_001.pdf",
        1_527_655,
        "59a3b82cdcc0df35c2be9ac01b114f3e",
        "06baadbcd7cdf81a2e6d66dd9cc06b087dc2eb86dbe18ec65827402d0dfd7983",
    ),
    FigshareSpec(
        "ACS_Figshare_双相演化聚氨酯",
        29_074_233,
        "10.1021/acsmaterialslett.5c00732.s001",
        "10.1021/acsmaterialslett.5c00732",
        54_565_746,
        "tz5c00732_si_001.pdf",
        1_176_571,
        "6df014a8c3993882cde3b56f12e15772",
        "5bdedae10fcaff85da215a98a5dadfe7b0608ea6d14ba7dcc1adcbbc468938c9",
    ),
    FigshareSpec(
        "ACS_Figshare_PLA立构复合TPU",
        31_333_274,
        "10.1021/acs.macromol.5c03502.s001",
        "10.1021/acs.macromol.5c03502",
        61_883_048,
        "ma5c03502_si_001.pdf",
        5_618_558,
        "8fba03dd36266d50c7158ab51e41fcc5",
        "c4d5ec8522eaccd52a2a208809efa9a6f4fccb3555aaeb733f0119398cfc9ec6",
    ),
    FigshareSpec(
        "ACS_Figshare_呋喃高强聚氨酯",
        31_429_142,
        "10.1021/acs.macromol.5c03627.s001",
        "10.1021/acs.macromol.5c03627",
        62_213_765,
        "ma5c03627_si_001.pdf",
        2_261_687,
        "1bf5ddb7bf0ba6cd2d523918bb934ffe",
        "1b85a8294ce375e9b7f7cf314df369eaf7edfa9713a2ff6031aae74330df9108",
    ),
    FigshareSpec(
        "ACS_Figshare_聚酰亚胺回收链扩剂PU",
        31_614_502,
        "10.1021/acsapm.5c04872.s001",
        "10.1021/acsapm.5c04872",
        62_586_220,
        "ap5c04872_si_001.pdf",
        922_002,
        "14370e1b742801823822070d03509ecd",
        "c18bb54c66f7182cff03508067f7def63ee417fbdbdbfe29067ba568849bedea",
    ),
    FigshareSpec(
        "ACS_Figshare_二氧化碳共聚酯聚氨酯",
        31_989_433,
        "10.1021/acsmacrolett.6c00123.s001",
        "10.1021/acsmacrolett.6c00123",
        63_654_607,
        "mz6c00123_si_001.pdf",
        1_392_127,
        "b5e061b8e3b53d63077f169f87c0b67c",
        "a8770b0aee18e63efe119807c745378d060ae81fbcd131f9734dee55f2e4406e",
    ),
    FigshareSpec(
        "ACS_Figshare_聚碳酸酯大分子二醇TPU",
        32_256_977,
        "10.1021/acsapm.6c00646.s001",
        "10.1021/acsapm.6c00646",
        64_494_752,
        "ap6c00646_si_001.pdf",
        970_780,
        "48c4426508ad45ea2dc1f0e3d7b75d38",
        "2bd71204aa0807379e5f27a1e30de02ca0e1d4b981b3251c1e1567c1ad0109ec",
    ),
    FigshareSpec(
        "ACS_Figshare_氢键纳米结构TPU",
        32_567_339,
        "10.1021/acs.macromol.6c00352.s001",
        "10.1021/acs.macromol.6c00352",
        65_244_953,
        "ma6c00352_si_001.pdf",
        2_077_355,
        "70d5cd29615128fd56d045d44fe1750a",
        "29d8b451025a86acb3c075a6cb7c29428b725e7c40083a70d271500444b93765",
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


def require_component(name: str) -> None:
    if not name or name in {".", ".."}:
        raise AcquisitionBlocked(f"非法路径分量：{name!r}")
    if Path(name).name != name or any(mark in name for mark in ("/", "\\")):
        raise AcquisitionBlocked(f"路径分量越界：{name!r}")
    if name.endswith((".", " ")) or ":" in name:
        raise AcquisitionBlocked(f"Windows 不安全路径分量：{name!r}")


def validate_url(url: str, allowed: frozenset[str] = ALLOWED_HOSTS) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in allowed:
        raise AcquisitionBlocked(f"拒绝非 HTTPS 或非白名单端点：{url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionBlocked(f"端点含用户信息或片段：{url}")
    return host


def source_directory(name: str) -> Path:
    require_component(name)
    root = DATA_ROOT.resolve(strict=True)
    if (
        not DATA_ROOT.is_dir()
        or is_reparse_point(DATA_ROOT)
        or not same_path(root, DATA_ROOT)
    ):
        raise AcquisitionBlocked(f"原始数据根不是普通目录：{DATA_ROOT}")
    target = DATA_ROOT / name
    if target.exists():
        if not target.is_dir() or is_reparse_point(target):
            raise AcquisitionBlocked(f"来源目标不是普通目录：{target}")
    else:
        target.mkdir()
    if not same_path(target.resolve(strict=True).parent, root):
        raise AcquisitionBlocked(f"来源目录逃逸：{target}")
    return target


def digest(path: Path, algorithm: str) -> str:
    value = (
        hashlib.md5(usedforsecurity=False)
        if algorithm == "md5"
        else hashlib.new(algorithm)
    )
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir() or is_reparse_point(path.parent):
        raise AcquisitionBlocked(f"输出目录不是普通目录：{path.parent}")
    if path.exists() and (not path.is_file() or is_reparse_point(path)):
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
        if not temporary.is_file() or is_reparse_point(temporary):
            raise AcquisitionBlocked(f"原子临时文件异常：{temporary}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RedirectPolicy(HTTPRedirectHandler):
    max_redirections = 4

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        absolute = urljoin(req.full_url, newurl)
        source = validate_url(req.full_url)
        target = validate_url(absolute)
        allowed_transitions = {
            MENDELEY_HOST: {MENDELEY_HOST, MENDELEY_CACHE_HOST},
            MENDELEY_CACHE_HOST: {MENDELEY_CACHE_HOST},
            FIGSHARE_DOWNLOAD_HOST: {FIGSHARE_DOWNLOAD_HOST, FIGSHARE_CACHE_HOST},
            FIGSHARE_CACHE_HOST: {FIGSHARE_CACHE_HOST},
            FIGSHARE_API_HOST: {FIGSHARE_API_HOST},
            CROSSREF_HOST: {CROSSREF_HOST},
        }
        if target not in allowed_transitions.get(source, set()):
            raise AcquisitionBlocked(f"重定向越出固定边界：{source} -> {target}")
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def open_request(request: Request, *, timeout: int):
    validate_url(request.full_url)
    response = build_opener(RedirectPolicy()).open(request, timeout=timeout)
    validate_url(response.geturl())
    return response


def request_json_capture(url: str, *, attempts: int = 3) -> tuple[Any, dict[str, object]]:
    """返回解析 JSON 及精确响应字节快照，便于日后复核 API 漂移。"""
    validate_url(url)
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with open_request(request, timeout=60) as response:
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
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise AcquisitionBlocked(f"官方 API 请求失败：{url}: {error}")


def verify_container(path: Path, *, expected_suffix: str | None = None) -> None:
    suffix = expected_suffix.casefold() if expected_suffix else path.suffix.casefold()
    with path.open("rb") as handle:
        head = handle.read(8)
    if suffix == ".pdf" and not head.startswith(b"%PDF-"):
        raise AcquisitionBlocked(f"PDF 魔数错误：{path.name}")
    if suffix == ".zip":
        if not head.startswith(b"PK"):
            raise AcquisitionBlocked(f"ZIP 魔数错误：{path.name}")
        try:
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
        except (OSError, zipfile.BadZipFile) as exc:
            raise AcquisitionBlocked(f"ZIP 容器无效：{path.name}: {exc}") from exc
        if bad:
            raise AcquisitionBlocked(f"ZIP 成员 CRC 错误：{bad}")


def verified_existing(path: Path, size: int, algorithm: str, expected: str) -> bool:
    if not path.exists():
        return False
    if not path.is_file() or is_reparse_point(path):
        raise AcquisitionBlocked(f"目标不是普通文件：{path}")
    if path.stat().st_size != size or digest(path, algorithm) != expected:
        raise AcquisitionBlocked(f"既有正式文件完整性失败，拒绝覆盖：{path}")
    verify_container(path)
    return True


def download(
    url: str,
    target: Path,
    *,
    size: int,
    algorithm: str,
    expected: str,
    final_hosts: frozenset[str],
    attempts: int = 3,
) -> str:
    if verified_existing(target, size, algorithm, expected):
        return "reused_verified"
    partial = target.with_name(target.name + ".part")
    if partial.exists() and (not partial.is_file() or is_reparse_point(partial)):
        raise AcquisitionBlocked(f"断点对象不是普通文件：{partial}")
    if partial.exists() and partial.stat().st_size > size:
        partial.unlink()
    if partial.exists() and partial.stat().st_size == size:
        if digest(partial, algorithm) != expected:
            partial.unlink()
        else:
            verify_container(partial, expected_suffix=target.suffix)
            os.replace(partial, target)
            return "resumed_verified"

    error: Exception | None = None
    entity_tag: str | None = None
    for attempt in range(attempts):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            if entity_tag:
                headers["If-Range"] = entity_tag
        try:
            response = open_request(Request(url, headers=headers), timeout=180)
            status = int(getattr(response, "status", response.getcode()))
            final_host = validate_url(response.geturl())
            if final_host not in final_hosts:
                response.close()
                raise AcquisitionBlocked(f"下载最终主机不在来源白名单：{final_host}")
            if offset and status != 206:
                response.close()
                with partial.open("wb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                offset = 0
                response = open_request(
                    Request(
                        url,
                        headers={
                            "User-Agent": USER_AGENT,
                            "Accept": "application/octet-stream",
                        },
                    ),
                    timeout=180,
                )
                status = int(getattr(response, "status", response.getcode()))
                final_host = validate_url(response.geturl())
                if final_host not in final_hosts:
                    response.close()
                    raise AcquisitionBlocked(f"重启下载最终主机异常：{final_host}")
            if status not in {200, 206}:
                response.close()
                raise AcquisitionBlocked(f"下载 HTTP 状态异常：{status}")
            if not offset and status != 200:
                response.close()
                raise AcquisitionBlocked(f"首次下载必须返回200：{status}")
            content_length_text = str(response.headers.get("Content-Length", ""))
            if not content_length_text.isdecimal():
                response.close()
                partial.unlink(missing_ok=True)
                raise AcquisitionBlocked("下载响应缺少精确Content-Length")
            content_length = int(content_length_text)
            if offset:
                content_range = str(response.headers.get("Content-Range", ""))
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if match is None:
                    response.close()
                    partial.unlink(missing_ok=True)
                    raise AcquisitionBlocked(f"断点Content-Range非法：{content_range!r}")
                start, end, total = (int(value) for value in match.groups())
                if (
                    start != offset
                    or end != size - 1
                    or total != size
                    or content_length != size - offset
                ):
                    response.close()
                    partial.unlink(missing_ok=True)
                    raise AcquisitionBlocked(
                        f"断点范围与冻结大小不一致：{content_range!r}; "
                        f"Content-Length={content_length}"
                    )
            elif content_length != size:
                response.close()
                partial.unlink(missing_ok=True)
                raise AcquisitionBlocked(f"完整响应大小头漂移：{content_length}/{size}")
            response_tag = str(response.headers.get("ETag", "")).strip()
            if response_tag:
                if entity_tag is not None and response_tag != entity_tag:
                    response.close()
                    partial.unlink(missing_ok=True)
                    raise AcquisitionBlocked("断点响应ETag漂移")
                entity_tag = response_tag
            written = offset
            with response, partial.open("ab" if offset else "wb") as handle:
                remaining = size - offset
                while True:
                    block = response.read(min(4 * 1024 * 1024, remaining + 1))
                    if not block:
                        break
                    if len(block) > remaining:
                        raise AcquisitionBlocked(f"下载超过冻结大小：{target.name}")
                    handle.write(block)
                    written += len(block)
                    remaining -= len(block)
                handle.flush()
                os.fsync(handle.fileno())
            if partial.stat().st_size == size:
                break
            error = AcquisitionBlocked(f"下载未完成：{partial.stat().st_size}/{size}")
        except AcquisitionBlocked:
            partial.unlink(missing_ok=True)
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            error = exc
        if attempt + 1 < attempts:
            time.sleep(2**attempt)
    else:
        raise AcquisitionBlocked(f"下载失败：{target.name}: {error}")

    if partial.stat().st_size != size or digest(partial, algorithm) != expected:
        partial.unlink(missing_ok=True)
        raise AcquisitionBlocked(f"下载完整性失败：{target.name}")
    verify_container(partial, expected_suffix=target.suffix)
    os.replace(partial, target)
    return "downloaded_verified"


def render_manifest(rows: list[dict[str, object]]) -> bytes:
    columns = [
        "provider",
        "source_directory",
        "record_id",
        "version",
        "supplement_doi",
        "resource_doi",
        "filename",
        "bytes",
        "official_hash_algorithm",
        "official_hash",
        "local_sha256",
        "stable_download_url",
        "local_state",
        "signed_redirect_url_persisted",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=columns, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def acquire_mendeley(spec: MendeleySpec) -> dict[str, object]:
    snapshot, snapshot_capture = request_json_capture(spec.snapshot_url)
    versions, versions_capture = request_json_capture(spec.versions_url)
    zip_metadata, zip_capture = request_json_capture(spec.zip_metadata_url)
    if not isinstance(snapshot, dict) or not isinstance(zip_metadata, dict):
        raise AcquisitionBlocked("Mendeley API 返回类型错误")
    if (
        snapshot.get("id") != spec.dataset_id
        or int(snapshot.get("version", -1)) != spec.version
        or str(snapshot.get("doi", "")).casefold() != spec.doi.casefold()
        or bool(snapshot.get("is_metadata_only"))
    ):
        raise AcquisitionBlocked("Mendeley 身份或版本漂移")
    licence = snapshot.get("licence") or {}
    if str(licence.get("short_name", "")).casefold() != "cc by 4.0":
        raise AcquisitionBlocked("Mendeley 许可证漂移")
    if (
        not isinstance(versions, list)
        or len(versions) != 1
        or int(versions[0].get("version", -1)) != 1
    ):
        raise AcquisitionBlocked("Mendeley 版本清单漂移")
    if (
        str(zip_metadata.get("status")) != "FINISH"
        or int(zip_metadata.get("size", -1)) != spec.size
        or str(zip_metadata.get("sha256_hash", "")).lower() != spec.sha256
    ):
        raise AcquisitionBlocked("Mendeley ZIP 元数据漂移")
    cache_url = str(zip_metadata.get("url", ""))
    if validate_url(cache_url, frozenset({MENDELEY_CACHE_HOST})) != MENDELEY_CACHE_HOST:
        raise AcquisitionBlocked("Mendeley ZIP 缓存主机漂移")
    if urlsplit(cache_url).query:
        raise AcquisitionBlocked("Mendeley ZIP 元数据意外含签名参数")

    directory = source_directory(spec.directory)
    target = directory / spec.archive_name
    state = download(
        spec.stable_download_url,
        target,
        size=spec.size,
        algorithm="sha256",
        expected=spec.sha256,
        final_hosts=frozenset({MENDELEY_CACHE_HOST}),
    )
    normalized = {
        "captured_on": CAPTURE_DATE,
        "raw_api_capture_format": "exact_response_bytes_base64_with_sha256",
        "raw_api_captures": [snapshot_capture, versions_capture, zip_capture],
        "provider": "Mendeley Data",
        "dataset_id": spec.dataset_id,
        "version": spec.version,
        "doi": spec.doi,
        "title": snapshot.get("name"),
        "description": snapshot.get("description"),
        "method": snapshot.get("method"),
        "publish_date": snapshot.get("publish_date"),
        "owner": (snapshot.get("owner") or {}).get("display_name"),
        "contributors": [
            {"first_name": item.get("first_name"), "last_name": item.get("last_name")}
            for item in (snapshot.get("contributors") or [])
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
        "archive": {
            "filename": spec.archive_name,
            "bytes": spec.size,
            "sha256": spec.sha256,
        },
        "signed_redirect_url_persisted": False,
    }
    row = {
        "provider": "Mendeley Data",
        "source_directory": spec.directory,
        "record_id": spec.dataset_id,
        "version": spec.version,
        "supplement_doi": spec.doi,
        "resource_doi": "",
        "filename": spec.archive_name,
        "bytes": spec.size,
        "official_hash_algorithm": "sha256",
        "official_hash": spec.sha256,
        "local_sha256": spec.sha256,
        "stable_download_url": spec.stable_download_url,
        "local_state": "verified_present",
        "signed_redirect_url_persisted": "false",
    }
    atomic_write(
        directory / "官方API元数据.json",
        (
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
    )
    atomic_write(directory / "官方文件清单.tsv", render_manifest([row]))
    print(f"{spec.directory}: {spec.archive_name}: {state}", flush=True)
    return {"source": spec.directory, "file": spec.archive_name, "state": state}


def acquire_figshare(spec: FigshareSpec) -> dict[str, object]:
    raw, figshare_capture = request_json_capture(spec.api_url)
    if not isinstance(raw, dict):
        raise AcquisitionBlocked(f"Figshare API 返回类型错误：{spec.article_id}")
    licence = raw.get("license") or {}
    if (
        int(raw.get("id", -1)) != spec.article_id
        or int(raw.get("version", -1)) != 1
        or str(raw.get("doi", "")).casefold() != spec.supplement_doi.casefold()
        or str(raw.get("resource_doi", "")).casefold() != spec.resource_doi.casefold()
        or str(licence.get("name", "")).casefold() != "cc by-nc 4.0"
        or not bool(raw.get("is_public"))
        or bool(raw.get("is_metadata_record"))
        or bool(raw.get("download_disabled"))
    ):
        raise AcquisitionBlocked(
            f"Figshare 身份、版本、许可或开放状态漂移：{spec.article_id}"
        )
    files = raw.get("files") or []
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise AcquisitionBlocked(f"Figshare 文件清单不再是唯一文件：{spec.article_id}")
    item = files[0]
    if (
        int(item.get("id", -1)) != spec.file_id
        or str(item.get("name", "")) != spec.filename
        or int(item.get("size", -1)) != spec.size
        or str(item.get("computed_md5", "")).lower() != spec.md5
        or str(item.get("supplied_md5", "")).lower() != spec.md5
        or bool(item.get("is_link_only"))
    ):
        raise AcquisitionBlocked(f"Figshare 文件身份或散列漂移：{spec.article_id}")
    official_download = str(item.get("download_url", ""))
    if official_download != spec.stable_download_url:
        raise AcquisitionBlocked(f"Figshare 稳定下载 URL 漂移：{spec.article_id}")

    crossref_envelope, crossref_capture = request_json_capture(spec.crossref_url)
    if (
        not isinstance(crossref_envelope, dict)
        or crossref_envelope.get("status") != "ok"
    ):
        raise AcquisitionBlocked(f"Crossref 主论文记录异常：{spec.resource_doi}")
    crossref = crossref_envelope.get("message")
    if not isinstance(crossref, dict):
        raise AcquisitionBlocked(f"Crossref 主论文 message 缺失：{spec.resource_doi}")
    if (
        str(crossref.get("DOI", "")).casefold() != spec.resource_doi.casefold()
        or str(crossref.get("type", "")) != "journal-article"
        or "American Chemical Society" not in str(crossref.get("publisher", ""))
    ):
        raise AcquisitionBlocked(f"Crossref 主论文身份漂移：{spec.resource_doi}")
    date_parts = ((crossref.get("published") or {}).get("date-parts") or [[]])[0]
    figshare_year = int(str(raw.get("published_date", ""))[:4])
    if not date_parts or int(date_parts[0]) != figshare_year:
        raise AcquisitionBlocked(
            f"Crossref 与 Figshare 主论文年份冲突：{spec.resource_doi}"
        )
    primary_article = {
        "doi": crossref.get("DOI"),
        "title": (crossref.get("title") or [None])[0],
        "authors": [
            {
                "given": author.get("given"),
                "family": author.get("family"),
                "orcid": author.get("ORCID"),
            }
            for author in (crossref.get("author") or [])
            if isinstance(author, dict)
        ],
        "journal": (crossref.get("container-title") or [None])[0],
        "publisher": crossref.get("publisher"),
        "published_date_parts": [int(part) for part in date_parts],
        "volume": crossref.get("volume"),
        "issue": crossref.get("issue"),
        "pages": crossref.get("page"),
        "type": crossref.get("type"),
    }
    citation_quality_notes = []
    if "(1753)" in str(raw.get("citation", "")):
        citation_quality_notes.append(
            "ACS Figshare 自动 citation 年份 1753 与 Crossref/发布日 2026 冲突；正式引用使用 Crossref 主论文年份"
        )

    directory = source_directory(spec.directory)
    target = directory / spec.filename
    state = download(
        spec.stable_download_url,
        target,
        size=spec.size,
        algorithm="md5",
        expected=spec.md5,
        final_hosts=frozenset({FIGSHARE_CACHE_HOST}),
    )
    if digest(target, "sha256") != spec.sha256:
        raise AcquisitionBlocked(f"Figshare 本地 SHA256 漂移：{spec.filename}")
    normalized = {
        "captured_on": CAPTURE_DATE,
        "raw_api_capture_format": "exact_response_bytes_base64_with_sha256",
        "raw_api_captures": [figshare_capture, crossref_capture],
        "provider": "ACS Figshare",
        "article_id": spec.article_id,
        "version": 1,
        "supplement_doi": spec.supplement_doi,
        "resource_doi": spec.resource_doi,
        "title": raw.get("title"),
        "description": raw.get("description"),
        "published_date": raw.get("published_date"),
        "citation": raw.get("citation"),
        "authors": [
            {"full_name": author.get("full_name"), "orcid_id": author.get("orcid_id")}
            for author in (raw.get("authors") or [])
            if isinstance(author, dict)
        ],
        "license": {"name": licence.get("name"), "url": licence.get("url")},
        "related_materials": [
            {
                "identifier": relation.get("identifier"),
                "relation": relation.get("relation"),
                "identifier_type": relation.get("identifier_type"),
            }
            for relation in (raw.get("related_materials") or [])
            if isinstance(relation, dict)
        ],
        "primary_article_crossref": primary_article,
        "citation_quality_notes": citation_quality_notes,
        "official_endpoints": {
            "api": spec.api_url,
            "stable_download": spec.stable_download_url,
            "primary_article_crossref": spec.crossref_url,
        },
        "file": {
            "file_id": spec.file_id,
            "filename": spec.filename,
            "bytes": spec.size,
            "official_md5": spec.md5,
            "local_sha256": spec.sha256,
        },
        "signed_redirect_url_persisted": False,
    }
    row = {
        "provider": "ACS Figshare",
        "source_directory": spec.directory,
        "record_id": spec.article_id,
        "version": 1,
        "supplement_doi": spec.supplement_doi,
        "resource_doi": spec.resource_doi,
        "filename": spec.filename,
        "bytes": spec.size,
        "official_hash_algorithm": "md5",
        "official_hash": spec.md5,
        "local_sha256": spec.sha256,
        "stable_download_url": spec.stable_download_url,
        "local_state": "verified_present",
        "signed_redirect_url_persisted": "false",
    }
    atomic_write(
        directory / "官方API元数据.json",
        (
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
    )
    atomic_write(directory / "官方文件清单.tsv", render_manifest([row]))
    print(f"{spec.directory}: {spec.filename}: {state}", flush=True)
    return {"source": spec.directory, "file": spec.filename, "state": state}


def validate_frozen_manifest() -> None:
    names = [MENDELEY.directory, *(spec.directory for spec in FIGSHARE_SOURCES)]
    if len(names) != 9 or len(set(names)) != 9:
        raise AcquisitionBlocked("固定来源数量或目录唯一性错误")
    require_component(MENDELEY.directory)
    require_component(MENDELEY.archive_name)
    if len(MENDELEY.sha256) != 64 or MENDELEY.size <= 0:
        raise AcquisitionBlocked("Mendeley 冻结散列或大小非法")
    for spec in FIGSHARE_SOURCES:
        require_component(spec.directory)
        require_component(spec.filename)
        if (
            spec.article_id <= 0
            or spec.file_id <= 0
            or spec.size <= 0
            or len(spec.md5) != 32
            or len(spec.sha256) != 64
        ):
            raise AcquisitionBlocked(f"Figshare 冻结字段非法：{spec.directory}")
        validate_url(spec.api_url, frozenset({FIGSHARE_API_HOST}))
        validate_url(spec.stable_download_url, frozenset({FIGSHARE_DOWNLOAD_HOST}))
        validate_url(spec.crossref_url, frozenset({CROSSREF_HOST}))


def main() -> int:
    validate_frozen_manifest()
    results = [acquire_mendeley(MENDELEY)]
    results.extend(acquire_figshare(spec) for spec in FIGSHARE_SOURCES)
    print(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcquisitionBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
