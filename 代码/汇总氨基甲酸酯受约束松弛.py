"""核验脂肪/芳香冻结二面角松弛结果并连接对应刚性DFT点。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORIES = [
    ROOT / "计算" / "现实MD" / "松弛扫描_脂肪族氨基甲酸酯",
    ROOT / "计算" / "现实MD" / "松弛扫描_芳香族氨基甲酸酯",
]

POINT_KEY = [
    "fragment_name",
    "validation_family",
    "requested_angle_degrees",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _verify_directory(
    directory: Path,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    manifest_path = directory / "受约束松弛清单.json"
    if not manifest_path.is_file():
        raise ValueError(f"受约束松弛清单不存在: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") not in {
        "completed_constrained_relaxed_dft_points",
        "incomplete_constrained_relaxed_dft_points",
    }:
        raise ValueError(f"受约束松弛状态无效: {directory}")
    for name, record in manifest["files"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"受约束松弛文件哈希不闭合: {path}")
    table = pd.read_csv(directory / "relaxed_scan.csv")
    if len(table) != int(manifest["counts"]["planned"]):
        raise ValueError(f"受约束松弛计划行数不闭合: {directory}")
    record = {
        "fragment_name": manifest["fragment_name"],
        "manifest_path": str(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "manifest_sha256": sha256(manifest_path),
    }
    return manifest, table, record


def reconcile_relaxed_attempts(
    base: pd.DataFrame,
    retries: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """以重试结果替换且仅替换v1失败点，同时保留逐尝试审计。"""

    required = {
        *POINT_KEY,
        "point_status",
        "relaxed_dft_energy_hartree",
        "attempt_kind",
        "attempt_release_id",
        "optimizer_profile",
        "geom_maxiter",
    }
    missing_base = sorted(required.difference(base.columns))
    if missing_base:
        raise ValueError(f"基础DFT松弛表缺字段: {missing_base}")
    if base.duplicated(POINT_KEY).any():
        raise ValueError("基础DFT松弛点键重复")
    base = base.copy()
    retry_frame = (
        retries.copy()
        if retries is not None
        else pd.DataFrame(columns=base.columns)
    )
    if not retry_frame.empty:
        missing_retry = sorted(required.difference(retry_frame.columns))
        if missing_retry:
            raise ValueError(f"重试DFT松弛表缺字段: {missing_retry}")
        if retry_frame.duplicated(POINT_KEY).any():
            raise ValueError("重试DFT松弛点键重复")

    base_indexed = base.set_index(POINT_KEY, drop=False)
    retry_indexed = retry_frame.set_index(POINT_KEY, drop=False)
    unknown = retry_indexed.index.difference(base_indexed.index)
    if len(unknown):
        raise ValueError(f"重试点不属于基础计划: {list(unknown)}")
    if not retry_frame.empty:
        overwritten_completed = base_indexed.loc[
            retry_indexed.index, "point_status"
        ].eq("completed")
        if overwritten_completed.any():
            bad = list(overwritten_completed.index[overwritten_completed])
            raise ValueError(f"重试不得覆盖v1已完成点: {bad}")
        if not retry_frame["optimizer_profile"].eq("difficult_v2").all():
            raise ValueError("重试点必须声明difficult_v2优化策略")

    selected = base_indexed.copy()
    selected["base_point_status"] = selected["point_status"]
    selected["retry_point_status"] = pd.NA
    selected["selected_attempt"] = "base_v1"
    if not retry_frame.empty:
        for key, retry in retry_indexed.iterrows():
            for column in base.columns:
                if column in retry.index:
                    selected.loc[key, column] = retry[column]
            selected.loc[key, "retry_point_status"] = retry["point_status"]
            selected.loc[key, "selected_attempt"] = "retry_v2"
    selected = selected.reset_index(drop=True)
    selected["relaxed_dft_relative_energy_kcal_mol"] = pd.NA
    for _, indexes in selected.groupby("validation_family", sort=True).groups.items():
        indexes = list(indexes)
        completed = selected.loc[indexes, "point_status"].eq("completed")
        completed_indexes = list(pd.Index(indexes)[completed.to_numpy()])
        if completed_indexes:
            minimum = pd.to_numeric(
                selected.loc[completed_indexes, "relaxed_dft_energy_hartree"]
            ).min()
            selected.loc[
                completed_indexes, "relaxed_dft_relative_energy_kcal_mol"
            ] = (
                pd.to_numeric(
                    selected.loc[
                        completed_indexes, "relaxed_dft_energy_hartree"
                    ]
                )
                - minimum
            ) * 627.5094740631

    audit = pd.concat([base, retry_frame], ignore_index=True, sort=False)
    selected_keys = {
        (*key, attempt)
        for key, attempt in zip(
            selected[POINT_KEY].itertuples(index=False, name=None),
            selected["selected_attempt"],
        )
    }
    audit["selected_for_release"] = [
        (*key, attempt) in selected_keys
        for key, attempt in zip(
            audit[POINT_KEY].itertuples(index=False, name=None),
            audit["attempt_kind"],
        )
    ]
    return (
        selected.sort_values(POINT_KEY, kind="stable").reset_index(drop=True),
        audit.sort_values([*POINT_KEY, "attempt_kind"], kind="stable").reset_index(
            drop=True
        ),
    )


def join_rigid_relaxed(
    rigid: pd.DataFrame, relaxed: pd.DataFrame
) -> pd.DataFrame:
    rigid_columns = [
        "fragment_name",
        "validation_family",
        "requested_angle_degrees",
        "dft_relative_energy_kcal_mol",
        "gaff2_relative_energy_kcal_mol",
        "gaff2_minus_dft_relative_energy_kcal_mol",
    ]
    missing = sorted(set(rigid_columns).difference(rigid.columns))
    if missing:
        raise ValueError(f"刚性扫描表缺字段: {missing}")
    joined = relaxed.merge(
        rigid[rigid_columns],
        on=[
            "fragment_name",
            "validation_family",
            "requested_angle_degrees",
        ],
        how="left",
        validate="one_to_one",
    )
    if joined["dft_relative_energy_kcal_mol"].isna().any():
        raise ValueError("受约束松弛点未完全连接刚性曲线")
    completed = joined["point_status"].eq("completed")
    joined.loc[completed, "relaxation_change_from_rigid_dft_kcal_mol"] = (
        joined.loc[completed, "relaxed_dft_relative_energy_kcal_mol"]
        - joined.loc[completed, "dft_relative_energy_kcal_mol"]
    )
    return joined.sort_values(
        ["validation_family", "requested_angle_degrees"], kind="stable"
    )


def write_release(
    directories: Sequence[Path],
    rigid_curve_path: Path,
    output_root: Path,
    *,
    release_id: str,
    retry_directories: Sequence[Path] = (),
    raw_archive: Path | None = None,
) -> dict[str, Any]:
    if len(directories) != 2:
        raise ValueError("受约束松弛汇总必须包含脂肪和芳香两个模型")
    if not rigid_curve_path.is_file():
        raise ValueError(f"刚性扫描曲线不存在: {rigid_curve_path}")
    base_tables: list[pd.DataFrame] = []
    retry_tables: list[pd.DataFrame] = []
    manifests: list[dict[str, Any]] = []
    retry_manifests: list[dict[str, Any]] = []
    inputs: dict[str, dict[str, Any]] = {}
    for directory in directories:
        manifest, table, record = _verify_directory(directory)
        manifests.append(manifest)
        table = table.copy()
        table["attempt_kind"] = "base_v1"
        table["attempt_release_id"] = manifest["release_id"]
        table["optimizer_profile"] = manifest.get(
            "optimizer_profile", "standard_v1"
        )
        table["geom_maxiter"] = manifest["geom_maxiter"]
        base_tables.append(table)
        inputs[str(manifest["fragment_name"])] = record
    retry_inputs: dict[str, dict[str, Any]] = {}
    manifest_by_fragment = {
        str(manifest["fragment_name"]): manifest for manifest in manifests
    }
    for directory in retry_directories:
        manifest, table, record = _verify_directory(directory)
        retry_manifests.append(manifest)
        fragment = str(manifest["fragment_name"])
        if fragment not in manifest_by_fragment:
            raise ValueError(f"重试片段不属于基础计划: {fragment}")
        base_manifest = manifest_by_fragment[fragment]
        for field in (
            "fragment_name",
            "validation_family",
            "smiles",
            "method",
            "basis",
            "constraint",
        ):
            if manifest.get(field) != base_manifest.get(field):
                raise ValueError(f"重试协议字段与基础计划不一致: {field}")
        if manifest.get("optimizer_profile") != "difficult_v2":
            raise ValueError("重试清单未声明difficult_v2优化策略")
        table = table.copy()
        table["attempt_kind"] = "retry_v2"
        table["attempt_release_id"] = manifest["release_id"]
        table["optimizer_profile"] = manifest["optimizer_profile"]
        table["geom_maxiter"] = manifest["geom_maxiter"]
        retry_tables.append(table)
        retry_inputs[str(manifest["release_id"])] = record
    relaxed, attempt_audit = reconcile_relaxed_attempts(
        pd.concat(base_tables, ignore_index=True),
        pd.concat(retry_tables, ignore_index=True) if retry_tables else None,
    )
    joined = join_rigid_relaxed(pd.read_csv(rigid_curve_path), relaxed)
    summary_rows = []
    for manifest in manifests:
        fragment = str(manifest["fragment_name"])
        subset = joined.loc[joined["fragment_name"].eq(fragment)]
        completed = subset.loc[subset["point_status"].eq("completed")]
        summary_rows.append(
            {
                "fragment_name": fragment,
                "validation_family": manifest["validation_family"],
                "planned_points": len(subset),
                "completed_points": len(completed),
                "failed_points": len(subset) - len(completed),
                "maximum_angle_drift_degrees": (
                    float(completed["angle_drift_degrees"].max())
                    if not completed.empty
                    else pd.NA
                ),
                "maximum_absolute_relaxation_change_kcal_mol": (
                    float(
                        completed[
                            "relaxation_change_from_rigid_dft_kcal_mol"
                        ].abs().max()
                    )
                    if not completed.empty
                    else pd.NA
                ),
                "elapsed_seconds": float(manifest["elapsed_seconds"])
                + sum(
                    float(retry["elapsed_seconds"])
                    for retry in retry_manifests
                    if retry["fragment_name"] == fragment
                ),
                "relaxed_validation_status": (
                    "completed_relaxed_dft_subset_mm_refit_pending"
                    if len(completed) == len(subset)
                    else "incomplete_relaxed_dft_subset"
                ),
                "production_md_permission": "blocked_mm_relaxed_refit_and_external_validation",
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(
        "validation_family", kind="stable"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    joined_out = output_root / "松弛与刚性对比.csv"
    summary_out = output_root / "受约束松弛汇总.csv"
    report_out = output_root / "受约束松弛说明.md"
    audit_out = output_root / "重试审计.csv"
    archive_out = output_root / "原始归档定位.json"
    _atomic_text(joined_out, joined.to_csv(index=False, float_format="%.12g"))
    _atomic_text(summary_out, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        audit_out, attempt_audit.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 氨基甲酸酯冻结二面角松弛结果",
                "",
                "每个家族包含DFT刚性最低点、DFT刚性最高点、最大DFT–GAFF2误差点和|角度|=150°肩部，共8个信息互补点。",
                "Psi4/OptKing冻结O=C-N-R二面角并以ωB97M-D3BJ/6-31G(d,p)松弛其余自由度；逐点保留QCHEM收敛状态和最终角度漂移。",
                "",
                "相对能仅在各家族成功松弛点内归零。`relaxation_change_from_rigid_dft`用于判断刚性屏蔽结论对几何松弛是否敏感，不是GAFF2松弛误差；下一步仍需相同约束下的MM最小化和参数重拟合。",
                (
                    "v1失败点只允许由同方法、同基组、同冻结角定义的difficult_v2重试替换；"
                    "v1成功点禁止重算覆盖。所有尝试保存在重试审计表中。"
                    if retry_directories
                    else "失败点保持失败，不插值、不用刚性能量替代。"
                ),
                "",
            ]
        ),
    )
    if raw_archive is not None:
        if not raw_archive.is_file():
            raise ValueError(f"受约束松弛原始归档不存在: {raw_archive}")
        archive_record: dict[str, Any] | None = {
            "storage": "server_only_not_committed_to_git",
            "path": str(raw_archive),
            "bytes": raw_archive.stat().st_size,
            "sha256": sha256(raw_archive),
        }
        _atomic_text(
            archive_out,
            json.dumps(archive_record, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
    else:
        archive_record = None
    files = [
        joined_out,
        summary_out,
        report_out,
        audit_out,
        *([archive_out] if archive_record is not None else []),
    ]
    manifest = {
        "release_id": release_id,
        "status": (
            "constrained_relaxed_dft_subset_completed_mm_refit_pending"
            if summary["failed_points"].astype(int).sum() == 0
            else "constrained_relaxed_dft_subset_incomplete"
        ),
        "counts": {
            "fragments": len(summary),
            "planned_points": int(summary["planned_points"].sum()),
            "completed_points": int(summary["completed_points"].sum()),
            "failed_points": int(summary["failed_points"].sum()),
        },
        "maximum_angle_drift_degrees": (
            float(summary["maximum_angle_drift_degrees"].dropna().max())
            if summary["maximum_angle_drift_degrees"].notna().any()
            else None
        ),
        "maximum_absolute_relaxation_change_kcal_mol": (
            float(
                summary["maximum_absolute_relaxation_change_kcal_mol"]
                .dropna()
                .max()
            )
            if summary["maximum_absolute_relaxation_change_kcal_mol"]
            .notna()
            .any()
            else None
        ),
        "inputs": {
            "relaxed_manifests": inputs,
            "retry_manifests": retry_inputs,
            "rigid_curve": {
                "path": str(rigid_curve_path),
                "bytes": rigid_curve_path.stat().st_size,
                "sha256": sha256(rigid_curve_path),
            },
        },
        "raw_archive": archive_record,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_mm_relaxed_refit_and_external_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "受约束松弛发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--松弛目录", type=Path, action="append")
    parser.add_argument("--重试目录", type=Path, action="append", default=[])
    parser.add_argument("--刚性曲线", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--原始归档", type=Path)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-urethane-constrained-relax-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.松弛目录 or DEFAULT_DIRECTORIES,
        args.刚性曲线,
        args.输出目录,
        release_id=args.发布ID,
        retry_directories=args.重试目录,
        raw_archive=args.原始归档,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["counts"]["failed_points"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
