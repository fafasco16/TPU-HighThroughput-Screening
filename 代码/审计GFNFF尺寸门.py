"""审计当前xTB 6.7.1二进制的GFN-FF低聚链尺寸烟雾结果。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
_TIME = re.compile(
    r"WALL=(?P<wall>[0-9.]+)\s+MAXRSS_KB=(?P<rss>\d+)\s+EXIT=(?P<exit>\d+)"
)
_CONVERGED = re.compile(r"GEOMETRY OPTIMIZATION CONVERGED", re.IGNORECASE)
_NORMAL = re.compile(r"normal termination of xtb", re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atom_count(path: Path) -> int:
    try:
        value = int(path.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, UnicodeError, IndexError, ValueError) as exc:
        raise ValueError(f"GFN-FF烟雾输入原子数无效: {path}") from exc
    if value <= 0:
        raise ValueError(f"GFN-FF烟雾输入原子数必须为正: {path}")
    return value


def audit_smoke_root(raw_root: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    cases = sorted(path for path in raw_root.iterdir() if path.is_dir())
    if not cases:
        raise ValueError("GFN-FF烟雾目录为空")
    rows: list[dict[str, Any]] = []
    aggregate_hash_inputs: list[str] = []
    for case in cases:
        input_path = case / "input.xyz"
        log_path = case / "xtb.out"
        time_path = case / "time.log"
        if not input_path.is_file() or not log_path.is_file() or not time_path.is_file():
            raise ValueError(f"GFN-FF烟雾案例缺少核心文件: {case.name}")
        input_hash = sha256(input_path)
        log_hash = sha256(log_path)
        time_hash = sha256(time_path)
        aggregate_hash_inputs.extend((input_hash, log_hash, time_hash))
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        time_text = time_path.read_text(encoding="utf-8", errors="replace")
        match = _TIME.search(time_text)
        if match is None:
            raise ValueError(f"GFN-FF烟雾time.log缺少资源终态: {case.name}")
        exit_code = int(match.group("exit"))
        converged = bool(
            _CONVERGED.search(log_text)
            and _NORMAL.search(f"{log_text}\n{time_text}")
        )
        sigsegv = "SIGSEGV" in time_text and exit_code == 174
        if converged and exit_code == 0:
            outcome = "converged"
        elif sigsegv:
            outcome = "sigsegv_neighbor_initialization"
        elif "FAILED TO CONVERGE" in log_text:
            outcome = "geometry_not_converged"
        else:
            outcome = "other_failure"
        output_path = case / "xtbopt.xyz"
        output_hash = sha256(output_path) if output_path.is_file() else ""
        if output_hash:
            aggregate_hash_inputs.append(output_hash)
        rows.append(
            {
                "case_id": case.name,
                "atom_count": _atom_count(input_path),
                "outcome": outcome,
                "exit_code": exit_code,
                "wall_seconds": float(match.group("wall")),
                "maxrss_kb": int(match.group("rss")),
                "input_xyz_sha256": input_hash,
                "stdout_sha256": log_hash,
                "time_log_sha256": time_hash,
                "optimized_xyz_sha256": output_hash,
                "xtb_version": "6.7.1",
                "xtb_binary_sha256": (
                    "debf27a9e0fa4bfb5ca75aafe4b90d8211f08ec2f4a482f375a4987212eaa12a"
                ),
                "method": "GFN-FF",
                "optimization_level": "normal",
            }
        )
    table = pd.DataFrame(rows).sort_values("atom_count").reset_index(drop=True)
    converged_atoms = table.loc[table["outcome"].eq("converged"), "atom_count"]
    sigsegv_atoms = table.loc[
        table["outcome"].eq("sigsegv_neighbor_initialization"), "atom_count"
    ]
    if converged_atoms.empty or sigsegv_atoms.empty:
        raise ValueError("GFN-FF尺寸门至少需要一个收敛和一个SIGSEGV案例")
    maximum_converged = int(converged_atoms.max())
    minimum_sigsegv = int(sigsegv_atoms.min())
    if maximum_converged >= minimum_sigsegv:
        raise ValueError("GFN-FF尺寸证据区间发生矛盾")
    gate = {
        "status": "empirical_binary_specific_gate",
        "xtb_version": "6.7.1",
        "xtb_binary_sha256": (
            "debf27a9e0fa4bfb5ca75aafe4b90d8211f08ec2f4a482f375a4987212eaa12a"
        ),
        "max_converged_atom_count": maximum_converged,
        "min_sigsegv_atom_count": minimum_sigsegv,
        "production_atom_limit": maximum_converged,
        "untested_interval": f"{maximum_converged + 1}-{minimum_sigsegv - 1}",
        "failure_location": "xtb_gfnff_neighbor_initialization",
        "interpretation_limit": (
            "environment- and binary-specific smoke evidence; not a universal GFN-FF limit"
        ),
        "raw_evidence_aggregate_sha256": hashlib.sha256(
            "".join(aggregate_hash_inputs).encode("ascii")
        ).hexdigest(),
    }
    return table, gate


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    raw_root: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    table, gate = audit_smoke_root(raw_root)
    table_path = output_root / "GFNFF尺寸烟雾审计.csv"
    gate_path = output_root / "GFNFF尺寸门.json"
    note_path = output_root / "GFNFF尺寸门说明.md"
    _atomic_text(table_path, table.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        gate_path,
        json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_text(
        note_path,
        "\n".join(
            [
                "# GFN-FF低聚链尺寸门说明",
                "",
                f"当前xTB 6.7.1二进制的收敛上界证据为{gate['max_converged_atom_count']}原子，最小SIGSEGV证据为{gate['min_sigsegv_atom_count']}原子。",
                f"{gate['untested_interval']}原子区间未测试，不外推为安全；生产任务只允许不超过{gate['production_atom_limit']}原子。",
                "SIGSEGV发生在GFN-FF邻居表初始化，失败任务没有xtbopt.xyz，不得自动重试或冒充收敛。",
                "该门只适用于清单记录的Linux二进制和当前环境，不是GFN-FF方法的普遍尺寸极限。",
                "大于生产门的链保留ETKDG/MMFF种子，并等待可验证的替代预优化/生产力场方案。",
                "",
            ]
        ),
    )
    manifest = {
        "release_id": release_id,
        "status": "completed",
        "counts": {
            "cases": len(table),
            "converged": int(table["outcome"].eq("converged").sum()),
            "sigsegv": int(
                table["outcome"].eq("sigsegv_neighbor_initialization").sum()
            ),
        },
        "raw_evidence_root": str(raw_root),
        "raw_evidence_aggregate_sha256": gate[
            "raw_evidence_aggregate_sha256"
        ],
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (table_path, gate_path, note_path)
        },
    }
    _atomic_text(
        output_root / "GFNFF尺寸门发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--原始烟雾目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "GFNFF预优化",
    )
    parser.add_argument(
        "--输出目录", type=Path, default=ROOT / "计算" / "现实MD"
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-gfnff-size-gate-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.原始烟雾目录,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
