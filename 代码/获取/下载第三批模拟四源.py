"""下载并固定 TPU 数据库第三批四个开放模拟来源。

本脚本只访问代码内白名单化的 Zenodo 记录和两个固定 Git commit raw 文件。
Zenodo 记录、最新版身份、文件集合、官方大小和 MD5 任一漂移都会失败关闭；
下载先写 ``.part``，完整性通过后原子替换。每个本地文件另计算 SHA256，
但不创建训练集、不调整权重，也不把轨迹帧、原子或量化化学驻点当作材料。

运行：

    python 代码/获取/下载第三批模拟四源.py
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "数据/原始" / "外部数据" / "新增开放数据"
CAPTURE_DATE = "2026-07-20"
USER_AGENT = "TPU-HighThroughput-Screening/0.3 (+research data acquisition)"
ALLOWED_HOSTS = frozenset({"zenodo.org", "raw.githubusercontent.com", "api.github.com"})
METADATA_NAME = "官方API元数据.json"
MANIFEST_NAME = "官方文件清单.tsv"
MAX_DOWNLOAD_REQUESTS = 12
MAX_DOWNLOAD_SECONDS = 1_800


@dataclass(frozen=True)
class ExpectedFile:
    official_key: str
    size: int
    md5: str
    local_name: str | None = None
    sha256: str | None = None

    @property
    def target_name(self) -> str:
        return self.local_name or self.official_key


@dataclass(frozen=True)
class SourceSpec:
    directory: str
    record_id: int
    concept_record_id: int
    doi: str
    concept_doi: str
    version: str | None
    license_id: str
    files: tuple[ExpectedFile, ...]


@dataclass(frozen=True)
class AuxiliaryFile:
    local_name: str
    url: str
    size: int
    sha256: str


POLYUREA = "Zenodo_反应型粗粒化聚脲固化"
NIPU = "Zenodo_NIPU反应路径DFT与MD"
PCL = "Zenodo_PCL软段构象粗粒化MD"
IMPACT = "Zenodo_PTMO_MDI_BDO聚氨酯冲击MD"

SOURCES = (
    SourceSpec(
        directory=POLYUREA,
        record_id=7_811_383,
        concept_record_id=7_811_382,
        doi="10.5281/zenodo.7811383",
        concept_doi="10.5281/zenodo.7811382",
        version="v1.0",
        license_id="other-open",
        files=(
            ExpectedFile(
                "liuminghao0830/cg-polyurea-curing-v1.0.zip",
                7_173_934,
                "35009e635573dff57a75d910afbf2302",
                "cg-polyurea-curing-v1.0.zip",
                "dc7771885f03c2729ded304d30bfcd42bf523ac656a92088a7627a26d856b2ea",
            ),
        ),
    ),
    SourceSpec(
        directory=NIPU,
        record_id=10_817_092,
        concept_record_id=10_817_091,
        doi="10.5281/zenodo.10817092",
        concept_doi="10.5281/zenodo.10817091",
        version="version 1",
        license_id="cc-by-4.0",
        files=(
            ExpectedFile("ReadMeFile.txt", 7_982, "37e4c895cb483cf7ed6267d3a407fe27"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0001_v1.lmp", 3_592, "2e881a2d6771d74a1a8a32dd9beb6324"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0002_v1.lmp", 3_880, "8d4a8da5fe94c618dd4277fb96aed492"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0003_v1.lmp", 3_918, "7abb7c11c18042182867d0b802b4e947"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0004_v1.lmp", 4_134, "33e00670d1159f6e87664d719691372e"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0005_v1.lmp", 3_761, "c96f7ca044ce4677fe3360b14476fb16"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0006_v1.lmp", 23_165, "e8f42303749c25c78a3d042b61c8fc0b"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0007_v1.lmp", 24_681, "fe8ec93623850762fe41e8623838b291"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0008_v1.lmp", 32_875, "061ee51205e93bc99d1a160bc76202b8"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0009_v1.lmp", 33_933, "04e9309eabb5843d274bdbd64f9b1f31"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0010_v1.lmp", 33_680, "79670787f5210959a43afe98a7824b09"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0011_v1.log", 1_459_677, "2b971fff759102311456006eb0534edf"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0012_v1.log", 1_734_740, "b7e7ce81687e32a66e2a79d2269a42fb"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0013_v1.log", 508_097, "6cb1cf913b636e147832e69bacc75fc2"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0014_v1.log", 1_425_747, "9e7a6cc2f578d359b2c2e845443ae5ec"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0015_v1.log", 2_126_237, "9769f6a2527cc50f063c047359808e97"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0016_v1.log", 3_971_635, "17b701d62656d4d822d53042353de9f5"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0017_v1.log", 490_783, "e1a949ade91046d2b60572bda1afe3cf"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0018_v1.log", 2_453_368, "bc1581ef455ce03c5443fbf1c69fae0f"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0019_v1.log", 9_042_455, "197b86628789b6fc066782f5e6da50b3"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0020_v1.log", 3_829_548, "61c3eef5b10a7c19f977ab404be8a964"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0021_v1.log", 1_941_192, "aeb32fc75a81aa004fe2e23db09036c7"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0022_v1.log", 21_696_793, "e83bf64900009ee211418aae115c8aea"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0023_v1.log", 5_854_923, "63b6f92eb1c6680dd95670a26259b834"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0024_v1.log", 24_972_151, "e67c8536235c5dadd6c807e408359e1e"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0025_v1.log", 6_859_922, "551f51cc4e9f4395db0c01d8e85fb33a"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0026_v1.log", 10_495_783, "17159c25cfb337de0906ef38505e10c1"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0027_v1.log", 17_393_206, "4daf38953fbb59d6fdd77c13a557b6e6"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0028_v1.log", 6_068_577, "fad130df9f47688eac71f100cb1358df"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0029_v1.log", 24_641_381, "72c49676953ddb58d446d42a225e3fc9"),
            ExpectedFile("VZ4_001_021_VSCHT_D_0030_v1.log", 8_169_660, "d08dbec57ab475731336e0580af1b0d0"),
        ),
    ),
    SourceSpec(
        directory=PCL,
        record_id=17_790_918,
        concept_record_id=16_944_033,
        doi="10.5281/zenodo.17790918",
        concept_doi="10.5281/zenodo.16944033",
        version="v1.0_2",
        license_id="cc-by-4.0",
        files=(
            ExpectedFile(
                "pbacova/PCL_Supplementary_material_systematic_CG-v1.0_2.zip",
                161_897_959,
                "b588fdf8a1afb1e76aaadeb2a53c1310",
                "PCL_Supplementary_material_systematic_CG-v1.0_2.zip",
                "5a59701e7a09f1f8b7907a0c9de70c86ffca05b4825812479b4ad4ad0a127002",
            ),
        ),
    ),
    SourceSpec(
        directory=IMPACT,
        record_id=5_099_589,
        concept_record_id=5_099_588,
        doi="10.5281/zenodo.5099589",
        concept_doi="10.5281/zenodo.5099588",
        version=None,
        license_id="cc-by-4.0",
        files=(
            ExpectedFile(
                "polyurethane_60nm.data",
                246_265_627,
                "06a2a113ee25d108fd06057c5667c45e",
                sha256="c518b4f5797e21cf9e79e77ead592ffc4ca12c76814c72085296bdb15ba3d376",
            ),
        ),
    ),
)

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
GIT_AUXILIARY = (
    AuxiliaryFile(
        "spall_in.in",
        "https://raw.githubusercontent.com/nuwan-d/MD_model_JAM-21-1174/"
        f"{PINNED_GIT_COMMIT}/spall_in.in",
        1_275,
        "5701606cd81ed341f1e62caf710bbb1cdcc3886643b24a202cd93187267629a3",
    ),
    AuxiliaryFile(
        "polyurethane_60nm.params",
        "https://raw.githubusercontent.com/nuwan-d/MD_model_JAM-21-1174/"
        f"{PINNED_GIT_COMMIT}/polyurethane_60nm.params",
        31_334,
        "776dc1f0686b0b8b5b2f03023b104b93327cf6c50fc22437335ee53c14e867db",
    ),
)


class AcquisitionBlocked(RuntimeError):
    """来源身份、路径边界或文件完整性不满足冻结协议。"""


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
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise AcquisitionBlocked(f"拒绝非白名单 HTTPS 端点：{url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionBlocked(f"端点含用户信息或片段：{url}")


class WhitelistRedirectHandler(HTTPRedirectHandler):
    """逐跳限制 HTTPS 重定向，禁止借官方入口访问第三方主机。"""

    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        source = req.full_url
        absolute = urljoin(source, newurl)
        require_https_allowed(source)
        require_https_allowed(absolute)
        if urlsplit(source).hostname != urlsplit(absolute).hostname:
            raise AcquisitionBlocked(f"重定向跨越固定主机：{source} -> {absolute}")
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def open_request(request: Request, *, timeout: int):
    require_https_allowed(request.full_url)
    opener = build_opener(WhitelistRedirectHandler())
    response = opener.open(request, timeout=timeout)
    require_https_allowed(response.geturl())
    return response


def require_safe_local_name(name: str) -> None:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise AcquisitionBlocked(f"非法本地文件名：{name!r}")
    if any(separator in name for separator in ("/", "\\")):
        raise AcquisitionBlocked(f"本地文件名越过来源目录：{name!r}")
    if name.endswith((".", " ")) or ":" in name or "\x00" in name:
        raise AcquisitionBlocked(f"本地文件名不适合安全落盘：{name!r}")


def ensure_source_directory(spec: SourceSpec) -> Path:
    root = DATA_ROOT.resolve(strict=True)
    if is_reparse_point(root):
        raise AcquisitionBlocked(f"新增开放数据根是重解析点：{root}")
    require_safe_local_name(spec.directory)
    target = root / spec.directory
    if target.exists():
        if not target.is_dir() or is_reparse_point(target):
            raise AcquisitionBlocked(f"来源目录不是普通目录：{target}")
    else:
        target.mkdir()
    if target.resolve(strict=True).parent != root:
        raise AcquisitionBlocked(f"来源目录逃逸：{target}")
    return target


def file_digest(path: Path, algorithm: str) -> str:
    if algorithm == "md5":
        digest = hashlib.md5(usedforsecurity=False)
    else:
        digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    parent = path.parent
    if not parent.is_dir() or is_reparse_point(parent) or parent.resolve(strict=True) != parent:
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
            raise AcquisitionBlocked(f"临时输出不是普通文件：{temporary}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def request_bytes_capture(
    url: str, *, attempts: int = 4, maximum: int = 16_000_000
) -> tuple[bytes, dict[str, object]]:
    """返回官方响应原始字节及带SHA256的精确API快照。"""
    require_https_allowed(url)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with open_request(request, timeout=90) as response:
                require_https_allowed(response.geturl())
                payload = response.read(maximum + 1)
                if len(payload) > maximum:
                    raise AcquisitionBlocked(f"API 响应超过上限：{url}")
                return payload, {
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
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise AcquisitionBlocked(f"官方 API 请求失败：{url}: {last_error}")


def request_github_bytes_capture(url: str) -> tuple[bytes, dict[str, object]]:
    """通过已认证 GitHub CLI 获取固定提交证据，避免匿名API限额。"""
    require_https_allowed(url)
    parsed = urlsplit(url)
    if parsed.hostname != "api.github.com":
        raise AcquisitionBlocked(f"GitHub CLI端点主机错误：{url}")
    executable = shutil.which("gh")
    if executable is None:
        raise AcquisitionBlocked("缺少GitHub CLI，无法固定Git提交证据")
    endpoint = parsed.path.lstrip("/") + (f"?{parsed.query}" if parsed.query else "")
    try:
        result = subprocess.run(
            [executable, "api", endpoint],
            check=False,
            capture_output=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcquisitionBlocked(f"GitHub CLI请求失败：{url}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise AcquisitionBlocked(f"GitHub CLI请求失败：{url}: {message}")
    payload = bytes(result.stdout)
    try:
        json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcquisitionBlocked(f"GitHub CLI未返回有效JSON：{url}") from exc
    return payload, {
        "request_url": url,
        "final_url": url,
        "status": 200,
        "transport": "authenticated_github_cli",
        "content_type": "application/json",
        "etag": None,
        "last_modified": None,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_base64": base64.b64encode(payload).decode("ascii"),
    }


def _latest_record_id(
    spec: SourceSpec, raw: dict[str, Any]
) -> tuple[int, dict[str, object]]:
    latest_url = str((raw.get("links") or {}).get("latest", ""))
    require_https_allowed(latest_url)
    payload, capture = request_bytes_capture(latest_url)
    latest = json.loads(payload.decode("utf-8"))
    return int(latest.get("id", -1)), capture


def fetch_record(spec: SourceSpec) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    api_url = f"https://zenodo.org/api/records/{spec.record_id}"
    record_payload, record_capture = request_bytes_capture(api_url)
    raw = json.loads(record_payload.decode("utf-8"))
    metadata = raw.get("metadata") or {}
    if int(raw.get("id", -1)) != spec.record_id:
        raise AcquisitionBlocked(f"Zenodo 记录 ID 漂移：{spec.directory}")
    if int(raw.get("conceptrecid", -1)) != spec.concept_record_id:
        raise AcquisitionBlocked(f"Zenodo 概念记录 ID 漂移：{spec.directory}")
    if str(metadata.get("doi", "")).casefold() != spec.doi.casefold():
        raise AcquisitionBlocked(f"Zenodo DOI 漂移：{spec.directory}")
    if str(raw.get("conceptdoi", "")).casefold() != spec.concept_doi.casefold():
        raise AcquisitionBlocked(f"Zenodo 概念 DOI 漂移：{spec.directory}")
    if metadata.get("version") != spec.version:
        raise AcquisitionBlocked(f"Zenodo 版本漂移：{spec.directory}")
    license_id = str((metadata.get("license") or {}).get("id", ""))
    if license_id.casefold() != spec.license_id.casefold():
        raise AcquisitionBlocked(f"Zenodo 许可证漂移：{spec.directory}")
    latest_record_id, latest_capture = _latest_record_id(spec, raw)
    if latest_record_id != spec.record_id:
        raise AcquisitionBlocked(f"固定记录已不是最新版：{spec.directory}")

    files: dict[str, dict[str, Any]] = {}
    for item in raw.get("files") or []:
        key = str(item.get("key", ""))
        checksum = str(item.get("checksum", ""))
        if not key or key in files or not checksum.startswith("md5:"):
            raise AcquisitionBlocked(f"Zenodo 文件名重复或缺 MD5：{spec.directory}/{key}")
        url = str((item.get("links") or {}).get("self", ""))
        require_https_allowed(url)
        files[key] = {
            "size": int(item.get("size", -1)),
            "md5": checksum.removeprefix("md5:").lower(),
            "url": url,
        }

    expected = {item.official_key: item for item in spec.files}
    if set(files) != set(expected):
        raise AcquisitionBlocked(
            f"官方文件集合漂移：{spec.directory}; "
            f"缺失={sorted(set(expected)-set(files))}; 多余={sorted(set(files)-set(expected))}"
        )
    for key, frozen in expected.items():
        actual = files[key]
        if actual["size"] != frozen.size or actual["md5"] != frozen.md5:
            raise AcquisitionBlocked(f"官方大小或 MD5 漂移：{spec.directory}/{key}")

    creators = [
        {field: creator.get(field) for field in ("name", "orcid", "affiliation") if creator.get(field)}
        for creator in metadata.get("creators") or []
    ]
    normalized = {
        "provider": "Zenodo",
        "raw_api_capture_format": "exact_response_bytes_base64_with_sha256",
        "raw_api_captures": [record_capture, latest_capture],
        "record_id": spec.record_id,
        "concept_record_id": spec.concept_record_id,
        "doi": spec.doi,
        "concept_doi": spec.concept_doi,
        "title": metadata.get("title"),
        "version": spec.version,
        "publication_date": metadata.get("publication_date"),
        "license": spec.license_id,
        "latest_record_verified": True,
        "api_url": api_url,
        "html_url": str((raw.get("links") or {}).get("self_html", "")),
        "creators": creators,
        "captured_on": CAPTURE_DATE,
        "training_allowed": False,
    }
    return normalized, files


def fetch_git_provenance() -> tuple[dict[str, object], list[dict[str, object]]]:
    commit_url = f"https://api.github.com/repos/{GIT_REPOSITORY}/commits/{PINNED_GIT_COMMIT}"
    tree_url = f"https://api.github.com/repos/{GIT_REPOSITORY}/git/trees/{PINNED_GIT_TREE}?recursive=1"
    commit_payload, commit_capture = request_github_bytes_capture(commit_url)
    tree_payload, tree_capture = request_github_bytes_capture(tree_url)
    commit = json.loads(commit_payload.decode("utf-8"))
    tree = json.loads(tree_payload.decode("utf-8"))
    if (
        str(commit.get("sha", "")) != PINNED_GIT_COMMIT
        or str(((commit.get("commit") or {}).get("tree") or {}).get("sha", ""))
        != PINNED_GIT_TREE
    ):
        raise AcquisitionBlocked("GitHub固定提交或tree身份漂移")
    if str(tree.get("sha", "")) != PINNED_GIT_TREE or bool(tree.get("truncated")):
        raise AcquisitionBlocked("GitHub固定tree被截断或身份漂移")
    observed: dict[str, tuple[str, str, str, int]] = {}
    for item in tree.get("tree") or []:
        if not isinstance(item, dict):
            raise AcquisitionBlocked("GitHub tree条目结构异常")
        path = str(item.get("path", ""))
        if not path or path in observed:
            raise AcquisitionBlocked("GitHub tree路径缺失或重复")
        observed[path] = (
            str(item.get("mode", "")),
            str(item.get("type", "")),
            str(item.get("sha", "")),
            int(item.get("size", -1)),
        )
    if observed != EXPECTED_GIT_TREE:
        raise AcquisitionBlocked("GitHub固定tree清单漂移")
    license_candidates = [
        path
        for path in observed
        if PurePosixPath(path).name.casefold().startswith(("license", "licence", "copying"))
    ]
    if license_candidates:
        raise AcquisitionBlocked(f"GitHub固定tree许可证文件状态漂移：{license_candidates}")
    return (
        {
            "repository": GIT_REPOSITORY,
            "commit": PINNED_GIT_COMMIT,
            "tree": PINNED_GIT_TREE,
            "commit_api_url": commit_url,
            "tree_api_url": tree_url,
            "tree_entries": [
                {
                    "path": path,
                    "mode": values[0],
                    "type": values[1],
                    "git_sha": values[2],
                    "bytes": values[3],
                }
                for path, values in sorted(observed.items())
            ],
            "license_candidates": license_candidates,
            "license_status": "no LICENSE/COPYING file detected in pinned tree",
            "redistribution_assumption": "do not assume redistribution permission",
            "allowed_use_here": "research reproducibility input only",
        },
        [commit_capture, tree_capture],
    )


def validate_download_response_headers(
    *,
    status: int,
    headers: Any,
    offset: int,
    expected_size: int,
    url: str,
) -> str | None:
    """验证完整/续传响应与冻结实体长度完全一致，并返回可用 ETag。"""
    if status not in {200, 206}:
        raise AcquisitionBlocked(f"文件下载 HTTP 状态异常：{status}: {url}")
    if offset and status != 206:
        raise AcquisitionBlocked(f"断点响应未返回 206：{status}: {url}")
    if not offset and status != 200:
        raise AcquisitionBlocked(f"首次下载必须返回 200：{status}: {url}")

    content_length_text = str(headers.get("Content-Length", ""))
    if not content_length_text.isdecimal():
        raise AcquisitionBlocked(f"文件响应缺少精确 Content-Length：{url}")
    content_length = int(content_length_text)
    if offset:
        content_range = str(headers.get("Content-Range", ""))
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if match is None:
            raise AcquisitionBlocked(f"断点响应 Content-Range 非法：{content_range!r}")
        start, end, total = (int(value) for value in match.groups())
        if (
            start != offset
            or end != expected_size - 1
            or total != expected_size
            or content_length != expected_size - offset
        ):
            raise AcquisitionBlocked(
                f"断点响应范围与冻结大小不一致：{content_range!r}; "
                f"Content-Length={content_length}"
            )
    elif content_length != expected_size:
        raise AcquisitionBlocked(
            f"完整响应大小头漂移：{content_length}/{expected_size}: {url}"
        )
    return str(headers.get("ETag", "")).strip() or None


def download_file(
    url: str,
    target: Path,
    *,
    expected_size: int,
    expected_md5: str | None,
    expected_sha256: str | None,
) -> str:
    require_https_allowed(url)
    if target.exists():
        if not target.is_file() or is_reparse_point(target):
            raise AcquisitionBlocked(f"目标不是普通文件：{target}")
        if target.stat().st_size != expected_size:
            raise AcquisitionBlocked(f"既有目标大小失败，拒绝覆盖：{target}")
        if expected_md5 and file_digest(target, "md5") != expected_md5:
            raise AcquisitionBlocked(f"既有目标 MD5 失败，拒绝覆盖：{target}")
        if expected_sha256 and file_digest(target, "sha256") != expected_sha256:
            raise AcquisitionBlocked(f"既有目标 SHA256 失败，拒绝覆盖：{target}")
        return "reused_verified"

    partial = target.with_name(target.name + ".part")
    if partial.exists() and (not partial.is_file() or is_reparse_point(partial)):
        raise AcquisitionBlocked(f"下载临时目标不是普通文件：{partial}")
    if partial.exists() and partial.stat().st_size > expected_size:
        # 仅清理由本脚本为这个唯一目标创建的专用临时文件，避免永久卡死。
        partial.unlink()

    attempts_without_progress = 0
    restarted_without_range = False
    entity_tag: str | None = None
    request_count = 0
    started_at = time.monotonic()
    while (partial.stat().st_size if partial.exists() else 0) < expected_size:
        request_count += 1
        if request_count > MAX_DOWNLOAD_REQUESTS:
            raise AcquisitionBlocked(f"文件下载超过总请求上限：{url}")
        if time.monotonic() - started_at > MAX_DOWNLOAD_SECONDS:
            raise AcquisitionBlocked(f"文件下载超过总时限：{url}")
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            if entity_tag:
                headers["If-Range"] = entity_tag
        try:
            response = open_request(Request(url, headers=headers), timeout=180)
            status = int(getattr(response, "status", response.getcode()))
            if offset and status != 206:
                response.close()
                if restarted_without_range:
                    raise AcquisitionBlocked(f"端点不支持可靠续传：{url}")
                partial.unlink(missing_ok=True)
                restarted_without_range = True
                continue
            try:
                response_tag = validate_download_response_headers(
                    status=status,
                    headers=response.headers,
                    offset=offset,
                    expected_size=expected_size,
                    url=url,
                )
            except AcquisitionBlocked:
                response.close()
                partial.unlink(missing_ok=True)
                raise
            if response_tag:
                if entity_tag is not None and response_tag != entity_tag:
                    response.close()
                    partial.unlink(missing_ok=True)
                    raise AcquisitionBlocked(f"断点响应 ETag 漂移：{url}")
                entity_tag = response_tag
            before = offset
            with response, partial.open("ab" if offset else "wb") as handle:
                remaining = expected_size - offset
                while True:
                    block = response.read(min(4 * 1024 * 1024, remaining + 1))
                    if not block:
                        break
                    if len(block) > remaining:
                        raise AcquisitionBlocked(f"下载超过固定大小：{target.name}")
                    handle.write(block)
                    remaining -= len(block)
                handle.flush()
                os.fsync(handle.fileno())
            after = partial.stat().st_size
            attempts_without_progress = 0 if after > before else attempts_without_progress + 1
        except AcquisitionBlocked:
            partial.unlink(missing_ok=True)
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            after = partial.stat().st_size if partial.exists() else 0
            attempts_without_progress = 0 if after > offset else attempts_without_progress + 1
            if attempts_without_progress >= 5:
                raise AcquisitionBlocked(f"文件下载连续失败：{url}: {exc}") from exc
            time.sleep(min(2 ** attempts_without_progress, 16))

    if partial.stat().st_size != expected_size:
        raise AcquisitionBlocked(
            f"下载大小不完整：{target.name}={partial.stat().st_size}/{expected_size}"
        )
    if expected_md5 and file_digest(partial, "md5") != expected_md5:
        partial.unlink(missing_ok=True)
        raise AcquisitionBlocked(f"下载 MD5 不匹配：{target.name}")
    actual_sha256 = file_digest(partial, "sha256")
    if expected_sha256 and actual_sha256 != expected_sha256:
        partial.unlink(missing_ok=True)
        raise AcquisitionBlocked(f"下载 SHA256 不匹配：{target.name}")
    os.replace(partial, target)
    return "downloaded_verified"


MANIFEST_COLUMNS = [
    "source_directory",
    "provider",
    "record_id_or_commit",
    "doi",
    "official_key",
    "local_filename",
    "bytes",
    "official_md5",
    "local_sha256",
    "download_url",
    "license",
    "redistribution_assumption",
    "training_allowed",
    "local_state",
]


def render_manifest(rows: list[dict[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=MANIFEST_COLUMNS, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def acquire_source(spec: SourceSpec) -> dict[str, object]:
    directory = ensure_source_directory(spec)
    metadata, official = fetch_record(spec)
    rows: list[dict[str, object]] = []
    for expected in sorted(spec.files, key=lambda item: item.official_key.casefold()):
        item = official[expected.official_key]
        target = directory / expected.target_name
        require_safe_local_name(expected.target_name)
        state = download_file(
            str(item["url"]),
            target,
            expected_size=expected.size,
            expected_md5=expected.md5,
            expected_sha256=expected.sha256,
        )
        sha256 = file_digest(target, "sha256")
        rows.append(
            {
                "source_directory": spec.directory,
                "provider": "Zenodo",
                "record_id_or_commit": str(spec.record_id),
                "doi": spec.doi,
                "official_key": expected.official_key,
                "local_filename": expected.target_name,
                "bytes": expected.size,
                "official_md5": expected.md5,
                "local_sha256": sha256,
                "download_url": item["url"],
                "license": spec.license_id,
                "redistribution_assumption": "按Zenodo记录许可证；保留原始归属与引用",
                "training_allowed": "false",
                "local_state": "verified_present",
            }
        )
        print(f"{spec.directory}: {expected.target_name}: {state}", flush=True)

    if spec.directory == IMPACT:
        git_provenance, git_captures = fetch_git_provenance()
        auxiliary_rows: list[dict[str, object]] = []
        for auxiliary in GIT_AUXILIARY:
            require_safe_local_name(auxiliary.local_name)
            target = directory / auxiliary.local_name
            state = download_file(
                auxiliary.url,
                target,
                expected_size=auxiliary.size,
                expected_md5=None,
                expected_sha256=auxiliary.sha256,
            )
            auxiliary_rows.append(
                {
                    "source_directory": spec.directory,
                    "provider": "GitHub_raw",
                    "record_id_or_commit": PINNED_GIT_COMMIT,
                    "doi": "",
                    "official_key": auxiliary.local_name,
                    "local_filename": auxiliary.local_name,
                    "bytes": auxiliary.size,
                    "official_md5": "",
                    "local_sha256": file_digest(target, "sha256"),
                    "download_url": auxiliary.url,
                    "license": "no-license-detected",
                    "redistribution_assumption": "禁止假定再分发；仅固定提交下的研究复算辅助输入",
                    "training_allowed": "false",
                    "local_state": "verified_present",
                }
            )
            print(f"{spec.directory}: {auxiliary.local_name}: {state}", flush=True)
        rows.extend(auxiliary_rows)
        metadata["raw_api_captures"].extend(git_captures)
        metadata["auxiliary_git_inputs"] = {
            **git_provenance,
            "files": [item.local_name for item in GIT_AUXILIARY],
        }

    metadata["files"] = [
        {
            key: row[key]
            for key in (
                "provider",
                "record_id_or_commit",
                "official_key",
                "local_filename",
                "bytes",
                "official_md5",
                "local_sha256",
                "download_url",
                "license",
                "redistribution_assumption",
                "training_allowed",
            )
        }
        for row in rows
    ]
    atomic_write(
        directory / METADATA_NAME,
        (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    atomic_write(directory / MANIFEST_NAME, render_manifest(rows))
    return {
        "source": spec.directory,
        "record_id": spec.record_id,
        "files": len(rows),
        "bytes": sum(int(row["bytes"]) for row in rows),
    }


def validate_frozen_manifest() -> None:
    if [source.record_id for source in SOURCES] != [7_811_383, 10_817_092, 17_790_918, 5_099_589]:
        raise AcquisitionBlocked("固定 Zenodo 记录顺序漂移")
    if len({source.directory for source in SOURCES}) != 4:
        raise AcquisitionBlocked("来源目录名重复")
    local_targets: set[tuple[str, str]] = set()
    for source in SOURCES:
        require_safe_local_name(source.directory)
        api_url = f"https://zenodo.org/api/records/{source.record_id}"
        require_https_allowed(api_url)
        if len({item.official_key for item in source.files}) != len(source.files):
            raise AcquisitionBlocked(f"固定官方文件名重复：{source.directory}")
        for item in source.files:
            require_safe_local_name(item.target_name)
            key = (source.directory, item.target_name.casefold())
            if key in local_targets:
                raise AcquisitionBlocked(f"本地目标名冲突：{source.directory}/{item.target_name}")
            local_targets.add(key)
            if item.size <= 0 or len(item.md5) != 32:
                raise AcquisitionBlocked(f"固定文件大小或 MD5 非法：{item.official_key}")
            if item.sha256 is not None and len(item.sha256) != 64:
                raise AcquisitionBlocked(f"固定 SHA256 非法：{item.official_key}")
    if sum(len(source.files) for source in SOURCES) != 34:
        raise AcquisitionBlocked("固定 Zenodo 文件总数不是 34")
    for item in GIT_AUXILIARY:
        require_safe_local_name(item.local_name)
        require_https_allowed(item.url)
        if PINNED_GIT_COMMIT not in item.url or item.size <= 0 or len(item.sha256) != 64:
            raise AcquisitionBlocked(f"Git 辅助输入白名单非法：{item.local_name}")


def main() -> int:
    validate_frozen_manifest()
    results = [acquire_source(source) for source in SOURCES]
    print(
        json.dumps(
            {
                "status": "pass",
                "sources": results,
                "training_allowed": False,
                "weights_adjusted": False,
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
    except AcquisitionBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
