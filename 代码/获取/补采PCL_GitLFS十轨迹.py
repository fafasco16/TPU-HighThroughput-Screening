"""补采 Zenodo PCL 归档中仅以 Git LFS 指针表示的十条轨迹。

此脚本只负责来源固定、下载恢复和逐对象完整性校验，不分配训练权重。
原始载荷保留在独立目录，避免改变既有第三批模拟来源的审计口径。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


项目根 = Path(__file__).resolve().parents[2]
来源目录 = 项目根 / "数据/原始" / "外部数据" / "新增开放数据"
原始归档目录 = 来源目录 / "Zenodo_PCL软段构象粗粒化MD"
原始归档 = 原始归档目录 / "PCL_Supplementary_material_systematic_CG-v1.0_2.zip"
补采目录 = 来源目录 / "PCL_GitLFS轨迹补采"
载荷目录 = 补采目录 / "轨迹载荷"
快照目录 = 补采目录 / "来源快照"

仓库 = "pbacova/PCL_Supplementary_material_systematic_CG"
批量接口 = f"https://github.com/{仓库}.git/info/lfs/objects/batch"

# 委托任务给出的提交在 PCL 仓库中不存在，必须作为异常留证，不能伪造解析结果。
请求固定提交 = "46683548a86a7b3c9007abe9b18da82ecb14dfe3"
# Zenodo ZIP 顶层目录和 GitHub 历史共同解析出的真实固定提交。
归档对应提交 = "446ebadb9ba937d393b6cd7d727256c90e15f24e"
归档对应树 = "51894a12d912275f37a23853a76dbc2f36e09584"
归档根 = "pbacova-PCL_Supplementary_material_systematic_CG-446ebad"

归档字节 = 161_897_959
归档SHA256 = "5a59701e7a09f1f8b7907a0c9de70c86ffca05b4825812479b4ad4ad0a127002"
指针数量 = 10
指针总字节 = 2_313_207_356
安全保留字节 = 2 * 1024**3
单对象请求上限 = 12
块大小 = 8 * 1024 * 1024
用户代理 = "TPU-PCL-LFS-evidence-acquisition/1.0"

接口主机 = frozenset({"api.github.com", "github.com"})
载荷主机 = frozenset(
    {
        "github-cloud.githubusercontent.com",
        "objects.githubusercontent.com",
        "objects-origin.githubusercontent.com",
        "github-production-repository-file-5c1aeb.s3.amazonaws.com",
    }
)

指针格式 = re.compile(
    rb"\Aversion https://git-lfs\.github\.com/spec/v1\r?\n"
    rb"oid sha256:([0-9a-f]{64})\r?\nsize ([1-9][0-9]*)\r?\n?\Z"
)
范围格式 = re.compile(r"\Abytes ([0-9]+)-([0-9]+)/([0-9]+)\Z")


class 补采阻断(RuntimeError):
    """来源身份、网络边界或载荷完整性不满足固定协议。"""


@dataclass(frozen=True)
class 指针:
    归档路径: str
    仓库路径: str
    oid: str
    字节: int
    指针字节: bytes
    Git对象SHA1: str
    本地文件名: str


def 是重解析点(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(flag and attributes & flag) or bool(is_junction())


def 验证普通路径(path: Path, stop: Path) -> None:
    absolute = Path(os.path.abspath(path))
    stop_absolute = Path(os.path.abspath(stop))
    if absolute != stop_absolute and stop_absolute not in absolute.parents:
        raise 补采阻断(f"路径越出补采根：{absolute}")
    cursor = absolute
    while True:
        if 是重解析点(cursor):
            raise 补采阻断(f"拒绝符号链接、目录联接或重解析点：{cursor}")
        if cursor == stop_absolute:
            break
        cursor = cursor.parent


def 原子写入(path: Path, payload: bytes) -> None:
    验证普通路径(path, 补采目录)
    path.parent.mkdir(parents=True, exist_ok=True)
    临时 = path.with_name(path.name + ".写入中")
    with 临时.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(临时, path)


def 文件SHA256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(块大小):
            digest.update(chunk)
    return digest.hexdigest()


def URL哈希(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def 验证URL(url: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise 补采阻断(f"仅允许无凭据、无片段的 HTTPS URL：{URL哈希(url)}")
    host = (parsed.hostname or "").lower()
    if host not in allowed_hosts:
        raise 补采阻断(f"URL 主机不在白名单：{host}")


class 严格重定向(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts
        self.hops: list[dict[str, object]] = []

    def redirect_request(  # noqa: PLR0913
        self,
        req: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        验证URL(req.full_url, self.allowed_hosts)
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        验证URL(absolute, self.allowed_hosts)
        self.hops.append(
            {
                "状态码": code,
                "来源主机": urllib.parse.urlsplit(req.full_url).hostname,
                "目标主机": urllib.parse.urlsplit(absolute).hostname,
                "来源URL_SHA256": URL哈希(req.full_url),
                "目标URL_SHA256": URL哈希(absolute),
            }
        )
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def 创建开启器(allowed_hosts: frozenset[str]) -> tuple[urllib.request.OpenerDirector, 严格重定向]:
    handler = 严格重定向(allowed_hosts)
    return urllib.request.build_opener(handler), handler


def 响应头证据(
    *,
    status: int,
    initial_url: str,
    final_url: str,
    headers: object,
    hops: list[dict[str, object]],
) -> dict[str, object]:
    header_items = sorted((str(k), str(v)) for k, v in headers.items())
    return {
        "状态码": status,
        "初始主机": urllib.parse.urlsplit(initial_url).hostname,
        "最终主机": urllib.parse.urlsplit(final_url).hostname,
        "初始URL_SHA256": URL哈希(initial_url),
        "最终URL_SHA256": URL哈希(final_url),
        "重定向逐跳证据": hops,
        "响应头": {key: value for key, value in header_items},
    }


def 验证载荷响应证据(point: 指针, evidence: object) -> dict[str, object]:
    """复核可持久化的最后一段 Range 响应及最终 OID 闭包。"""
    if not isinstance(evidence, dict):
        raise 补采阻断(f"载荷响应证据不是对象：{point.oid}")
    try:
        status = int(evidence.get("状态码", -1))
        start = int(evidence.get("本次起点", -1))
        request_index = int(evidence.get("请求序号", -1))
        declared_size = int(evidence.get("声明字节", -1))
        final_size = int(evidence.get("最终载荷字节", -1))
    except (TypeError, ValueError) as exc:
        raise 补采阻断(f"载荷响应证据数值字段非法：{point.oid}") from exc
    if (
        status != 206
        or request_index < 1
        or start < 0
        or start >= point.字节
        or declared_size != point.字节
        or final_size != point.字节
        or evidence.get("OID") != point.oid
        or evidence.get("最终载荷SHA256") != point.oid
    ):
        raise 补采阻断(f"载荷响应证据身份或最终闭包漂移：{point.oid}")
    initial_host = str(evidence.get("初始主机", "")).lower()
    final_host = str(evidence.get("最终主机", "")).lower()
    if initial_host not in 载荷主机 or final_host not in 载荷主机:
        raise 补采阻断(f"载荷响应证据主机越界：{point.oid}")
    for key in ("初始URL_SHA256", "最终URL_SHA256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(evidence.get(key, ""))) is None:
            raise 补采阻断(f"载荷响应证据 URL 哈希非法：{point.oid}/{key}")
    hops = evidence.get("重定向逐跳证据")
    if not isinstance(hops, list):
        raise 补采阻断(f"载荷响应证据缺少逐跳重定向：{point.oid}")
    for hop in hops:
        try:
            valid_hop = (
                isinstance(hop, dict)
                and str(hop.get("来源主机", "")).lower() in 载荷主机
                and str(hop.get("目标主机", "")).lower() in 载荷主机
                and int(hop.get("状态码", -1)) in {301, 302, 303, 307, 308}
            )
        except (TypeError, ValueError):
            valid_hop = False
        if not valid_hop:
            raise 补采阻断(f"载荷响应证据重定向越界：{point.oid}")
    raw_headers = evidence.get("响应头")
    if not isinstance(raw_headers, dict):
        raise 补采阻断(f"载荷响应证据缺少响应头：{point.oid}")
    headers = {str(key).casefold(): str(value) for key, value in raw_headers.items()}
    match = 范围格式.fullmatch(headers.get("content-range", ""))
    try:
        content_length = int(headers.get("content-length", "-1"))
    except ValueError as exc:
        raise 补采阻断(f"载荷响应 Content-Length 非法：{point.oid}") from exc
    if (
        match is None
        or tuple(map(int, match.groups())) != (start, point.字节 - 1, point.字节)
        or content_length != point.字节 - start
    ):
        raise 补采阻断(f"载荷响应 Range 证据不精确：{point.oid}")
    return evidence


def 读取载荷响应证据(point: 指针, path: Path) -> dict[str, object]:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise 补采阻断(f"载荷响应证据不可读：{point.oid}") from exc
    return 验证载荷响应证据(point, evidence)


def 捕获一次(
    name: str,
    request: urllib.request.Request,
    *,
    allowed_hosts: frozenset[str],
    expected_status: int,
) -> tuple[bytes, dict[str, object]]:
    body_path = 快照目录 / f"{name}_响应.json"
    headers_path = 快照目录 / f"{name}_响应头.json"
    if body_path.exists() != headers_path.exists():
        raise 补采阻断(f"来源快照只存在一半：{name}")
    if body_path.exists():
        body = body_path.read_bytes()
        evidence = json.loads(headers_path.read_text(encoding="utf-8"))
        if int(evidence["状态码"]) != expected_status:
            raise 补采阻断(f"既有来源快照状态码漂移：{name}")
        return body, evidence

    验证URL(request.full_url, allowed_hosts)
    opener, handler = 创建开启器(allowed_hosts)
    try:
        with opener.open(request, timeout=60) as response:
            status = int(response.status)
            body = response.read()
            final_url = response.geturl()
            headers = response.headers
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
        final_url = exc.geturl()
        headers = exc.headers
    if status != expected_status:
        raise 补采阻断(f"来源接口状态码漂移：{name}={status}，期望{expected_status}")
    验证URL(final_url, allowed_hosts)
    evidence = 响应头证据(
        status=status,
        initial_url=request.full_url,
        final_url=final_url,
        headers=headers,
        hops=handler.hops,
    )
    原子写入(body_path, body)
    原子写入(
        headers_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )
    return body, evidence


def Git对象SHA1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def 本地文件名(repo_path: str) -> str:
    parts = PurePosixPath(repo_path).parts
    if len(parts) < 3 or parts[-1] != "traj.trr.bz2":
        raise 补采阻断(f"无法生成安全文件名：{repo_path}")
    for part in parts:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", part):
            raise 补采阻断(f"仓库路径包含未允许字符：{repo_path}")
    translated = {"solvated": "溶剂", "vacuum": "真空"}
    stem_parts = [translated.get(part, part) for part in parts[:-1]]
    if len(stem_parts) == 2:
        stem_parts.append("默认")
    return "_".join(stem_parts) + "_轨迹.trr.bz2"


def 读取指针() -> list[指针]:
    if not 原始归档.is_file():
        raise 补采阻断(f"缺少固定 Zenodo 归档：{原始归档}")
    if 原始归档.stat().st_size != 归档字节 or 文件SHA256(原始归档) != 归档SHA256:
        raise 补采阻断("Zenodo PCL 固定归档字节或 SHA256 漂移")
    rows: list[指针] = []
    with zipfile.ZipFile(原始归档) as archive:
        for info in archive.infolist():
            path = info.filename.replace("\\", "/")
            if not path.endswith("/traj.trr.bz2"):
                continue
            payload = archive.read(info)
            match = 指针格式.fullmatch(payload)
            if not match:
                continue
            pure = PurePosixPath(path)
            if not pure.parts or pure.parts[0] != 归档根:
                raise 补采阻断(f"Git LFS 指针越出固定归档根：{path}")
            repo_path = PurePosixPath(*pure.parts[1:]).as_posix()
            rows.append(
                指针(
                    归档路径=path,
                    仓库路径=repo_path,
                    oid=match.group(1).decode("ascii"),
                    字节=int(match.group(2)),
                    指针字节=payload,
                    Git对象SHA1=Git对象SHA1(payload),
                    本地文件名=本地文件名(repo_path),
                )
            )
    rows.sort(key=lambda item: item.仓库路径)
    if len(rows) != 指针数量 or sum(item.字节 for item in rows) != 指针总字节:
        raise 补采阻断(
            f"Git LFS 指针集合漂移：{len(rows)}条/{sum(item.字节 for item in rows)}字节"
        )
    if len({item.oid for item in rows}) != 指针数量:
        raise 补采阻断("Git LFS OID 不唯一")
    if len({item.本地文件名 for item in rows}) != 指针数量:
        raise 补采阻断("本地扁平文件名发生碰撞")
    return rows


def JSON请求(url: str, *, data: bytes | None = None) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": 用户代理,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = "application/vnd.git-lfs+json"
        headers["Accept"] = "application/vnd.git-lfs+json"
    return urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")


def 核验来源指针(points: list[指针]) -> dict[str, object]:
    bad_url = f"https://api.github.com/repos/{仓库}/commits/{请求固定提交}"
    bad_body, bad_headers = 捕获一次(
        "请求固定提交",
        JSON请求(bad_url),
        allowed_hosts=接口主机,
        expected_status=422,
    )
    bad_json = json.loads(bad_body)
    if "No commit found" not in str(bad_json.get("message", "")):
        raise 补采阻断("请求固定提交的失败语义漂移")

    commit_url = f"https://api.github.com/repos/{仓库}/commits/{归档对应提交}"
    commit_body, commit_headers = 捕获一次(
        "归档对应提交",
        JSON请求(commit_url),
        allowed_hosts=接口主机,
        expected_status=200,
    )
    commit = json.loads(commit_body)
    tree_sha = str(commit.get("commit", {}).get("tree", {}).get("sha", ""))
    if commit.get("sha") != 归档对应提交 or tree_sha != 归档对应树:
        raise 补采阻断("归档对应 Git 提交或 tree SHA 漂移")

    tree_url = f"https://api.github.com/repos/{仓库}/git/trees/{归档对应树}?recursive=1"
    tree_body, tree_headers = 捕获一次(
        "归档对应树",
        JSON请求(tree_url),
        allowed_hosts=接口主机,
        expected_status=200,
    )
    tree = json.loads(tree_body)
    if tree.get("sha") != 归档对应树 or tree.get("truncated") is not False:
        raise 补采阻断("Git tree 身份漂移或递归结果被截断")
    tree_by_path = {str(row.get("path")): row for row in tree.get("tree", [])}
    for point in points:
        row = tree_by_path.get(point.仓库路径)
        if (
            row is None
            or row.get("type") != "blob"
            or row.get("sha") != point.Git对象SHA1
            or int(row.get("size", -1)) != len(point.指针字节)
        ):
            raise 补采阻断(f"固定 tree 中指针 blob 不一致：{point.仓库路径}")

    repo_url = f"https://api.github.com/repos/{仓库}"
    repo_body, repo_headers = 捕获一次(
        "仓库身份",
        JSON请求(repo_url),
        allowed_hosts=接口主机,
        expected_status=200,
    )
    repo = json.loads(repo_body)
    if repo.get("full_name") != 仓库:
        raise 补采阻断("GitHub 仓库身份漂移")
    license_paths = sorted(
        path
        for path in tree_by_path
        if PurePosixPath(path).name.upper().startswith(("LICENSE", "COPYING"))
    )
    if repo.get("license") is not None or license_paths:
        raise 补采阻断("仓库许可状态由无显式 LICENSE 漂移，需人工重审")

    snapshots = {}
    for name, _body, _headers in (
        ("请求固定提交", bad_body, bad_headers),
        ("归档对应提交", commit_body, commit_headers),
        ("归档对应树", tree_body, tree_headers),
        ("仓库身份", repo_body, repo_headers),
    ):
        snapshots[name] = {
            "响应SHA256": 文件SHA256(快照目录 / f"{name}_响应.json"),
            "响应头SHA256": 文件SHA256(快照目录 / f"{name}_响应头.json"),
        }
    return {
        "请求固定提交": 请求固定提交,
        "请求固定提交状态": "PCL仓库内不存在（GitHub API 422，原始响应已留证）",
        "归档对应提交": 归档对应提交,
        "归档对应树": 归档对应树,
        "归档根": 归档根,
        "许可状态": (
            "GitHub 仓库元数据 license=null 且固定 tree 未发现 LICENSE/COPYING；"
            "仅保留为可追溯科研证据，不声明再分发或训练许可"
        ),
        "训练许可": False,
        "来源快照": snapshots,
    }


def 已有载荷有效(point: 指针) -> bool:
    target = 载荷目录 / point.本地文件名
    if not target.exists():
        return False
    验证普通路径(target, 补采目录)
    if not target.is_file() or target.stat().st_size != point.字节:
        raise 补采阻断(f"既有最终载荷字节漂移，拒绝覆盖：{target.name}")
    if 文件SHA256(target) != point.oid:
        raise 补采阻断(f"既有最终载荷 SHA256 漂移，拒绝覆盖：{target.name}")
    return True


def 磁盘预检(points: list[指针]) -> tuple[int, int]:
    remaining = 0
    for point in points:
        if 已有载荷有效(point):
            continue
        part = (载荷目录 / point.本地文件名).with_suffix(".bz2.part")
        if part.exists():
            验证普通路径(part, 补采目录)
            if not part.is_file() or part.stat().st_size > point.字节:
                raise 补采阻断(f"断点文件非法：{part.name}")
            remaining += point.字节 - part.stat().st_size
        else:
            remaining += point.字节
    free = shutil.disk_usage(补采目录.parent).free
    if free < remaining + 安全保留字节:
        raise 补采阻断(
            f"磁盘余量不足：free={free}, remaining={remaining}, reserve={安全保留字节}"
        )
    return free, remaining


def 获取批量动作(points: list[指针]) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    payload = {
        "operation": "download",
        "transfers": ["basic"],
        "ref": {"name": 归档对应提交},
        "objects": [{"oid": point.oid, "size": point.字节} for point in points],
    }
    request_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    原子写入(快照目录 / "GitLFS批量_请求.json", request_bytes)
    request = JSON请求(批量接口, data=request_bytes)
    验证URL(request.full_url, 接口主机)
    opener, handler = 创建开启器(接口主机)
    with opener.open(request, timeout=60) as response:
        status = int(response.status)
        body = response.read()
        final_url = response.geturl()
        headers = response.headers
    if status != 200:
        raise 补采阻断(f"Git LFS batch 状态码漂移：{status}")
    验证URL(final_url, 接口主机)
    header_evidence = 响应头证据(
        status=status,
        initial_url=request.full_url,
        final_url=final_url,
        headers=headers,
        hops=handler.hops,
    )
    原子写入(快照目录 / "GitLFS批量_响应.json", body)
    原子写入(
        快照目录 / "GitLFS批量_响应头.json",
        json.dumps(header_evidence, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )
    parsed = json.loads(body)
    objects = parsed.get("objects")
    if not isinstance(objects, list) or len(objects) != len(points):
        raise 补采阻断("Git LFS batch 对象数量漂移")
    expected = {point.oid: point.字节 for point in points}
    actions: dict[str, dict[str, object]] = {}
    for obj in objects:
        oid = str(obj.get("oid", ""))
        size = int(obj.get("size", -1))
        if oid not in expected or expected[oid] != size or "error" in obj:
            raise 补采阻断(f"Git LFS batch 对象身份或可用性异常：{oid}")
        action = obj.get("actions", {}).get("download")
        if not isinstance(action, dict) or not isinstance(action.get("href"), str):
            raise 补采阻断(f"Git LFS 对象缺少 download action：{oid}")
        验证URL(str(action["href"]), 载荷主机)
        action_headers = action.get("header", {})
        if not isinstance(action_headers, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in action_headers.items()
        ):
            raise 补采阻断(f"Git LFS action header 非字符串映射：{oid}")
        actions[oid] = action
    if set(actions) != set(expected):
        raise 补采阻断("Git LFS batch 返回 OID 集合漂移")
    snapshot = {
        "请求SHA256": hashlib.sha256(request_bytes).hexdigest(),
        "响应SHA256": hashlib.sha256(body).hexdigest(),
        "响应头SHA256": 文件SHA256(快照目录 / "GitLFS批量_响应头.json"),
    }
    return actions, snapshot


def 下载一个(point: 指针, action: dict[str, object]) -> dict[str, object]:
    target = 载荷目录 / point.本地文件名
    part = target.with_suffix(".bz2.part")
    response_evidence_path = 快照目录 / f"载荷响应_{point.oid}.json"
    if 已有载荷有效(point):
        if not response_evidence_path.is_file():
            raise 补采阻断(f"已有载荷缺失下载响应证据：{point.oid}")
        return 读取载荷响应证据(point, response_evidence_path)
    载荷目录.mkdir(parents=True, exist_ok=True)
    验证普通路径(target, 补采目录)
    if part.exists() and part.stat().st_size > point.字节:
        raise 补采阻断(f"断点文件大于声明对象：{part.name}")
    if part.exists() and part.stat().st_size == point.字节:
        actual_sha = 文件SHA256(part)
        if actual_sha != point.oid:
            raise 补采阻断(
                f"完整断点文件 SHA256 不符，保留供取证：{point.oid}/{actual_sha}"
            )
        if response_evidence_path.is_file():
            evidence = 读取载荷响应证据(point, response_evidence_path)
            os.replace(part, target)
            return evidence
        # 旧版本可能在完整载荷 fsync 后、响应证据落盘前中断。没有网络证据时
        # 不把临时载荷晋升为正式文件；删除专用 .part 后从新 batch 动作重取。
        part.unlink()

    href = str(action["href"])
    action_headers = {str(k): str(v) for k, v in dict(action.get("header", {})).items()}
    last_evidence: dict[str, object] | None = None
    for attempt in range(1, 单对象请求上限 + 1):
        offset = part.stat().st_size if part.exists() else 0
        if offset == point.字节:
            break
        request_headers = dict(action_headers)
        request_headers.update(
            {
                "Range": f"bytes={offset}-{point.字节 - 1}",
                "User-Agent": 用户代理,
                "Accept-Encoding": "identity",
            }
        )
        request = urllib.request.Request(href, headers=request_headers, method="GET")
        验证URL(request.full_url, 载荷主机)
        opener, handler = 创建开启器(载荷主机)
        try:
            with opener.open(request, timeout=120) as response:
                status = int(response.status)
                final_url = response.geturl()
                验证URL(final_url, 载荷主机)
                content_range = str(response.headers.get("Content-Range", ""))
                match = 范围格式.fullmatch(content_range)
                expected_length = point.字节 - offset
                declared_length = int(response.headers.get("Content-Length", "-1"))
                if (
                    status != 206
                    or match is None
                    or tuple(map(int, match.groups()))
                    != (offset, point.字节 - 1, point.字节)
                    or declared_length != expected_length
                ):
                    raise 补采阻断(
                        f"Range 响应不精确：{point.oid}/status={status}/"
                        f"range={content_range}/length={declared_length}"
                    )
                last_evidence = 响应头证据(
                    status=status,
                    initial_url=request.full_url,
                    final_url=final_url,
                    headers=response.headers,
                    hops=handler.hops,
                )
                last_evidence.update(
                    {
                        "OID": point.oid,
                        "声明字节": point.字节,
                        "本次起点": offset,
                        "请求序号": attempt,
                    }
                )
                received = 0
                with part.open("ab") as stream:
                    while chunk := response.read(块大小):
                        stream.write(chunk)
                        received += len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if received != expected_length:
                    raise OSError(
                        f"响应提前结束：received={received}, expected={expected_length}"
                    )
        except 补采阻断:
            raise
        except (
            OSError,
            TimeoutError,
            http.client.IncompleteRead,
            urllib.error.URLError,
        ) as exc:
            if part.exists() and part.stat().st_size > point.字节:
                raise 补采阻断(f"失败后断点越过对象边界：{point.oid}") from exc
            if attempt == 单对象请求上限:
                raise 补采阻断(f"对象下载超过总请求上限：{point.oid}") from exc
            time.sleep(min(2 ** (attempt - 1), 20))

    if not part.is_file() or part.stat().st_size != point.字节:
        raise 补采阻断(f"对象断点文件最终字节不符：{point.oid}")
    actual_sha = 文件SHA256(part)
    if actual_sha != point.oid:
        raise 补采阻断(f"对象 SHA256 不符，保留 .part 供取证：{point.oid}/{actual_sha}")
    if last_evidence is None:
        raise 补采阻断(f"对象完成但没有本次响应证据：{point.oid}")
    last_evidence["最终载荷SHA256"] = actual_sha
    last_evidence["最终载荷字节"] = part.stat().st_size
    验证载荷响应证据(point, last_evidence)
    原子写入(
        response_evidence_path,
        json.dumps(last_evidence, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )
    # 先持久化并复核响应/OID 证据，再晋升载荷。这样在两步之间崩溃时，
    # 下一次运行能用“完整 .part + 已闭合证据”无网络恢复，而不会形成死锁。
    os.replace(part, target)
    return last_evidence


def 写清单(
    points: list[指针],
    provenance: dict[str, object],
    batch_snapshot: dict[str, object],
    response_evidence: dict[str, dict[str, object]],
) -> None:
    rows = []
    for point in points:
        target = 载荷目录 / point.本地文件名
        rows.append(
            {
                "归档路径": point.归档路径,
                "仓库路径": point.仓库路径,
                "GitLFS_OID_SHA256": point.oid,
                "GitLFS声明字节": point.字节,
                "指针Git对象SHA1": point.Git对象SHA1,
                "本地相对路径": target.relative_to(补采目录).as_posix(),
                "本地字节": target.stat().st_size,
                "本地SHA256": 文件SHA256(target),
                "下载最终URL_SHA256": response_evidence[point.oid]["最终URL_SHA256"],
                "下载最终主机": response_evidence[point.oid]["最终主机"],
                "训练许可": False,
            }
        )
    manifest = {
        "清单版本": "pcl-git-lfs-acquisition-v1.0",
        "来源": "PCL Git LFS 十轨迹补采",
        "仓库": 仓库,
        "Zenodo归档": {
            "DOI": "10.5281/zenodo.17790918",
            "文件": 原始归档.name,
            "字节": 归档字节,
            "SHA256": 归档SHA256,
        },
        "来源固定": provenance,
        "GitLFS批量快照": batch_snapshot,
        "对象数": len(rows),
        "对象总字节": sum(int(row["本地字节"]) for row in rows),
        "许可说明": provenance["许可状态"],
        "训练许可": False,
        "训练权重": None,
        "对象": rows,
    }
    原子写入(
        补采目录 / "补采清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )


def main() -> None:
    补采目录.mkdir(parents=True, exist_ok=True)
    载荷目录.mkdir(parents=True, exist_ok=True)
    快照目录.mkdir(parents=True, exist_ok=True)
    验证普通路径(补采目录, 补采目录)
    验证普通路径(载荷目录, 补采目录)
    验证普通路径(快照目录, 补采目录)
    points = 读取指针()
    free, remaining = 磁盘预检(points)
    print(f"磁盘预检通过：可用 {free:,} B；尚需 {remaining:,} B；保留 {安全保留字节:,} B")
    provenance = 核验来源指针(points)

    missing = [point for point in points if not 已有载荷有效(point)]
    batch_snapshot: dict[str, object]
    actions: dict[str, dict[str, object]] = {}
    if missing:
        actions, batch_snapshot = 获取批量动作(missing)
    else:
        request_path = 快照目录 / "GitLFS批量_请求.json"
        response_path = 快照目录 / "GitLFS批量_响应.json"
        headers_path = 快照目录 / "GitLFS批量_响应头.json"
        if not all(path.is_file() for path in (request_path, response_path, headers_path)):
            raise 补采阻断("载荷齐全但缺少 Git LFS batch 原始快照")
        batch_snapshot = {
            "请求SHA256": 文件SHA256(request_path),
            "响应SHA256": 文件SHA256(response_path),
            "响应头SHA256": 文件SHA256(headers_path),
        }

    response_evidence: dict[str, dict[str, object]] = {}
    for index, point in enumerate(points, start=1):
        print(f"[{index}/{len(points)}] {point.仓库路径} ({point.字节:,} B)")
        if point in missing:
            evidence = 下载一个(point, actions[point.oid])
        else:
            evidence_path = 快照目录 / f"载荷响应_{point.oid}.json"
            if not evidence_path.is_file():
                raise 补采阻断(f"载荷响应证据缺失：{point.oid}")
            evidence = 读取载荷响应证据(point, evidence_path)
        response_evidence[point.oid] = evidence

    写清单(points, provenance, batch_snapshot, response_evidence)
    print(f"补采完成：{len(points)} 个对象，{sum(point.字节 for point in points):,} B")


if __name__ == "__main__":
    main()
