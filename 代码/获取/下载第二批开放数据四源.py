"""从固定官方端点下载 TPU 数据库第二批四个开放来源。

下载器只接受代码内冻结的记录、文件名、大小和 MD5。官方 API 若增加、删除或
修改文件，程序会在写入科学文件前失败。下载临时文件使用 ``.part`` 后缀；只有
大小和 MD5 同时通过才原子替换目标。原始数据目录受 Git 忽略。

运行：

    python 代码/获取/下载第二批开放数据四源.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
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
DATA_ROOT = PROJECT_ROOT / "数据/原始" / "外部数据" / "新增开放数据"
CAPTURE_DATE = "2026-07-20"
USER_AGENT = "TPU-HighThroughput-Screening/0.2 (+research data acquisition)"
EXPECTED_FILE_COUNT = 11
EXPECTED_EXCLUDED_FILE_COUNT = 12
ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {"zenodo.org", "api.figshare.com", "ndownloader.figshare.com"}
)


@dataclass(frozen=True)
class ExpectedFile:
    name: str
    size: int
    md5: str


@dataclass(frozen=True)
class SourceSpec:
    directory: str
    provider: str
    record_id: int
    doi: str
    api_url: str
    license_id: str
    required: tuple[ExpectedFile, ...]
    excluded_names: tuple[str, ...] = ()


SOURCES = (
    SourceSpec(
        directory="Zenodo_标准化弹性体表征",
        provider="zenodo",
        record_id=14983287,
        doi="10.5281/zenodo.14983287",
        api_url="https://zenodo.org/api/records/14983287",
        license_id="cc-by-4.0",
        required=(
            ExpectedFile(
                "Uniaxial compression.zip",
                73253,
                "1d445b2cc7a2393e62b8017f69710ca0",
            ),
            ExpectedFile(
                "Melting.zip", 308782, "505a998ae051622109428893947c6ca3"
            ),
            ExpectedFile(
                "Thermal degradation.zip",
                1158247,
                "4c4f05282ef44d818b453f8e4827b9fe",
            ),
            ExpectedFile(
                "Uniaxial tension.zip",
                1138881,
                "961fdf0eb7650941fcadf53b8650887d",
            ),
            ExpectedFile(
                "Curing.zip", 4052971, "5a2ce15e1dddc9054966977e9f5d9a63"
            ),
            ExpectedFile(
                "Stress relaxation.zip",
                80183980,
                "95a66a75f896c6b6653be2299d5e47a8",
            ),
            ExpectedFile(
                "Glass transition.zip",
                1346354,
                "d343517cf7b22209a1a657f5dcd8e13c",
            ),
        ),
    ),
    SourceSpec(
        directory="Zenodo_PU微球复合材料拉伸",
        provider="zenodo",
        record_id=6390478,
        doi="10.5281/zenodo.6390478",
        api_url="https://zenodo.org/api/records/6390478",
        license_id="cc-by-4.0",
        required=(
            ExpectedFile("Data_csv.zip", 780946, "edb09e11f8e525961ce468e00f8a36f6"),
            ExpectedFile("readme.md", 3480, "d94065b61d1482a60eb73a710a267fab"),
        ),
        excluded_names=(
            "211008_poro_00_spec_02b.zip",
            "211008_poro_00_spec_03b.zip",
            "211008_poro_05_spec_02.zip",
            "211008_poro_05_spec_03.zip",
            "211008_poro_10_spec_02.zip",
            "211008_poro_10_spec_03.zip",
            "211008_poro_15_spec_02.zip",
            "211008_poro_15_spec_03.zip",
            "211008_poro_20_spec_02.zip",
            "211008_poro_20_spec_03.zip",
            "211008_poro_25_spec_02.zip",
            "211008_poro_25_spec_03.zip",
        ),
    ),
    SourceSpec(
        directory="Figshare_PU高低速变形后应力松弛",
        provider="figshare",
        record_id=23635998,
        doi="10.6084/m9.figshare.23635998.v1",
        api_url="https://api.figshare.com/v2/articles/23635998",
        license_id="CC BY 4.0",
        required=(
            ExpectedFile(
                "rspa20220830_si_002.zip",
                8831991,
                "c23c7c9172c6fb467aa808160afd9613",
            ),
        ),
    ),
    SourceSpec(
        directory="Zenodo_TPU1301热黏弹黏塑本构",
        provider="zenodo",
        record_id=15370425,
        doi="10.5281/zenodo.15370425",
        api_url="https://zenodo.org/api/records/15370425",
        license_id="cc-by-4.0",
        required=(
            ExpectedFile(
                "ijss_2025_vevp_ScriptsForTestsImages.zip",
                450879687,
                "f2446a9be2b0c50d8169b4557e6e2a37",
            ),
        ),
    ),
)


class AcquisitionBlocked(RuntimeError):
    """官方记录、目标边界或下载完整性不满足冻结清单。"""


def is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(flag and attributes & flag)


def require_https_allowed(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise AcquisitionBlocked(f"拒绝非白名单下载端点：{url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionBlocked(f"下载端点含不允许的用户信息或片段：{url}")


def require_allowed_response_endpoint(response: Any) -> None:
    final_url = str(response.geturl())
    require_https_allowed(final_url)


class StrictRedirectHandler(HTTPRedirectHandler):
    """只允许 HTTPS 且每一跳均在冻结主机白名单内。"""

    max_redirections = 4
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        source = str(req.full_url)
        require_https_allowed(source)
        absolute = urljoin(source, newurl)
        require_https_allowed(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def open_request(request: Request, *, timeout: int):
    require_https_allowed(str(request.full_url))
    response = build_opener(StrictRedirectHandler()).open(request, timeout=timeout)
    try:
        require_allowed_response_endpoint(response)
    except Exception:
        response.close()
        raise
    return response


def require_safe_component(name: str) -> None:
    if not name or name in {".", ".."}:
        raise AcquisitionBlocked(f"非法文件名：{name!r}")
    if Path(name).name != name or any(separator in name for separator in ("/", "\\")):
        raise AcquisitionBlocked(f"文件名越过来源目录：{name!r}")
    if name.endswith((".", " ")) or ":" in name:
        raise AcquisitionBlocked(f"文件名不适合 Windows 安全落盘：{name!r}")


def ensure_source_directory(spec: SourceSpec) -> Path:
    root = DATA_ROOT.resolve(strict=True)
    if is_reparse_point(root):
        raise AcquisitionBlocked(f"新增开放数据根是重解析点：{root}")
    require_safe_component(spec.directory)
    target = root / spec.directory
    if target.exists():
        if not target.is_dir() or is_reparse_point(target):
            raise AcquisitionBlocked(f"来源目录不是普通目录：{target}")
    else:
        target.mkdir()
    if target.resolve(strict=True).parent != root:
        raise AcquisitionBlocked(f"来源目录逃逸：{target}")
    return target


def digest(path: Path, algorithm: str) -> str:
    if algorithm == "md5":
        value = hashlib.md5(usedforsecurity=False)
    else:
        value = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    if path.parent.resolve(strict=True) != path.parent or is_reparse_point(path.parent):
        raise AcquisitionBlocked(f"输出目录不是安全普通目录：{path.parent}")
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
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def request_bytes(url: str, *, attempts: int = 3) -> bytes:
    require_https_allowed(url)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with open_request(request, timeout=60) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise AcquisitionBlocked(f"官方 API 请求失败：{url}: {last_error}")


def fetch_record(spec: SourceSpec) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    raw = json.loads(request_bytes(spec.api_url).decode("utf-8"))
    if spec.provider == "zenodo":
        if int(raw.get("id", -1)) != spec.record_id:
            raise AcquisitionBlocked(f"Zenodo 记录 ID 漂移：{spec.directory}")
        metadata = raw.get("metadata") or {}
        if str(metadata.get("doi", "")).casefold() != spec.doi.casefold():
            raise AcquisitionBlocked(f"Zenodo DOI 漂移：{spec.directory}")
        license_id = str((metadata.get("license") or {}).get("id", ""))
        if license_id.casefold() != spec.license_id.casefold():
            raise AcquisitionBlocked(f"Zenodo 许可证漂移：{spec.directory}")
        files: dict[str, dict[str, Any]] = {}
        for item in raw.get("files") or []:
            name = str(item.get("key", ""))
            checksum = str(item.get("checksum", ""))
            if not checksum.startswith("md5:"):
                raise AcquisitionBlocked(f"Zenodo 文件缺 MD5：{spec.directory}/{name}")
            files[name] = {
                "name": name,
                "size": int(item.get("size", -1)),
                "md5": checksum.removeprefix("md5:").lower(),
                "url": str((item.get("links") or {}).get("self", "")),
            }
        normalized = {
            "provider": "Zenodo",
            "record_id": spec.record_id,
            "doi": spec.doi,
            "title": metadata.get("title"),
            "version": metadata.get("version"),
            "publication_date": metadata.get("publication_date"),
            "license": license_id,
            "creators": metadata.get("creators") or [],
            "related_identifiers": metadata.get("related_identifiers") or [],
        }
    elif spec.provider == "figshare":
        if int(raw.get("id", -1)) != spec.record_id:
            raise AcquisitionBlocked(f"Figshare 记录 ID 漂移：{spec.directory}")
        if str(raw.get("doi", "")).casefold() != spec.doi.casefold():
            raise AcquisitionBlocked(f"Figshare DOI 漂移：{spec.directory}")
        license_name = str((raw.get("license") or {}).get("name", ""))
        if license_name.casefold() != spec.license_id.casefold():
            raise AcquisitionBlocked(f"Figshare 许可证漂移：{spec.directory}")
        files = {}
        for item in raw.get("files") or []:
            name = str(item.get("name", ""))
            files[name] = {
                "name": name,
                "size": int(item.get("size", -1)),
                "md5": str(item.get("computed_md5", "")).lower(),
                "url": str(item.get("download_url", "")),
            }
        normalized = {
            "provider": "Figshare",
            "record_id": spec.record_id,
            "doi": spec.doi,
            "title": raw.get("title"),
            "version": raw.get("version"),
            "published_date": raw.get("published_date"),
            "license": license_name,
            "authors": raw.get("authors") or [],
            "defined_type_name": raw.get("defined_type_name"),
            "url_private_api": None,
            "url_public_api": raw.get("url_public_api"),
            "url_public_html": raw.get("url_public_html"),
        }
    else:
        raise AcquisitionBlocked(f"未支持的仓库类型：{spec.provider}")

    if len(files) != len(set(files)):
        raise AcquisitionBlocked(f"官方 API 出现重复文件名：{spec.directory}")
    for name, item in files.items():
        require_safe_component(name)
        require_https_allowed(str(item["url"]))
    return normalized, files


def validate_record_files(
    spec: SourceSpec, official_files: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = {item.name: item for item in spec.required}
    official_names = set(official_files)
    frozen_names = set(expected) | set(spec.excluded_names)
    if official_names != frozen_names:
        missing = sorted(frozen_names - official_names)
        extra = sorted(official_names - frozen_names)
        raise AcquisitionBlocked(
            f"官方文件集合漂移：{spec.directory}; missing={missing}; extra={extra}"
        )
    rows: list[dict[str, Any]] = []
    for name in sorted(official_names, key=str.casefold):
        official = official_files[name]
        decision = "download" if name in expected else "excluded_large_images"
        if name in expected:
            frozen = expected[name]
            if int(official["size"]) != frozen.size or str(official["md5"]).lower() != frozen.md5:
                raise AcquisitionBlocked(
                    f"官方大小或 MD5 漂移：{spec.directory}/{name}"
                )
        rows.append(
            {
                "source_directory": spec.directory,
                "provider": spec.provider,
                "record_id": spec.record_id,
                "doi": spec.doi,
                "filename": name,
                "bytes": int(official["size"]),
                "md5": str(official["md5"]).lower(),
                "download_url": str(official["url"]),
                "decision": decision,
            }
        )
    return rows


def download_file(url: str, target: Path, expected_size: int, expected_md5: str) -> str:
    require_https_allowed(url)
    if target.exists():
        if not target.is_file() or is_reparse_point(target):
            raise AcquisitionBlocked(f"目标不是普通文件：{target}")
        if target.stat().st_size != expected_size or digest(target, "md5") != expected_md5:
            raise AcquisitionBlocked(f"既有目标完整性失败，拒绝覆盖：{target}")
        return "reused_verified"

    part_suffix = ".part"
    partial = target.with_name(target.name + part_suffix)
    if partial.exists() and (not partial.is_file() or is_reparse_point(partial)):
        raise AcquisitionBlocked(f"下载临时目标不是普通文件：{partial}")
    current_size = partial.stat().st_size if partial.exists() else 0
    if current_size > expected_size:
        raise AcquisitionBlocked(f"下载临时文件大于官方大小：{partial}")
    if current_size == expected_size:
        actual_md5 = digest(partial, "md5")
        if actual_md5 != expected_md5:
            raise AcquisitionBlocked(
                f"完整 .part 的 MD5 不匹配，拒绝晋升：{target.name}="
                f"{actual_md5}/{expected_md5}"
            )
        os.replace(partial, target)
        return "resumed_verified"

    def open_response(offset: int):
        # Zenodo 的文件内容端点会对显式 application/octet-stream 返回 406；
        # 保留可识别的 User-Agent，让仓库按文件自身媒体类型响应。
        headers = {"User-Agent": USER_AGENT}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, headers=headers)
        return open_request(request, timeout=120)

    def validate_response_headers(response: Any, offset: int) -> None:
        status = int(getattr(response, "status", response.getcode()))
        expected_length = expected_size - offset
        content_length = str(response.headers.get("Content-Length", "")).strip()
        try:
            actual_length = int(content_length)
        except ValueError as exc:
            raise AcquisitionBlocked(
                f"下载响应缺少合法 Content-Length：{target.name}={content_length!r}"
            ) from exc
        if actual_length != expected_length:
            raise AcquisitionBlocked(
                f"下载 Content-Length 与预期不符：{target.name}="
                f"{actual_length}/{expected_length}"
            )
        content_range = str(response.headers.get("Content-Range", "")).strip()
        if offset:
            if status != 206:
                raise AcquisitionBlocked(f"续传必须返回 HTTP 206：{status}: {url}")
            expected_range = f"bytes {offset}-{expected_size - 1}/{expected_size}"
            if content_range != expected_range:
                raise AcquisitionBlocked(
                    f"续传 Content-Range 与冻结大小不符：{target.name}="
                    f"{content_range!r}/{expected_range!r}"
                )
        else:
            if status != 200:
                raise AcquisitionBlocked(f"完整下载必须返回 HTTP 200：{status}: {url}")
            if content_range:
                raise AcquisitionBlocked(
                    f"完整下载意外返回 Content-Range：{target.name}={content_range!r}"
                )

    try:
        response = open_response(current_size)
        status = int(getattr(response, "status", response.getcode()))
        if current_size and status == 200:
            # 服务端忽略 Range 时不读取响应体，改为从零重启。
            response.close()
            current_size = 0
            response = open_response(0)
        try:
            validate_response_headers(response, current_size)
        except Exception:
            response.close()
            raise
    except (HTTPError, URLError, TimeoutError) as exc:
        raise AcquisitionBlocked(f"文件下载请求失败：{url}: {exc}") from exc

    mode = "ab" if current_size else "wb"
    written = current_size
    with response, partial.open(mode) as handle:
        while True:
            block = response.read(4 * 1024 * 1024)
            if not block:
                break
            handle.write(block)
            written += len(block)
            if written > expected_size:
                raise AcquisitionBlocked(f"下载超过官方大小：{target.name}")
        handle.flush()
        os.fsync(handle.fileno())
    if partial.stat().st_size != expected_size:
        raise AcquisitionBlocked(
            f"下载大小不完整：{target.name}={partial.stat().st_size}/{expected_size}"
        )
    actual_md5 = digest(partial, "md5")
    if actual_md5 != expected_md5:
        raise AcquisitionBlocked(
            f"下载 MD5 不匹配：{target.name}={actual_md5}/{expected_md5}"
        )
    os.replace(partial, target)
    return "downloaded_verified"


def render_tsv(rows: list[dict[str, Any]]) -> bytes:
    columns = [
        "source_directory",
        "provider",
        "record_id",
        "doi",
        "filename",
        "bytes",
        "md5",
        "download_url",
        "decision",
        "local_state",
        "local_sha256",
    ]
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def acquire_source(spec: SourceSpec) -> dict[str, Any]:
    target_directory = ensure_source_directory(spec)
    normalized, official_files = fetch_record(spec)
    rows = validate_record_files(spec, official_files)
    verified_sha256: dict[str, str] = {}
    expected = {item.name: item for item in spec.required}
    for name in sorted(expected, key=str.casefold):
        item = expected[name]
        official = official_files[name]
        target = target_directory / name
        state = download_file(str(official["url"]), target, item.size, item.md5)
        verified_sha256[name] = digest(target, "sha256")
        print(f"{spec.directory}: {name}: {state}", flush=True)

    manifest_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item["filename"] in verified_sha256:
            # 清单描述稳定的科学事实，而不是本次运行究竟下载还是复用了文件。
            # 运行事件只打印到控制台，否则第一次和第二次运行会产生不同清单。
            item["local_state"] = "verified_present"
            item["local_sha256"] = verified_sha256[item["filename"]]
        else:
            item["local_state"] = "not_downloaded_by_design"
            item["local_sha256"] = ""
        manifest_rows.append(item)

    normalized["captured_on"] = CAPTURE_DATE
    normalized["api_url"] = spec.api_url
    normalized["download_policy"] = {
        "required_file_count": len(spec.required),
        "excluded_file_count": len(spec.excluded_names),
        "excluded_reason": (
            "26.9 GB原始图像不增加12个试样的身份或数值通道；使用Data_csv最小充分包。"
            if spec.excluded_names
            else None
        ),
    }
    normalized["files"] = [
        {
            key: row[key]
            for key in ("filename", "bytes", "md5", "download_url", "decision")
        }
        for row in manifest_rows
    ]
    atomic_write(
        target_directory / "官方API元数据.json",
        (json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    atomic_write(target_directory / "官方文件清单.tsv", render_tsv(manifest_rows))
    return {
        "source": spec.directory,
        "downloaded_file_count": len(spec.required),
        "downloaded_bytes": sum(item.size for item in spec.required),
        "excluded_file_count": len(spec.excluded_names),
    }


def validate_frozen_manifest() -> None:
    required = [item for source in SOURCES for item in source.required]
    excluded = [name for source in SOURCES for name in source.excluded_names]
    if len(required) != EXPECTED_FILE_COUNT or len({item.name for item in required}) != len(required):
        raise AcquisitionBlocked("固定下载文件总数或名称唯一性不成立")
    if len(excluded) != EXPECTED_EXCLUDED_FILE_COUNT or len(set(excluded)) != len(excluded):
        raise AcquisitionBlocked("固定排除文件总数或名称唯一性不成立")
    if len({source.directory for source in SOURCES}) != len(SOURCES):
        raise AcquisitionBlocked("来源目录名重复")
    for source in SOURCES:
        require_https_allowed(source.api_url)
        for item in source.required:
            require_safe_component(item.name)
            if item.size <= 0 or len(item.md5) != 32:
                raise AcquisitionBlocked(f"固定文件大小或MD5非法：{source.directory}/{item.name}")


def main() -> int:
    validate_frozen_manifest()
    results = [acquire_source(source) for source in SOURCES]
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcquisitionBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
