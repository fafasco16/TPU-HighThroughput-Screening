"""从固定 Zenodo 官方端点下载 TPU 数据库第三批两个实验来源。

覆盖记录 ``5841610``（商业 TPU 多材料打印传感）和 ``6128356``
（Tecoflex EG-60D / Niclosamide 复合材料）。下载器只接受代码内冻结的
记录、文件名、大小和 MD5；官方记录一旦增删文件或修改内容，程序会在写入
科学文件前失败。所有下载先进入同目录 ``.part``，大小与 MD5 同时通过后才
原子替换目标；元数据与文件清单也采用同目录原子替换。

运行：

    python 代码/获取/下载第三批Zenodo实验双源.py
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
DATA_ROOT = PROJECT_ROOT / "数据/原始" / "外部数据" / "新增开放数据"
CAPTURE_DATE = "2026-07-20"
USER_AGENT = "TPU-HighThroughput-Screening/0.2 (+research data acquisition)"
EXPECTED_FILE_COUNT = 25
EXPECTED_TOTAL_BYTES = 22_223_943
ALLOWED_DOWNLOAD_HOSTS = frozenset({"zenodo.org"})


@dataclass(frozen=True)
class ExpectedFile:
    name: str
    size: int
    md5: str


@dataclass(frozen=True)
class SourceSpec:
    directory: str
    record_id: int
    concept_record_id: int
    doi: str
    concept_doi: str
    version: str | None
    api_url: str
    license_id: str
    required: tuple[ExpectedFile, ...]


SOURCES = (
    SourceSpec(
        directory="Zenodo_商业TPU多材料打印传感",
        record_id=5_841_610,
        concept_record_id=5_841_609,
        doi="10.5281/zenodo.5841610",
        concept_doi="10.5281/zenodo.5841609",
        version=None,
        api_url="https://zenodo.org/api/records/5841610",
        license_id="cc-by-4.0",
        required=(
            ExpectedFile("Figure_2a.csv", 1_572_196, "e7ee99b37eed874be6d562b573030115"),
            ExpectedFile("Figure_2b.csv", 81, "893a3121e27cded764b1d907b9836723"),
            ExpectedFile("Figure_3.csv", 6_021, "832f52c17fe113271d7d7f9762d6030b"),
            ExpectedFile("Figure_4.csv", 781_439, "1cdd2f42f591746dd7824ffe1f7f81b2"),
            ExpectedFile("Figure_5.csv", 2_507_642, "240435dbd8b4b6335d1d9ddd3b4a53e6"),
            ExpectedFile("Figure_6.csv", 9_795, "52407f4692f923cf8477cda1fc51ffd1"),
            ExpectedFile("Figure_7.csv", 590_856, "d84aaf3d4d9a484c47ef344dae2c3160"),
            ExpectedFile("Figure_9a.csv", 53_235, "304b04cacabaefb5709427c47e94bc43"),
            ExpectedFile("Figure_9b.csv", 8_622, "5f43b4cd3a804d840555caa84dc289b5"),
            ExpectedFile("Figure_9c.csv", 8_010, "05693637475bb4082698d3152abc534e"),
            ExpectedFile("Figure_S4.csv", 2_471_108, "0770467ed2ea4f9af337b365484bfc60"),
            ExpectedFile("Figure_S5.csv", 7_096_012, "5096f49380e55f9961b28dc52cb627db"),
            ExpectedFile("Figure_S6.csv", 2_600_886, "a285c0467717a026e7484a34de0b06e6"),
            ExpectedFile("Figure_S7.csv", 1_857_048, "2da226318a4f64d2201d85f1b17033bb"),
        ),
    ),
    SourceSpec(
        directory="Zenodo_Tecoflex药物复合TPU",
        record_id=6_128_356,
        concept_record_id=6_128_355,
        doi="10.5281/zenodo.6128356",
        concept_doi="10.5281/zenodo.6128355",
        version="Version 1",
        api_url="https://zenodo.org/api/records/6128356",
        license_id="cc-by-4.0",
        required=(
            ExpectedFile("FTIR.xlsx", 1_205_634, "e909662476ac1c44a98490860396c913"),
            ExpectedFile(
                "In vitro antibacterial evaluation.xlsx",
                35_763,
                "89f2175832acc267295382ffbb36b352",
            ),
            ExpectedFile(
                "In vitro release and disk diffusion.xlsx",
                66_679,
                "001f82c75ee12588bf13c67a452877f3",
            ),
            ExpectedFile(
                "In vivo antibacterial evaluation.xlsx",
                16_460,
                "732b8c94d73550c625d9db076ed4fec6",
            ),
            ExpectedFile(
                "Results in vivo Release.xlsx",
                20_390,
                "fa308a33a1c86b0dd64c5762fb52bdad",
            ),
            ExpectedFile(
                "TGA thermal analysis.xlsx",
                819_167,
                "5bdb6c7a577b47f7be550d52fe6bebe9",
            ),
            ExpectedFile("XRD.zip", 61_005, "ffa0b4f77b7e3858ae31b715b6e39838"),
            ExpectedFile("contact angle.xlsx", 20_132, "5b1906ca732bb318377180a425185950"),
            ExpectedFile(
                "mechanical testing sup fig 1.xlsx",
                41_340,
                "0edc8d2132c41b8780ecf636036ef82b",
            ),
            ExpectedFile(
                "mechanical testing.xlsx",
                350_279,
                "de1952918690cfb46e56d8eea319d265",
            ),
            ExpectedFile("tube diameter.xlsx", 24_143, "c79dc4c7406ab8003aa268b4661e0805"),
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
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(flag and attributes & flag) or bool(is_junction())


def require_https_allowed(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise AcquisitionBlocked(f"拒绝非白名单下载端点：{url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionBlocked(f"下载端点含不允许的用户信息或片段：{url}")


def require_allowed_response_endpoint(response: Any) -> None:
    """自动重定向完成后再次核验最终端点，避免只验证请求前 URL。"""
    final_url = str(response.geturl())
    require_https_allowed(final_url)


class WhitelistRedirectHandler(HTTPRedirectHandler):
    """每一跳都限定在同一Zenodo HTTPS主机。"""

    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        source = req.full_url
        absolute = urljoin(source, newurl)
        require_https_allowed(source)
        require_https_allowed(absolute)
        if urlsplit(source).hostname != urlsplit(absolute).hostname:
            raise AcquisitionBlocked(f"重定向跨越Zenodo固定主机：{source} -> {absolute}")
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def open_request(request: Request, *, timeout: int):
    require_https_allowed(request.full_url)
    response = build_opener(WhitelistRedirectHandler()).open(request, timeout=timeout)
    require_allowed_response_endpoint(response)
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
    parent = path.parent
    if (
        not parent.is_dir()
        or is_reparse_point(parent)
        or parent.resolve(strict=True) != parent.absolute()
    ):
        raise AcquisitionBlocked(f"输出目录不是安全普通目录：{parent}")
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
        if is_reparse_point(temporary) or not temporary.is_file():
            raise AcquisitionBlocked(f"原子写临时文件异常：{temporary}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def request_bytes_capture(
    url: str, *, attempts: int = 3
) -> tuple[bytes, dict[str, object]]:
    require_https_allowed(url)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with open_request(request, timeout=60) as response:
                require_allowed_response_endpoint(response)
                status = int(getattr(response, "status", response.getcode()))
                if status != 200:
                    raise AcquisitionBlocked(f"官方 API HTTP 状态异常：{status}: {url}")
                payload = response.read()
                return payload, {
                    "request_url": url,
                    "final_url": response.geturl(),
                    "status": status,
                    "content_type": response.headers.get("Content-Type"),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "payload_bytes": len(payload),
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                    "payload_base64": base64.b64encode(payload).decode("ascii"),
                }
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise AcquisitionBlocked(f"官方 API 请求失败：{url}: {last_error}")


def fetch_record(spec: SourceSpec) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    record_payload, record_capture = request_bytes_capture(spec.api_url)
    raw = json.loads(record_payload.decode("utf-8"))
    if int(raw.get("id", -1)) != spec.record_id:
        raise AcquisitionBlocked(f"Zenodo 记录 ID 漂移：{spec.directory}")
    if int(raw.get("conceptrecid", -1)) != spec.concept_record_id:
        raise AcquisitionBlocked(f"Zenodo 概念记录 ID 漂移：{spec.directory}")
    if str(raw.get("conceptdoi", "")).casefold() != spec.concept_doi.casefold():
        raise AcquisitionBlocked(f"Zenodo 概念 DOI 漂移：{spec.directory}")
    metadata = raw.get("metadata") or {}
    if str(metadata.get("doi", "")).casefold() != spec.doi.casefold():
        raise AcquisitionBlocked(f"Zenodo DOI 漂移：{spec.directory}")
    license_id = str((metadata.get("license") or {}).get("id", ""))
    if license_id.casefold() != spec.license_id.casefold():
        raise AcquisitionBlocked(f"Zenodo 许可证漂移：{spec.directory}")
    if metadata.get("version") != spec.version:
        raise AcquisitionBlocked(f"Zenodo 版本漂移：{spec.directory}")
    latest_url = str((raw.get("links") or {}).get("latest", ""))
    require_https_allowed(latest_url)
    latest_payload, latest_capture = request_bytes_capture(latest_url)
    latest = json.loads(latest_payload.decode("utf-8"))
    if int(latest.get("id", -1)) != spec.record_id:
        raise AcquisitionBlocked(f"Zenodo 固定记录已不是最新版：{spec.directory}")

    files: dict[str, dict[str, Any]] = {}
    for item in raw.get("files") or []:
        name = str(item.get("key", ""))
        require_safe_component(name)
        if name in files:
            raise AcquisitionBlocked(f"Zenodo 官方 API 出现重复文件名：{name}")
        checksum = str(item.get("checksum", ""))
        if not checksum.startswith("md5:"):
            raise AcquisitionBlocked(f"Zenodo 文件缺 MD5：{spec.directory}/{name}")
        url = str((item.get("links") or {}).get("self", ""))
        require_https_allowed(url)
        files[name] = {
            "name": name,
            "size": int(item.get("size", -1)),
            "md5": checksum.removeprefix("md5:").lower(),
            "url": url,
        }

    normalized = {
        "provider": "Zenodo",
        "record_id": spec.record_id,
        "concept_record_id": spec.concept_record_id,
        "doi": spec.doi,
        "concept_doi": spec.concept_doi,
        "latest_record_verified": True,
        "raw_api_capture_format": "exact_response_bytes_base64_with_sha256",
        "raw_api_captures": [record_capture, latest_capture],
        "title": metadata.get("title"),
        "version": metadata.get("version"),
        "publication_date": metadata.get("publication_date"),
        "license": license_id,
        "creators": metadata.get("creators") or [],
        "related_identifiers": metadata.get("related_identifiers") or [],
    }
    return normalized, files


def validate_record_files(
    spec: SourceSpec, official_files: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = {item.name: item for item in spec.required}
    if set(official_files) != set(expected):
        missing = sorted(set(expected) - set(official_files))
        extra = sorted(set(official_files) - set(expected))
        raise AcquisitionBlocked(
            f"官方文件集合漂移：{spec.directory}; missing={missing}; extra={extra}"
        )
    rows: list[dict[str, Any]] = []
    for name in sorted(expected, key=str.casefold):
        frozen = expected[name]
        official = official_files[name]
        if int(official["size"]) != frozen.size or str(official["md5"]) != frozen.md5:
            raise AcquisitionBlocked(f"官方大小或 MD5 漂移：{spec.directory}/{name}")
        rows.append(
            {
                "source_directory": spec.directory,
                "provider": "zenodo",
                "record_id": spec.record_id,
                "doi": spec.doi,
                "filename": name,
                "bytes": frozen.size,
                "md5": frozen.md5,
                "download_url": str(official["url"]),
                "decision": "download",
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

    partial = target.with_name(target.name + ".part")
    if partial.exists() and (not partial.is_file() or is_reparse_point(partial)):
        raise AcquisitionBlocked(f"下载临时目标不是普通文件：{partial}")
    current_size = partial.stat().st_size if partial.exists() else 0
    if current_size > expected_size:
        partial.unlink()
        current_size = 0
    if current_size == expected_size:
        actual_md5 = digest(partial, "md5")
        if actual_md5 != expected_md5:
            partial.unlink()
            current_size = 0
        else:
            os.replace(partial, target)
            return "resumed_complete_verified"

    def open_response(offset: int):
        headers = {"User-Agent": USER_AGENT}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        return open_request(Request(url, headers=headers), timeout=120)

    def validate_download_response(response: Any, offset: int) -> int:
        require_allowed_response_endpoint(response)
        status = int(getattr(response, "status", response.getcode()))
        if offset:
            if status == 200:
                # 服务端忽略 Range 时由调用方丢弃已有 part 并从零重下。
                return status
            if status != 206:
                raise AcquisitionBlocked(
                    f"续传 HTTP 状态异常：{status}: {target.name}"
                )
            content_range = str(response.headers.get("Content-Range", ""))
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
            if match is None:
                raise AcquisitionBlocked(
                    f"续传缺少合法 Content-Range：{target.name}={content_range!r}"
                )
            start, end, total = (int(value) for value in match.groups())
            if (
                start != offset
                or end != expected_size - 1
                or total != expected_size
                or end < start
            ):
                raise AcquisitionBlocked(
                    f"续传 Content-Range 与冻结大小不符：{target.name}="
                    f"{content_range!r}; offset={offset}; expected={expected_size}"
                )
            content_length = str(response.headers.get("Content-Length", ""))
            if not content_length.isdecimal() or int(content_length) != end - start + 1:
                raise AcquisitionBlocked(
                    f"续传 Content-Length 不符：{target.name}={content_length}"
                )
            return status
        if status != 200:
            raise AcquisitionBlocked(
                f"完整下载必须返回 200 而非 {status}：{target.name}"
            )
        if response.headers.get("Content-Range") is not None:
            raise AcquisitionBlocked(f"完整下载意外返回 Content-Range：{target.name}")
        content_length = str(response.headers.get("Content-Length", ""))
        if not content_length.isdecimal() or int(content_length) != expected_size:
            raise AcquisitionBlocked(
                f"完整下载 Content-Length 不符：{target.name}={content_length}/{expected_size}"
            )
        return status

    try:
        response = open_response(current_size)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise AcquisitionBlocked(f"文件下载请求失败：{url}: {exc}") from exc
    try:
        status = validate_download_response(response, current_size)
    except Exception:
        response.close()
        raise
    if current_size and status != 206:
        response.close()
        current_size = 0
        try:
            response = open_response(0)
            status = validate_download_response(response, 0)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AcquisitionBlocked(f"Range 回退下载请求失败：{url}: {exc}") from exc
        except Exception:
            response.close()
            raise

    mode = "ab" if current_size else "wb"
    written = current_size
    try:
        with response, partial.open(mode) as handle:
            remaining = expected_size - current_size
            while True:
                block = response.read(min(4 * 1024 * 1024, remaining + 1))
                if not block:
                    break
                if len(block) > remaining:
                    raise AcquisitionBlocked(f"下载超过官方大小：{target.name}")
                handle.write(block)
                written += len(block)
                remaining -= len(block)
            handle.flush()
            os.fsync(handle.fileno())
    except AcquisitionBlocked:
        partial.unlink(missing_ok=True)
        raise
    if partial.stat().st_size != expected_size:
        raise AcquisitionBlocked(
            f"下载大小不完整：{target.name}={partial.stat().st_size}/{expected_size}"
        )
    actual_md5 = digest(partial, "md5")
    if actual_md5 != expected_md5:
        partial.unlink(missing_ok=True)
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
        state = download_file(
            str(official_files[name]["url"]), target_directory / name, item.size, item.md5
        )
        verified_sha256[name] = digest(target_directory / name, "sha256")
        print(f"{spec.directory}: {name}: {state}", flush=True)

    manifest_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["local_state"] = "verified_present"
        item["local_sha256"] = verified_sha256[str(item["filename"])]
        manifest_rows.append(item)

    normalized["captured_on"] = CAPTURE_DATE
    normalized["api_url"] = spec.api_url
    normalized["download_policy"] = {
        "required_file_count": len(spec.required),
        "excluded_file_count": 0,
        "all_record_files_downloaded": True,
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
    }


def validate_frozen_manifest() -> None:
    files = [item for source in SOURCES for item in source.required]
    if len(files) != EXPECTED_FILE_COUNT:
        raise AcquisitionBlocked("固定下载文件总数不成立")
    if sum(item.size for item in files) != EXPECTED_TOTAL_BYTES:
        raise AcquisitionBlocked("固定下载字节总数不成立")
    if len({(source.directory, item.name) for source in SOURCES for item in source.required}) != len(files):
        raise AcquisitionBlocked("固定来源内文件名不唯一")
    if len({source.directory for source in SOURCES}) != len(SOURCES):
        raise AcquisitionBlocked("来源目录名重复")
    for source in SOURCES:
        require_https_allowed(source.api_url)
        for item in source.required:
            require_safe_component(item.name)
            if item.size <= 0 or len(item.md5) != 32:
                raise AcquisitionBlocked(f"固定文件大小或 MD5 非法：{source.directory}/{item.name}")


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
