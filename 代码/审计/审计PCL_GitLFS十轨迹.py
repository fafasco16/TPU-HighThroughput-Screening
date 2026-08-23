"""独立审计 PCL Git LFS 补采轨迹的来源、载荷、TRR 全帧和运行协议。

输出只描述证据与相关性，不给训练准入或权重。所有帧属于各自模拟运行，
不能把帧数当作独立材料或独立样本数。
"""

from __future__ import annotations

import bz2
import csv
import hashlib
import json
import math
import os
import re
import stat
import struct
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import BinaryIO


项目根 = Path(__file__).resolve().parents[2]
来源目录 = 项目根 / "数据/原始" / "外部数据" / "新增开放数据"
原始归档目录 = 来源目录 / "Zenodo_PCL软段构象粗粒化MD"
原始归档 = 原始归档目录 / "PCL_Supplementary_material_systematic_CG-v1.0_2.zip"
补采目录 = 来源目录 / "PCL_GitLFS轨迹补采"
载荷目录 = 补采目录 / "轨迹载荷"
快照目录 = 补采目录 / "来源快照"
清单路径 = 补采目录 / "补采清单.json"

仓库 = "pbacova/PCL_Supplementary_material_systematic_CG"
请求固定提交 = "46683548a86a7b3c9007abe9b18da82ecb14dfe3"
归档对应提交 = "446ebadb9ba937d393b6cd7d727256c90e15f24e"
归档对应树 = "51894a12d912275f37a23853a76dbc2f36e09584"
归档根 = "pbacova-PCL_Supplementary_material_systematic_CG-446ebad"

归档字节 = 161_897_959
归档SHA256 = "5a59701e7a09f1f8b7907a0c9de70c86ffca05b4825812479b4ad4ad0a127002"
指针数量 = 10
指针总字节 = 2_313_207_356
块大小 = 8 * 1024 * 1024
单轨迹最大解压字节 = 64 * 1024**3
单轨迹最大帧数 = 5_000_000

