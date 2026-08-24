"""汇总四类片段的三构象共同电荷RESP，并与独立拟合敏感性矩阵比较。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import functional_core_indices, sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORIES = [
    ROOT / "计算" / "现实MD" / "RESP联合_脂肪族氨基甲酸酯",
    ROOT / "计算" / "现实MD" / "RESP联合_芳香族氨基甲酸酯",
    ROOT / "计算" / "现实MD" / "RESP联合_脂肪族异氰酸酯",
    ROOT / "计算" / "现实MD" / "RESP联合_芳香族异氰酸酯",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _verify_joint_directory(
    directory: Path,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    manifest_path = directory / "RESP联合清单.json"
    if not manifest_path.is_file():
        raise ValueError(f"联合RESP清单不存在: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed_joint_multiconformer_resp_fragment":
        raise ValueError(f"联合RESP未完成: {directory}")
    if int(manifest.get("conformer_count", 0)) != 3:
        raise ValueError(f"联合RESP不是三构象: {directory}")
    for name, record in manifest["files"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"联合RESP文件哈希不闭合: {path}")
    charges = pd.read_csv(directory / "joint_resp_charges.csv")
    if len(charges) != int(manifest["atom_count"]):
        raise ValueError(f"联合RESP原子数不闭合: {directory}")
    charge_sum = float(math.fsum(charges["joint_stage2_resp_charge_e"].tolist()))
    if abs(charge_sum - float(manifest["target_total_charge_e"])) > 1.0e-8:
        raise ValueError(f"联合RESP电荷和不闭合: {directory}")
    record = {
        "fragment_name": manifest["fragment_name"],
        "manifest_path": str(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "manifest_sha256": sha256(manifest_path),
    }
    return manifest, charges, record


def compare_joint_to_independent(
    joint: pd.DataFrame, independent: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {
        "fragment_name",
        "validation_family",
        "atom_index_zero_based",
        "element",
        "functional_core",
        "joint_stage2_resp_charge_e",
    }
    missing = sorted(required.difference(joint.columns))
    if missing:
        raise ValueError(f"联合RESP表缺字段: {missing}")
    independent_density1 = independent.loc[
        independent["vdw_point_density"].astype(float).eq(1.0)
    ]
    grouped = (
        independent_density1.groupby(
            ["fragment_name", "atom_index_zero_based", "element"], sort=True
        )["stage2_resp_charge_e"]
        .agg(independent_conformer_count="count", independent_mean_e="mean", independent_sample_std_e="std")
        .reset_index()
    )
    if not grouped["independent_conformer_count"].eq(3).all():
        raise ValueError("独立RESP点密度1.0不是每原子三个构象")
    merged = joint.merge(
        grouped,
        on=["fragment_name", "atom_index_zero_based", "element"],
        how="left",
        validate="one_to_one",
    )
    if merged["independent_mean_e"].isna().any():
        raise ValueError("联合RESP与独立敏感性矩阵未完全连接")
    merged["joint_minus_independent_mean_e"] = (
        merged["joint_stage2_resp_charge_e"] - merged["independent_mean_e"]
    )
    merged["absolute_joint_minus_independent_mean_e"] = merged[
        "joint_minus_independent_mean_e"
    ].abs()
    rows: list[dict[str, Any]] = []
    for (fragment_name, family), subset in merged.groupby(
        ["fragment_name", "validation_family"], sort=True
    ):
        core = subset.loc[subset["functional_core"]]
        rows.append(
            {
                "fragment_name": fragment_name,
                "validation_family": family,
                "atom_count": len(subset),
                "functional_core_atom_count": len(core),
                "maximum_absolute_joint_minus_independent_mean_e": float(
                    subset["absolute_joint_minus_independent_mean_e"].max()
                ),
                "maximum_core_absolute_joint_minus_independent_mean_e": float(
                    core["absolute_joint_minus_independent_mean_e"].max()
                ),
                "maximum_independent_conformer_sample_std_e": float(
                    subset["independent_sample_std_e"].max()
                ),
                "maximum_core_independent_conformer_sample_std_e": float(
                    core["independent_sample_std_e"].max()
                ),
                "production_md_permission": "blocked_fragment_to_polymer_transfer_and_forcefield_validation",
            }
        )
    return merged.sort_values(
        ["validation_family", "atom_index_zero_based"], kind="stable"
    ), pd.DataFrame(rows).sort_values("validation_family", kind="stable")


def write_release(
    directories: Sequence[Path],
    sensitivity_charge_path: Path,
    output_root: Path,
    *,
    release_id: str,
    raw_archive: Path | None = None,
) -> dict[str, Any]:
    if len(directories) != 4:
        raise ValueError("联合RESP必须恰好覆盖四个局部化学家族")
    if not sensitivity_charge_path.is_file():
        raise ValueError(f"RESP敏感性逐原子电荷不存在: {sensitivity_charge_path}")
    frames: list[pd.DataFrame] = []
    input_records: dict[str, dict[str, Any]] = {}
    manifests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in directories:
        manifest, charges, record = _verify_joint_directory(directory)
        fragment_name = str(manifest["fragment_name"])
        if fragment_name in seen:
            raise ValueError(f"联合RESP片段重复: {fragment_name}")
        seen.add(fragment_name)
        family = {
            "methyl_n_methyl_carbamate": "aliphatic_urethane",
            "methyl_n_phenyl_carbamate": "aromatic_urethane",
            "ethyl_isocyanate": "aliphatic_terminal_isocyanate",
            "phenyl_isocyanate": "aromatic_terminal_isocyanate",
        }.get(fragment_name)
        if family is None:
            raise ValueError(f"未知联合RESP片段: {fragment_name}")
        core_indices = functional_core_indices(manifest["smiles"], family)
        charges = charges.copy()
        charges.insert(
            0,
            "functional_core",
            charges["atom_index_zero_based"].astype(int).isin(core_indices),
        )
        charges.insert(0, "smiles", manifest["smiles"])
        charges.insert(0, "validation_family", family)
        charges.insert(0, "fragment_name", fragment_name)
        frames.append(charges)
        manifests.append(manifest)
        input_records[fragment_name] = record
    joint = pd.concat(frames, ignore_index=True)
    independent = pd.read_csv(sensitivity_charge_path)
    comparison, summary = compare_joint_to_independent(joint, independent)
    output_root.mkdir(parents=True, exist_ok=True)
    comparison_path = output_root / "联合RESP逐原子比较.csv"
    summary_path = output_root / "联合RESP片段汇总.csv"
    report_path = output_root / "联合RESP说明.md"
    archive_path = output_root / "原始归档定位.json"
    _atomic_text(
        comparison_path, comparison.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(summary_path, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_path,
        "\n".join(
            [
                "# 三构象联合RESP说明",
                "",
                "四个局部化学家族均以种子20260825/20260826/20260827生成三个ETKDG/MMFF构象，在点密度1.0下等权共同拟合同一组原子电荷。",
                "逐原子比较同时给出三个独立拟合电荷的均值、样本标准差，以及联合拟合与独立均值的差值。联合拟合不是把三套电荷简单平均。",
                "",
                "该结果已通过构象层面的共同电荷约束，但仍只覆盖四个小片段。下一步必须定义端基/重复单元等价原子，验证片段电荷转移到完整TPU链后的总电荷、局部偶极和构象稳定性；GAFF2 P0主链二面角验证仍独立阻断生产MD。",
                "",
            ]
        ),
    )
    if raw_archive is not None:
        if not raw_archive.is_file():
            raise ValueError(f"联合RESP原始归档不存在: {raw_archive}")
        archive_record: dict[str, Any] | None = {
            "storage": "server_only_not_committed_to_git",
            "path": str(raw_archive),
            "bytes": raw_archive.stat().st_size,
            "sha256": sha256(raw_archive),
        }
        _atomic_text(
            archive_path,
            json.dumps(archive_record, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
    else:
        archive_record = None
    files = [
        comparison_path,
        summary_path,
        report_path,
        *([archive_path] if archive_record is not None else []),
    ]
    raw_charge_errors = [
        abs(float(manifest["charge_metrics"]["stage2_resp_charge_sum_e"]))
        for manifest in manifests
    ]
    manifest = {
        "release_id": release_id,
        "status": "four_family_joint_multiconformer_resp_completed_transfer_pending",
        "counts": {
            "fragments": len(summary),
            "conformers_per_fragment": 3,
            "joint_atomic_charge_rows": len(comparison),
        },
        "maximum_raw_joint_charge_sum_error_e": max(raw_charge_errors),
        "maximum_core_absolute_joint_minus_independent_mean_e": float(
            summary["maximum_core_absolute_joint_minus_independent_mean_e"].max()
        ),
        "maximum_core_independent_conformer_sample_std_e": float(
            summary["maximum_core_independent_conformer_sample_std_e"].max()
        ),
        "inputs": {
            "joint_manifests": input_records,
            "sensitivity_charge_table": {
                "path": str(sensitivity_charge_path),
                "bytes": sensitivity_charge_path.stat().st_size,
                "sha256": sha256(sensitivity_charge_path),
            },
        },
        "raw_archive": archive_record,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_fragment_to_polymer_transfer_and_forcefield_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "联合RESP发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--联合目录", type=Path, action="append")
    parser.add_argument("--敏感性电荷", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--原始归档", type=Path)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-joint-resp-four-family-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.联合目录 or DEFAULT_DIRECTORIES,
        args.敏感性电荷,
        args.输出目录,
        release_id=args.发布ID,
        raw_archive=args.原始归档,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
