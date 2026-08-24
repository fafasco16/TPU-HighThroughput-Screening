"""从40条现实复核队列选择覆盖全部商业构件的高层DFT候选子集。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ROLE_COLUMNS = {
    "diisocyanate": "diisocyanate_id",
    "macrodiol": "macrodiol_id",
    "chain_extender": "chain_extender_id",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(frame: pd.DataFrame, columns: set[str]) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"高层DFT来源队列缺少字段: {missing}")


def _truth(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes"}


def _tokens(row: pd.Series) -> set[str]:
    return {f"{role}:{row[column]}" for role, column in ROLE_COLUMNS.items()}


def select_high_level_subset(queue: pd.DataFrame, *, target_size: int = 12) -> pd.DataFrame:
    _required(
        queue,
        {
            "review_queue_rank",
            "formulation_id",
            "base_system_id",
            "planning_tier",
            *ROLE_COLUMNS.values(),
            "hard_segment_mass_fraction_target",
            "pareto_is_nondominated",
            "screening_cluster_representative",
            "performance_claim_status",
        },
    )
    if target_size < 1:
        raise ValueError("target_size必须为正")
    if queue.empty or not queue["formulation_id"].is_unique:
        raise ValueError("高层DFT来源队列formulation_id必须非空唯一")
    if not queue["performance_claim_status"].astype(str).eq(
        "no_performance_claim"
    ).all():
        raise ValueError("高层DFT来源队列不得包含性能宣称")
    if target_size > len(queue):
        raise ValueError("target_size不能大于来源队列")

    working = queue.copy()
    working["review_queue_rank"] = pd.to_numeric(
        working["review_queue_rank"], errors="raise"
    ).astype(int)
    for column in ROLE_COLUMNS.values():
        if working[column].isna().any() or working[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"高层DFT来源队列{column}存在空值")
    universe = set().union(*(_tokens(row) for _, row in working.iterrows()))
    selected: list[str] = []
    reasons: dict[str, str] = {}
    covered: set[str] = set()
    indexed = working.set_index("formulation_id", drop=False)

    def add(formulation_id: str, reason: str) -> None:
        if formulation_id in selected:
            return
        selected.append(formulation_id)
        reasons[formulation_id] = reason
        covered.update(_tokens(indexed.loc[formulation_id]))

    controls = working.loc[
        working["planning_tier"].astype(str).eq("tier1_small_control_matrix")
    ].sort_values(["review_queue_rank", "formulation_id"], kind="stable")
    if len(controls) > target_size:
        raise ValueError("target_size小于必须保留的商业对照数量")
    for formulation_id in controls["formulation_id"].astype(str):
        add(formulation_id, "commercial_small_control_matrix")

    def ranking(row: pd.Series, *, coverage: bool) -> tuple[object, ...]:
        new_count = len(_tokens(row) - covered) if coverage else 0
        pareto = _truth(row["pareto_is_nondominated"])
        cluster = _truth(row["screening_cluster_representative"])
        return (
            -new_count,
            -int(pareto and cluster),
            -int(pareto),
            -int(cluster),
            int(row["review_queue_rank"]),
            str(row["formulation_id"]),
        )

    while covered != universe and len(selected) < target_size:
        candidates = working.loc[
            ~working["formulation_id"].astype(str).isin(selected)
        ]
        ordered = sorted(
            (row for _, row in candidates.iterrows()),
            key=lambda row: ranking(row, coverage=True),
        )
        if not ordered or len(_tokens(ordered[0]) - covered) == 0:
            break
        add(str(ordered[0]["formulation_id"]), "component_set_coverage")
    if covered != universe:
        missing = sorted(universe - covered)
        raise ValueError(
            f"target_size={target_size}无法覆盖全部现实构件: {missing}"
        )

    while len(selected) < target_size:
        candidates = working.loc[
            ~working["formulation_id"].astype(str).isin(selected)
        ]
        ordered = sorted(
            (row for _, row in candidates.iterrows()),
            key=lambda row: ranking(row, coverage=False),
        )
        if not ordered:
            break
        row = ordered[0]
        reason = (
            "pareto_diversity_fill"
            if _truth(row["pareto_is_nondominated"])
            else "deterministic_diversity_fill"
        )
        add(str(row["formulation_id"]), reason)
    if len(selected) != target_size:
        raise ValueError("高层DFT子集数量未达到target_size")

    output = indexed.loc[selected].copy().reset_index(drop=True)
    output.insert(0, "high_level_dft_rank", np.arange(1, len(output) + 1))
    output["high_level_selection_reason"] = output["formulation_id"].map(reasons)
    output["pre_reaction_xtb_status"] = "ready"
    output["dft_engine_status"] = "blocked_no_authorized_r2scan3c_engine"
    output["dft_protocol_status"] = "input_preparation_allowed_execution_blocked"
    output["performance_claim_status"] = "no_performance_claim"
    return output


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    source_path: Path,
    output_root: Path,
    *,
    release_id: str,
    target_size: int = 12,
) -> dict[str, Any]:
    if not source_path.is_file():
        raise ValueError(f"DFT/MD复核队列不存在: {source_path}")
    source = pd.read_csv(source_path)
    selected = select_high_level_subset(source, target_size=target_size)
    output_name = f"高层DFT候选{target_size}.csv"
    output_path = output_root / output_name
    _atomic_text(output_path, selected.to_csv(index=False, float_format="%.12g"))
    note = "\n".join(
        [
            "# 高层DFT执行门",
            "",
            f"当前发布从{len(source)}条现实DFT/MD复核队列中选择{len(selected)}条，覆盖全部商业构件。",
            "当前没有授权可执行的r2SCAN-3c程序，因此高层DFT仅允许准备输入，不允许把xTB结果改名为DFT。",
            "先运行GFN2-xTB预反应复合物多起点筛选（NCO–OH）；其缔合能只作代理，用于缩小需要正式反应路径/频率计算的体系。",
            "获得ORCA等合规程序后，必须固定版本、r2SCAN-3c协议、频率门、溶剂/熔融环境假设和输入输出SHA-256。",
            "全部结果继续保持no_performance_claim，不能解释为TPU强度、韧性或反应速率常数。",
            "",
        ]
    )
    note_path = output_root / "高层DFT执行门.md"
    _atomic_text(note_path, note)
    manifest = {
        "release_id": release_id,
        "status": "ready_input_preparation_dft_execution_blocked",
        "counts": {
            "source_queue": len(source),
            "selected_formulations": len(selected),
            "diisocyanates": int(selected["diisocyanate_id"].nunique()),
            "macrodiols": int(selected["macrodiol_id"].nunique()),
            "chain_extenders": int(selected["chain_extender_id"].nunique()),
        },
        "source": {
            "path": str(source_path),
            "bytes": source_path.stat().st_size,
            "sha256": sha256(source_path),
        },
        "files": {
            output_name: {
                "bytes": output_path.stat().st_size,
                "sha256": sha256(output_path),
            },
            note_path.name: {
                "bytes": note_path.stat().st_size,
                "sha256": sha256(note_path),
            },
        },
    }
    _atomic_text(
        output_root / "高层DFT子集发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--复核队列",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "DFT_MD复核队列.csv",
    )
    parser.add_argument(
        "--输出目录", type=Path, default=ROOT / "结果" / "现实筛选"
    )
    parser.add_argument("--数量", type=int, default=12)
    parser.add_argument(
        "--发布ID", default="tpu-reality-high-level-dft-subset-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.复核队列,
        args.输出目录,
        release_id=args.发布ID,
        target_size=args.数量,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