允许最终主机 = frozenset(
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


class 审计阻断(RuntimeError):
    """来源、文件、协议或科学语义未通过固定审计。"""


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
        raise 审计阻断(f"路径越出审计根：{path}")
    cursor = absolute
    while True:
        if 是重解析点(cursor):
            raise 审计阻断(f"拒绝符号链接、目录联接或重解析点：{cursor}")
        if cursor == stop_absolute:
            break
        cursor = cursor.parent


def 文件SHA256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(块大小):
            digest.update(chunk)
    return digest.hexdigest()


def Git对象SHA1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def 读取JSON(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise 审计阻断(f"JSON 不可读：{path}") from exc


def 原子写入(path: Path, payload: bytes) -> None:
    验证普通路径(path, 补采目录)
    temp = path.with_name(path.name + ".写入中")
    with temp.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def 写TSV(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    temp = path.with_name(path.name + ".写入中")
    验证普通路径(temp, 补采目录)
    with temp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def 读取固定指针() -> tuple[list[dict[str, object]], zipfile.ZipFile]:
    if not 原始归档.is_file():
        raise 审计阻断("缺少固定 Zenodo PCL 归档")
    if 原始归档.stat().st_size != 归档字节 or 文件SHA256(原始归档) != 归档SHA256:
        raise 审计阻断("固定 Zenodo PCL 归档字节或 SHA256 漂移")
    archive = zipfile.ZipFile(原始归档)
    rows: list[dict[str, object]] = []
    try:
        for info in archive.infolist():
            path = info.filename.replace("\\", "/")
            if not path.endswith("/traj.trr.bz2"):
                continue
            payload = archive.read(info)
            match = 指针格式.fullmatch(payload)
            if match is None:
                continue
            pure = PurePosixPath(path)
            if pure.parts[0] != 归档根:
                raise 审计阻断(f"指针越出固定归档根：{path}")
            rows.append(
                {
                    "归档路径": path,
                    "仓库路径": PurePosixPath(*pure.parts[1:]).as_posix(),
                    "OID": match.group(1).decode("ascii"),
                    "字节": int(match.group(2)),
                    "指针字节": payload,
                    "Git对象SHA1": Git对象SHA1(payload),
                }
            )
    except Exception:
        archive.close()
        raise
    rows.sort(key=lambda row: str(row["仓库路径"]))
    if len(rows) != 指针数量 or sum(int(row["字节"]) for row in rows) != 指针总字节:
        archive.close()
        raise 审计阻断("固定归档的 Git LFS 指针数量或总字节漂移")
    if len({str(row["OID"]) for row in rows}) != 指针数量:
        archive.close()
        raise 审计阻断("Git LFS 指针 OID 不唯一")
    return rows, archive


def 核验快照哈希(manifest: dict[str, object]) -> None:
    provenance = manifest.get("来源固定")
    if not isinstance(provenance, dict):
        raise 审计阻断("补采清单缺少来源固定块")
    snapshots = provenance.get("来源快照")
    if not isinstance(snapshots, dict):
        raise 审计阻断("补采清单缺少来源快照哈希")
    for name in ("请求固定提交", "归档对应提交", "归档对应树", "仓库身份"):
        item = snapshots.get(name)
        if not isinstance(item, dict):
            raise 审计阻断(f"补采清单缺少来源快照：{name}")
        body = 快照目录 / f"{name}_响应.json"
        headers = 快照目录 / f"{name}_响应头.json"
        if 文件SHA256(body) != item.get("响应SHA256"):
            raise 审计阻断(f"来源快照响应哈希漂移：{name}")
        if 文件SHA256(headers) != item.get("响应头SHA256"):
            raise 审计阻断(f"来源快照响应头哈希漂移：{name}")

    batch = manifest.get("GitLFS批量快照")
    if not isinstance(batch, dict):
        raise 审计阻断("补采清单缺少 Git LFS batch 快照哈希")
    for key, filename in (
        ("请求SHA256", "GitLFS批量_请求.json"),
        ("响应SHA256", "GitLFS批量_响应.json"),
        ("响应头SHA256", "GitLFS批量_响应头.json"),
    ):
        if 文件SHA256(快照目录 / filename) != batch.get(key):
            raise 审计阻断(f"Git LFS batch 快照哈希漂移：{filename}")


def 核验来源快照(points: list[dict[str, object]], manifest: dict[str, object]) -> None:
    if manifest.get("清单版本") != "pcl-git-lfs-acquisition-v1.0":
        raise 审计阻断("补采清单版本漂移")
    if manifest.get("仓库") != 仓库 or manifest.get("训练许可") is not False:
        raise 审计阻断("仓库身份或训练许可状态漂移")
    if manifest.get("训练权重") is not None:
        raise 审计阻断("补采清单不应提前给训练权重")
    if int(manifest.get("对象数", -1)) != 指针数量:
        raise 审计阻断("补采清单对象数漂移")
    if int(manifest.get("对象总字节", -1)) != 指针总字节:
        raise 审计阻断("补采清单对象总字节漂移")
    provenance = manifest["来源固定"]
    if (
        provenance.get("请求固定提交") != 请求固定提交
        or "不存在" not in str(provenance.get("请求固定提交状态", ""))
        or provenance.get("归档对应提交") != 归档对应提交
        or provenance.get("归档对应树") != 归档对应树
        or provenance.get("训练许可") is not False
    ):
        raise 审计阻断("来源提交异常或实际锚点漂移")
    核验快照哈希(manifest)

    bad = 读取JSON(快照目录 / "请求固定提交_响应.json")
    bad_headers = 读取JSON(快照目录 / "请求固定提交_响应头.json")
    if (
        not isinstance(bad, dict)
        or "No commit found" not in str(bad.get("message", ""))
        or not isinstance(bad_headers, dict)
        or bad_headers.get("状态码") != 422
    ):
        raise 审计阻断("请求固定提交的 422 原始证据漂移")

    commit = 读取JSON(快照目录 / "归档对应提交_响应.json")
    if (
        not isinstance(commit, dict)
        or commit.get("sha") != 归档对应提交
        or commit.get("commit", {}).get("tree", {}).get("sha") != 归档对应树
    ):
        raise 审计阻断("实际 Git commit/tree 原始证据漂移")
    tree = 读取JSON(快照目录 / "归档对应树_响应.json")
    if not isinstance(tree, dict) or tree.get("sha") != 归档对应树 or tree.get("truncated") is not False:
        raise 审计阻断("Git tree 原始证据漂移或被截断")
    tree_by_path = {str(row.get("path")): row for row in tree.get("tree", [])}
    for point in points:
        row = tree_by_path.get(str(point["仓库路径"]))
        if (
            row is None
            or row.get("type") != "blob"
            or row.get("sha") != point["Git对象SHA1"]
            or int(row.get("size", -1)) != len(point["指针字节"])
        ):
            raise 审计阻断(f"Git tree 指针 blob 漂移：{point['仓库路径']}")
    if any(
        PurePosixPath(path).name.upper().startswith(("LICENSE", "COPYING"))
        for path in tree_by_path
    ):
        raise 审计阻断("固定 tree 许可文件状态漂移，需人工重新裁决")
    repo = 读取JSON(快照目录 / "仓库身份_响应.json")
    if not isinstance(repo, dict) or repo.get("full_name") != 仓库 or repo.get("license") is not None:
        raise 审计阻断("GitHub 仓库身份或无显式许可证状态漂移")

    batch_request = 读取JSON(快照目录 / "GitLFS批量_请求.json")
    batch_response = 读取JSON(快照目录 / "GitLFS批量_响应.json")
    if (
        not isinstance(batch_request, dict)
        or batch_request.get("ref", {}).get("name") != 归档对应提交
        or not isinstance(batch_response, dict)
    ):
        raise 审计阻断("Git LFS batch 请求未固定到实际归档提交")
    expected = {str(point["OID"]): int(point["字节"]) for point in points}
    request_objects = {
        str(row.get("oid")): int(row.get("size", -1))
        for row in batch_request.get("objects", [])
    }
    response_objects = {
        str(row.get("oid")): int(row.get("size", -1))
        for row in batch_response.get("objects", [])
    }
    if request_objects != expected or response_objects != expected:
        raise 审计阻断("Git LFS batch 请求或响应对象集合漂移")


def 核验载荷响应证据(
    response: object,
    point: dict[str, object],
    manifest_row: dict[str, object],
) -> None:
    if not isinstance(response, dict):
        raise 审计阻断(f"载荷响应证据不是对象：{point['OID']}")
    oid = str(point["OID"])
    size = int(point["字节"])
    try:
        status = int(response.get("状态码", -1))
        start = int(response.get("本次起点", -1))
        request_index = int(response.get("请求序号", -1))
        declared_size = int(response.get("声明字节", -1))
        final_size = int(response.get("最终载荷字节", -1))
    except (TypeError, ValueError) as exc:
        raise 审计阻断(f"载荷响应证据数值字段非法：{oid}") from exc
    if (
        status != 206
        or request_index < 1
        or start < 0
        or start >= size
        or declared_size != size
        or final_size != size
        or response.get("OID") != oid
        or response.get("最终载荷SHA256") != oid
        or response.get("最终URL_SHA256")
        != manifest_row.get("下载最终URL_SHA256")
    ):
        raise 审计阻断(f"载荷响应证据身份或最终闭包漂移：{oid}")
    initial_host = str(response.get("初始主机", "")).lower()
    final_host = str(response.get("最终主机", "")).lower()
    if initial_host not in 允许最终主机 or final_host not in 允许最终主机:
        raise 审计阻断(f"载荷响应证据主机越界：{oid}")
    for key in ("初始URL_SHA256", "最终URL_SHA256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(response.get(key, ""))) is None:
            raise 审计阻断(f"载荷响应证据 URL 哈希非法：{oid}/{key}")
    hops = response.get("重定向逐跳证据")
    if not isinstance(hops, list):
        raise 审计阻断(f"载荷响应证据缺少逐跳重定向：{oid}")
    for hop in hops:
        try:
            valid_hop = (
                isinstance(hop, dict)
                and str(hop.get("来源主机", "")).lower() in 允许最终主机
                and str(hop.get("目标主机", "")).lower() in 允许最终主机
                and int(hop.get("状态码", -1)) in {301, 302, 303, 307, 308}
            )
        except (TypeError, ValueError):
            valid_hop = False
        if not valid_hop:
            raise 审计阻断(f"载荷响应证据重定向越界：{oid}")
    raw_headers = response.get("响应头")
    if not isinstance(raw_headers, dict):
        raise 审计阻断(f"载荷响应证据缺少响应头：{oid}")
    headers = {str(key).casefold(): str(value) for key, value in raw_headers.items()}
    match = 范围格式.fullmatch(headers.get("content-range", ""))
    try:
        content_length = int(headers.get("content-length", "-1"))
    except ValueError as exc:
        raise 审计阻断(f"载荷响应 Content-Length 非法：{oid}") from exc
    if (
        match is None
        or tuple(map(int, match.groups())) != (start, size - 1, size)
        or content_length != size - start
    ):
        raise 审计阻断(f"载荷响应 Range 证据不精确：{oid}")


def 读取精确(stream: BinaryIO, size: int, *, clean_eof: bool = False) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if clean_eof and remaining == size:
                return None
            raise 审计阻断(f"TRR 帧被截断：需要{size}字节，实得{size - remaining}字节")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def 审计TRR(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    total = 0
    frame_count = 0
    natoms_value: int | None = None
    precision_value: int | None = None
    first_step: int | None = None
    last_step: int | None = None
    first_time: float | None = None
    last_time: float | None = None
    velocity_frames = 0
    force_frames = 0

    try:
        with bz2.BZ2File(path, "rb") as trajectory:

            def consume(size: int, *, return_bytes: bool = False) -> bytes | None:
                nonlocal total
                if size < 0 or total + size > 单轨迹最大解压字节:
                    raise 审计阻断(f"TRR 解压边界越界：{path.name}/{total + size}")
                if return_bytes:
                    payload = 读取精确(trajectory, size)
                    assert payload is not None
                    digest.update(payload)
                    total += size
                    return payload
                remaining = size
                while remaining:
                    chunk = trajectory.read(min(remaining, 块大小))
                    if not chunk:
                        raise 审计阻断(f"TRR 负载被截断：{path.name}/{size - remaining}/{size}")
                    digest.update(chunk)
                    total += len(chunk)
                    remaining -= len(chunk)
                return None

            while True:
                magic = trajectory.read(4)
                if not magic:
                    break
                if len(magic) != 4:
                    raise 审计阻断(f"TRR magic 被截断：{path.name}")
                digest.update(magic)
                total += 4
                if struct.unpack(">i", magic)[0] != 1993:
                    raise 审计阻断(f"TRR magic 漂移：{path.name}/{frame_count}")
                version_buflen = struct.unpack(">i", consume(4, return_bytes=True))[0]
                version_length = struct.unpack(">i", consume(4, return_bytes=True))[0]
                padded = (version_length + 3) // 4 * 4
                version = consume(padded, return_bytes=True)
                if (
                    version_buflen != 13
                    or version_length != 12
                    or version[:version_length] != b"GMX_trn_file"
                ):
                    raise 审计阻断(f"TRR 版本头漂移：{path.name}/{frame_count}")
                sizes = struct.unpack(">10i", consume(40, return_bytes=True))
                if any(size < 0 or size > 512_000_000 for size in sizes):
                    raise 审计阻断(f"TRR 块大小越界：{path.name}/{frame_count}")
                natoms, step, _nre = struct.unpack(">3i", consume(12, return_bytes=True))
                if natoms <= 0 or step < 0:
                    raise 审计阻断(f"TRR 原子数或步号非法：{path.name}/{frame_count}")
                box_size, x_size, velocity_size, force_size = sizes[2], sizes[7], sizes[8], sizes[9]
                candidates: set[int] = set()
                if box_size:
                    if box_size % 9:
                        raise 审计阻断(f"TRR box 块形状非法：{path.name}/{frame_count}")
                    candidates.add(box_size // 9)
                for block_size in (x_size, velocity_size, force_size):
                    if block_size:
                        denominator = natoms * 3
                        if block_size % denominator:
                            raise 审计阻断(f"TRR 原子向量块形状非法：{path.name}/{frame_count}")
                        candidates.add(block_size // denominator)
                if candidates not in ({4}, {8}) or x_size == 0:
                    raise 审计阻断(f"TRR 精度或坐标块非法：{path.name}/{frame_count}/{candidates}")
                precision = next(iter(candidates))
                real_format = ">f" if precision == 4 else ">d"
                time_ps = float(struct.unpack(real_format, consume(precision, return_bytes=True))[0])
                _lambda = struct.unpack(real_format, consume(precision, return_bytes=True))[0]
                if not math.isfinite(time_ps):
                    raise 审计阻断(f"TRR 时间非有限：{path.name}/{frame_count}")
                for block_size in sizes:
                    consume(block_size)

                if natoms_value is None:
                    natoms_value = natoms
                    precision_value = precision
                    first_step = step
                    first_time = time_ps
                elif natoms != natoms_value or precision != precision_value:
                    raise 审计阻断(f"TRR 原子数或精度在帧间漂移：{path.name}/{frame_count}")
                if last_step is not None and (step <= last_step or time_ps <= float(last_time)):
                    raise 审计阻断(f"TRR 步号或时间不严格递增：{path.name}/{frame_count}")
                last_step = step
                last_time = time_ps
                velocity_frames += int(velocity_size > 0)
                force_frames += int(force_size > 0)
                frame_count += 1
                if frame_count > 单轨迹最大帧数:
                    raise 审计阻断(f"TRR 帧数超过固定安全上限：{path.name}")
    except (EOFError, OSError) as exc:
        raise 审计阻断(f"BZip2/TRR 完整性校验失败：{path.name}") from exc
    if frame_count == 0 or None in {
        natoms_value,
        precision_value,
        first_step,
        last_step,
        first_time,
        last_time,
    }:
        raise 审计阻断(f"TRR 没有完整帧：{path.name}")
    return {
        "解压TRR字节": total,
        "解压TRR_SHA256": digest.hexdigest(),
        "帧数": frame_count,
        "原子数": natoms_value,
        "浮点精度字节": precision_value,
        "首步": first_step,
        "末步": last_step,
        "首时刻_ps": round(float(first_time), 6),
        "末时刻_ps": round(float(last_time), 6),
        "含速度帧数": velocity_frames,
        "含力帧数": force_frames,
    }


def 单一设置(mdp: str, name: str, mdp_path: str) -> str:
    values = re.findall(rf"^\s*{re.escape(name)}\s*=\s*([^;\s]+)", mdp, re.MULTILINE)
    if len(values) != 1:
        raise 审计阻断(f"mdout.mdp 设置缺失或重复：{mdp_path}/{name}={values}")
    return values[0]


def 审计协议(
    archive: zipfile.ZipFile,
    repo_path: str,
    trajectory: dict[str, object],
) -> dict[str, object]:
    archived_path = f"{归档根}/{repo_path}"
    parent = str(PurePosixPath(archived_path).parent)
    mdp_path = f"{parent}/mdout.mdp"
    log_path = f"{parent}/md.log"
    names = set(archive.namelist())
    if mdp_path not in names or log_path not in names:
        raise 审计阻断(f"指针轨迹缺配套 mdout.mdp 或 md.log：{repo_path}")
    mdp = archive.read(mdp_path).decode("latin-1")
    log = archive.read(log_path).decode("latin-1", errors="replace")
    dt_ps = float(单一设置(mdp, "dt", mdp_path))
    nsteps = int(单一设置(mdp, "nsteps", mdp_path))
    tau_t_ps = float(单一设置(mdp, "tau_t", mdp_path))
    ref_t_k = float(单一设置(mdp, "ref_t", mdp_path))
    finished = "Finished mdrun on rank 0" in log
    first_term_signal = "Received the TERM signal" in log
    second_int_term_signal = "Received the second INT/TERM signal" in log
    signal_count = int(first_term_signal) + int(second_int_term_signal)
    if int(finished) + signal_count != 1:
        raise 审计阻断(
            f"运行结束证据必须且只能为 Finished、TERM 或 second INT/TERM 之一：{repo_path}"
        )
    terminal_step: int | None = None
    terminal_signal: str | None = None
    if signal_count:
        matches = re.findall(
            r"Received the (?:TERM signal|second INT/TERM signal).*?"
            r"Step\s+Time\s+Lambda\s+(\d+)",
            log,
            re.DOTALL,
        )
        if len(matches) != 1:
            raise 审计阻断(f"TERM/INT 终止步无法唯一解析：{repo_path}")
        terminal_step = int(matches[0])
        terminal_signal = (
            "second_int_term_signal" if second_int_term_signal else "term_signal"
        )
    last_step = int(trajectory["末步"])
    last_time = float(trajectory["末时刻_ps"])
    if not math.isclose(last_time, last_step * dt_ps, rel_tol=0.0, abs_tol=0.01):
        raise 审计阻断(f"TRR 末时刻与 dt×step 不一致：{repo_path}")
    if finished and last_step != nsteps:
        raise 审计阻断(f"Finished 运行末帧不等于 nsteps：{repo_path}")
    if terminal_step is not None and last_step > terminal_step:
        raise 审计阻断(f"TRR 末帧晚于 TERM 记录：{repo_path}")
    if "tau0.5" in PurePosixPath(repo_path).parts and not (
        math.isclose(dt_ps, 0.0005) and math.isclose(tau_t_ps, 0.1)
    ):
        raise 审计阻断(f"tau0.5 目录的实际 dt/tau_t 语义漂移：{repo_path}")
    if not all(math.isfinite(value) and value > 0 for value in (dt_ps, tau_t_ps, ref_t_k)):
        raise 审计阻断(f"运行协议包含非正或非有限参数：{repo_path}")
    if finished:
        completion = "finished"
    elif second_int_term_signal:
        completion = "terminated_by_second_int_term_signal"
    elif last_step > nsteps:
        completion = "terminated_after_continuation_beyond_declared_nsteps"
    else:
        completion = "terminated_before_declared_nsteps"
    return {
        "mdout路径": mdp_path,
        "md日志路径": log_path,
        "dt_ps": dt_ps,
        "dt_fs": dt_ps * 1000,
        "声明步数": nsteps,
        "tau_t_ps": tau_t_ps,
        "参考温度_K": ref_t_k,
        "完成状态": completion,
        "日志终止信号": terminal_signal,
        "日志终止步": terminal_step,
        "目录语义说明": (
            "tau0.5目录实际为dt=0.5 fs、tau_t=0.1 ps"
            if "tau0.5" in PurePosixPath(repo_path).parts
            else (
                "无300K子目录但mdout明确ref_t=600 K"
                if math.isclose(ref_t_k, 600.0)
                else "温度与步长以mdout.mdp为准"
            )
        ),
    }


def 路径条件(repo_path: str) -> tuple[str, str, str]:
    parts = PurePosixPath(repo_path).parts
    chain = parts[0]
    environment = {"solvated": "水中", "vacuum": "真空"}.get(parts[1], parts[1])
    variant = parts[2] if len(parts) == 4 else "默认"
    return chain, environment, variant


def main() -> None:
    验证普通路径(补采目录, 补采目录)
    if not 清单路径.is_file():
        raise 审计阻断("缺少补采清单，请先运行独立补采脚本")
    if list(载荷目录.glob("*.part")):
        raise 审计阻断("仍存在未完成 .part，不能生成正式审计输出")
    manifest = 读取JSON(清单路径)
    if not isinstance(manifest, dict):
        raise 审计阻断("补采清单根不是对象")
    points, archive = 读取固定指针()
    try:
        核验来源快照(points, manifest)
        manifest_rows = manifest.get("对象")
        if not isinstance(manifest_rows, list):
            raise 审计阻断("补采清单对象表缺失")
        by_oid = {str(row.get("GitLFS_OID_SHA256")): row for row in manifest_rows}
        if len(by_oid) != 指针数量:
            raise 审计阻断("补采清单 OID 不唯一")

        file_rows: list[dict[str, object]] = []
        trajectory_rows: list[dict[str, object]] = []
        details: list[dict[str, object]] = []
        for index, point in enumerate(points, start=1):
            oid = str(point["OID"])
            row = by_oid.get(oid)
            if row is None:
                raise 审计阻断(f"补采清单缺少 OID：{oid}")
            if (
                row.get("归档路径") != point["归档路径"]
                or row.get("仓库路径") != point["仓库路径"]
                or int(row.get("GitLFS声明字节", -1)) != point["字节"]
                or row.get("指针Git对象SHA1") != point["Git对象SHA1"]
                or row.get("训练许可") is not False
            ):
                raise 审计阻断(f"补采清单对象来源字段漂移：{oid}")
            relative = PurePosixPath(str(row.get("本地相对路径", "")))
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "轨迹载荷":
                raise 审计阻断(f"本地相对路径越界：{oid}")
            local = 补采目录.joinpath(*relative.parts)
            验证普通路径(local, 补采目录)
            if not local.is_file() or local.stat().st_size != point["字节"]:
                raise 审计阻断(f"补采载荷缺失或字节漂移：{oid}")
            local_sha = 文件SHA256(local)
            if local_sha != oid or row.get("本地SHA256") != oid:
                raise 审计阻断(f"补采载荷 SHA256/OID 不一致：{oid}/{local_sha}")
            response_path = 快照目录 / f"载荷响应_{oid}.json"
            response = 读取JSON(response_path)
            核验载荷响应证据(response, point, row)

            print(f"[{index}/{len(points)}] 全帧审计 {point['仓库路径']}")
            trr = 审计TRR(local)
            protocol = 审计协议(archive, str(point["仓库路径"]), trr)
            chain, environment, variant = 路径条件(str(point["仓库路径"]))
            detail = {
                "仓库路径": point["仓库路径"],
                "OID": oid,
                "压缩字节": point["字节"],
                "本地文件": relative.as_posix(),
                "链长": chain,
                "环境": environment,
                "变体": variant,
                **trr,
                **protocol,
            }
            details.append(detail)
            file_rows.append(
                {
                    "序号": index,
                    "仓库路径": point["仓库路径"],
                    "本地文件": relative.as_posix(),
                    "声明字节": point["字节"],
                    "本地字节": local.stat().st_size,
                    "GitLFS_OID_SHA256": oid,
                    "本地SHA256": local_sha,
                    "指针Git对象SHA1": point["Git对象SHA1"],
                    "BZip2与TRR全帧": "通过",
                    "下载响应证据SHA256": 文件SHA256(response_path),
                    "许可状态": "上游无显式LICENSE；仅科研证据保留",
                }
            )
            trajectory_rows.append(
                {
                    "序号": index,
                    "运行家族": f"{chain}|{environment}|{variant}",
                    "链长": chain,
                    "环境": environment,
                    "变体": variant,
                    "帧数": trr["帧数"],
                    "原子数": trr["原子数"],
                    "首步": trr["首步"],
                    "末步": trr["末步"],
                    "首时刻_ps": trr["首时刻_ps"],
                    "末时刻_ps": trr["末时刻_ps"],
                    "dt_fs": protocol["dt_fs"],
                    "tau_t_ps": protocol["tau_t_ps"],
                    "参考温度_K": protocol["参考温度_K"],
                    "完成状态": protocol["完成状态"],
                    "含速度帧数": trr["含速度帧数"],
                    "含力帧数": trr["含力帧数"],
                    "解压TRR字节": trr["解压TRR字节"],
                    "解压TRR_SHA256": trr["解压TRR_SHA256"],
                    "相关性与独立单位": "同一TRR内全部帧强相关；一条轨迹至多是一个模拟运行家族",
                    "训练状态": "未准入；本审计不给权重",
                }
            )
    finally:
        archive.close()

    completion_counts = Counter(str(row["完成状态"]) for row in details)
    summary = {
        "审计版本": "pcl-git-lfs-payload-audit-v1.1",
        "来源": "PCL Git LFS 十轨迹补采",
        "来源锚点": {
            "请求固定提交": 请求固定提交,
            "请求结果": "PCL仓库内不存在；GitHub API 422 原始证据已固定",
            "实际归档提交": 归档对应提交,
            "实际归档树": 归档对应树,
            "Zenodo_DOI": "10.5281/zenodo.17790918",
            "Zenodo归档SHA256": 归档SHA256,
        },
        "对象数": len(details),
        "独立模拟运行家族上限": len(details),
        "压缩总字节": sum(int(row["压缩字节"]) for row in details),
        "解压TRR总字节": sum(int(row["解压TRR字节"]) for row in details),
        "总帧数": sum(int(row["帧数"]) for row in details),
        "完成状态计数": dict(sorted(completion_counts.items())),
        "参考温度_K集合": sorted({float(row["参考温度_K"]) for row in details}),
        "dt_fs集合": sorted({float(row["dt_fs"]) for row in details}),
        "tau_t_ps集合": sorted({float(row["tau_t_ps"]) for row in details}),
        "含速度轨迹数": sum(int(row["含速度帧数"]) > 0 for row in details),
        "含力轨迹数": sum(int(row["含力帧数"]) > 0 for row in details),
        "科学语义": (
            "十个 LFS 对象恢复了十条 GROMACS TRR 模拟载荷；帧是时间相关观测，"
            "不得把总帧数当作独立材料、独立配方或实验样本数。"
        ),
        "许可状态": (
            "GitHub 仓库 metadata license=null，固定 tree 未发现 LICENSE/COPYING；"
            "对象仅按可追溯科研证据保留，不声明再分发或训练许可。"
        ),
        "训练许可": False,
        "训练权重": None,
        "轨迹": details,
    }
    写TSV(
        补采目录 / "文件校验清单.tsv",
        file_rows,
        [
            "序号",
            "仓库路径",
            "本地文件",
            "声明字节",
            "本地字节",
            "GitLFS_OID_SHA256",
            "本地SHA256",
            "指针Git对象SHA1",
            "BZip2与TRR全帧",
            "下载响应证据SHA256",
            "许可状态",
        ],
    )
    写TSV(
        补采目录 / "轨迹审计清单.tsv",
        trajectory_rows,
        [
            "序号",
            "运行家族",
            "链长",
            "环境",
            "变体",
            "帧数",
            "原子数",
            "首步",
            "末步",
            "首时刻_ps",
            "末时刻_ps",
            "dt_fs",
            "tau_t_ps",
            "参考温度_K",
            "完成状态",
            "含速度帧数",
            "含力帧数",
            "解压TRR字节",
            "解压TRR_SHA256",
            "相关性与独立单位",
            "训练状态",
        ],
    )
    原子写入(
        补采目录 / "内容审计摘要.json",
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )
    print(
        f"审计完成：{len(details)}条轨迹，{summary['总帧数']:,}帧，"
        f"解压{summary['解压TRR总字节']:,}字节"
    )


if __name__ == "__main__":
    main()
