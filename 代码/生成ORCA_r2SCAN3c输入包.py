"""由合格预反应配对生成ORCA 6.1 r2SCAN-3c优化/频率输入包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_XTB_OUTPUTS = ("xtbopt.xyz", "xtbout.json", "xtb.out", "wbo")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def _truth(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _safe_path(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ValueError(f"{label}必须为相对路径")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved_root not in resolved.parents or not resolved.is_file():
        raise ValueError(f"{label}不存在或越出结果根目录")
    return resolved


def _best_geometry(
    pair: pd.Series,
    task_results: pd.DataFrame,
    result_root: Path,
) -> tuple[Path, str]:
    slug = str(pair["best_task_slug"])
    matches = task_results.loc[task_results["task_slug"].astype(str).eq(slug)]
    if len(matches) != 1:
        raise ValueError(f"{pair['pair_id']}最佳任务无法唯一回连")
    task = matches.iloc[0]
    if str(task["pair_id"]) != str(pair["pair_id"]) or str(task["run_status"]) != "completed":
        raise ValueError(f"{pair['pair_id']}最佳任务身份或状态不闭合")
    state_path = _safe_path(result_root, str(task["state_file"]), "最佳任务状态")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{pair['pair_id']}最佳任务状态JSON无效") from exc
    if (
        state.get("status") != "completed"
        or state.get("task_slug") != slug
        or state.get("pair_id") != pair["pair_id"]
    ):
        raise ValueError(f"{pair['pair_id']}最佳任务状态身份不一致")
    attempt_relative = state.get("attempt_directory")
    output_hashes = state.get("output_sha256")
    if not isinstance(attempt_relative, str) or not isinstance(output_hashes, dict):
        raise ValueError(f"{pair['pair_id']}最佳任务缺少输出身份")
    attempt = Path(attempt_relative)
    if attempt.is_absolute():
        raise ValueError(f"{pair['pair_id']}尝试目录必须为相对路径")
    attempt_root = (result_root / attempt).resolve()
    if result_root.resolve() not in attempt_root.parents:
        raise ValueError(f"{pair['pair_id']}尝试目录越出结果根目录")
    if set(output_hashes) != set(REQUIRED_XTB_OUTPUTS):
        raise ValueError(f"{pair['pair_id']}最佳任务输出集合不完整")
    for name in REQUIRED_XTB_OUTPUTS:
        path = attempt_root / name
        if not path.is_file() or sha256(path) != str(output_hashes[name]):
            raise ValueError(f"{pair['pair_id']}最佳任务{name} SHA-256不一致")
    return attempt_root / "xtbopt.xyz", str(output_hashes["xtbopt.xyz"])


def _atom_count(path: Path) -> int:
    try:
        value = int(path.read_text(encoding="utf-8").splitlines()[0].strip())
    except (OSError, UnicodeError, IndexError, ValueError) as exc:
        raise ValueError(f"ORCA初始几何原子数无效: {path}") from exc
    if value <= 0:
        raise ValueError(f"ORCA初始几何原子数必须为正: {path}")
    return value


def _orca_input(
    *,
    run_type: str,
    xyz_relative: str,
    nprocs: int,
    maxcore_mb: int,
) -> str:
    if run_type not in {"Opt", "Freq"}:
        raise ValueError("ORCA运行类型必须是Opt或Freq")
    return "\n".join(
        [
            f"! R2SCAN-3C TightSCF {run_type}",
            "",
            "%pal",
            f"  NProcs {nprocs}",
            "end",
            "",
            f"%maxcore {maxcore_mb}",
            "",
            "%scf",
            "  MaxIter 500",
            "end",
            "",
            f"* xyzfile 0 1 {xyz_relative}",
            "",
        ]
    )


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def build_input_package(
    pair_results: pd.DataFrame,
    task_results: pd.DataFrame,
    result_root: Path,
    output_root: Path,
    *,
    release_id: str,
    nprocs: int = 8,
    maxcore_mb: int = 6000,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if nprocs < 1:
        raise ValueError("ORCA NProcs必须为正")
    if maxcore_mb < 1000:
        raise ValueError("ORCA maxcore至少为1000 MB")
    _required(
        pair_results,
        {
            "pair_id",
            "pair_type",
            "diisocyanate_id",
            "oh_component_id",
            "pair_status",
            "pair_release_eligible",
            "best_task_slug",
        },
        "预反应逐配对结果",
    )
    _required(
        task_results,
        {"task_slug", "pair_id", "run_status", "state_file"},
        "预反应逐任务结果",
    )
    if not pair_results["pair_id"].is_unique or not task_results["task_slug"].is_unique:
        raise ValueError("预反应配对或任务身份不唯一")
    rows: list[dict[str, Any]] = []
    artifact_hashes: list[str] = []
    for pair in pair_results.sort_values("pair_id", kind="stable").to_dict(
        orient="records"
    ):
        base = {
            "pair_id": pair["pair_id"],
            "pair_type": pair["pair_type"],
            "diisocyanate_id": pair["diisocyanate_id"],
            "oh_component_id": pair["oh_component_id"],
            "best_task_slug": pair["best_task_slug"],
            "orca_version_target": "6.1",
            "method": "R2SCAN-3C",
            "charge": 0,
            "multiplicity": 1,
            "environment_model": "gas_phase",
            "nprocs": nprocs,
            "maxcore_mb_per_process": maxcore_mb,
            "execution_permission": "blocked_missing_authorized_executable",
            "orca_executable_sha256": "",
            "performance_claim_status": "no_performance_claim",
        }
        if not _truth(pair["pair_release_eligible"]):
            rows.append(
                {
                    **base,
                    "input_generation_status": "blocked_ineligible_pair",
                    "initial_geometry_file": "",
                    "initial_geometry_sha256": "",
                    "atom_count": pd.NA,
                    "optimization_input_file": "",
                    "optimization_input_sha256": "",
                    "frequency_input_file": "",
                    "frequency_input_sha256": "",
                    "frequency_dependency_status": "blocked_ineligible_pair",
                }
            )
            continue
        pair_series = pd.Series(pair)
        source_geometry, source_hash = _best_geometry(
            pair_series, task_results, result_root
        )
        geometry_relative = Path("几何") / f"{pair['pair_id']}.xyz"
        geometry_path = output_root / geometry_relative
        geometry_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = geometry_path.with_name(geometry_path.name + ".tmp")
        shutil.copyfile(source_geometry, temporary)
        temporary.replace(geometry_path)
        if sha256(geometry_path) != source_hash:
            raise ValueError(f"{pair['pair_id']}复制ORCA几何后SHA-256变化")
        opt_relative = Path("优化输入") / f"{pair['pair_id']}.inp"
        freq_relative = Path("频率输入") / f"{pair['pair_id']}.inp"
        opt_path = output_root / opt_relative
        freq_path = output_root / freq_relative
        _atomic_text(
            opt_path,
            _orca_input(
                run_type="Opt",
                xyz_relative=f"../{geometry_relative.as_posix()}",
                nprocs=nprocs,
                maxcore_mb=maxcore_mb,
            ),
        )
        _atomic_text(
            freq_path,
            _orca_input(
                run_type="Freq",
                xyz_relative=f"../ORCA优化几何/{pair['pair_id']}.xyz",
                nprocs=nprocs,
                maxcore_mb=maxcore_mb,
            ),
        )
        geometry_hash = sha256(geometry_path)
        opt_hash = sha256(opt_path)
        freq_hash = sha256(freq_path)
        artifact_hashes.extend((geometry_hash, opt_hash, freq_hash))
        rows.append(
            {
                **base,
                "input_generation_status": "generated_execution_blocked",
                "initial_geometry_file": geometry_relative.as_posix(),
                "initial_geometry_sha256": geometry_hash,
                "atom_count": _atom_count(geometry_path),
                "optimization_input_file": opt_relative.as_posix(),
                "optimization_input_sha256": opt_hash,
                "frequency_input_file": freq_relative.as_posix(),
                "frequency_input_sha256": freq_hash,
                "frequency_dependency_status": (
                    "requires_successful_ORCA_optimization_and_resource_review"
                ),
            }
        )
    table = pd.DataFrame(rows)
    table_path = output_root / "ORCA_r2SCAN3c任务清单.csv"
    _atomic_text(table_path, table.to_csv(index=False))
    note_path = output_root / "执行说明.md"
    _atomic_text(
        note_path,
        "\n".join(
            [
                "# ORCA r2SCAN-3c执行说明",
                "",
                "输入语法按ORCA 6.1官方手册生成：`R2SCAN-3C`、`%pal NProcs`、`Opt`和依赖优化几何的`Freq`。",
                "当前服务器没有授权ORCA可执行文件，全部任务保持执行阻断；不得使用xTB结果替代DFT输出。",
                "频率任务只有在同一pair的ORCA优化正常结束并回填`ORCA优化几何/<pair_id>.xyz`后才允许启动。",
                "正式执行前必须记录ORCA版本、可执行文件SHA-256、MPI版本、分区、核数、内存和输出哈希。",
                "本输入包只用于预反应复合物的高层结构/频率复核，不是过渡态或反应能垒计算。",
                "",
                "官方手册：https://www.faccts.de/docs/orca/6.1/manual/contents/modelchemistries/3cmethods.html",
                "并行说明：https://www.faccts.de/docs/orca/6.1/manual/contents/essentialelements/parallel.html",
                "",
            ]
        ),
    )
    manifest = {
        "release_id": release_id,
        "status": "ready_inputs_execution_blocked",
        "counts": {
            "pairs": len(table),
            "generated_optimization_inputs": int(
                table["input_generation_status"].eq(
                    "generated_execution_blocked"
                ).sum()
            ),
            "blocked_pairs": int(
                table["input_generation_status"].ne(
                    "generated_execution_blocked"
                ).sum()
            ),
        },
        "orca_version_target": "6.1",
        "method": "R2SCAN-3C",
        "execution_permission": "blocked_missing_authorized_executable",
        "task_table": {
            "path": table_path.name,
            "bytes": table_path.stat().st_size,
            "sha256": sha256(table_path),
        },
        "execution_note": {
            "path": note_path.name,
            "bytes": note_path.stat().st_size,
            "sha256": sha256(note_path),
        },
        "aggregate_artifact_sha256": hashlib.sha256(
            "".join(artifact_hashes).encode("ascii")
        ).hexdigest(),
    }
    _atomic_text(
        output_root / "ORCA输入发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return table, manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--配对结果", type=Path, required=True)
    parser.add_argument("--任务结果", type=Path, required=True)
    parser.add_argument("--结果根目录", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--进程数", type=int, default=8)
    parser.add_argument("--单进程内存MB", type=int, default=6000)
    parser.add_argument(
        "--发布ID", default="tpu-reality-orca-r2scan3c-inputs-20260825-v1"
    )
    args = parser.parse_args(argv)
    _, manifest = build_input_package(
        pd.read_csv(args.配对结果),
        pd.read_csv(args.任务结果),
        args.结果根目录.resolve(),
        args.输出目录.resolve(),
        release_id=args.发布ID,
        nprocs=args.进程数,
        maxcore_mb=args.单进程内存MB,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
