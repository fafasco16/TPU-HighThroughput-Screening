"""核验并汇总脂肪/芳香氨基甲酸酯与异氰酸酯RESP片段结果。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORIES = [
    ROOT / "计算" / "现实MD" / "RESP烟雾_小片段_尝试5",
    ROOT / "计算" / "现实MD" / "RESP片段_芳香族氨基甲酸酯",
    ROOT / "计算" / "现实MD" / "RESP片段_脂肪族异氰酸酯",
    ROOT / "计算" / "现实MD" / "RESP片段_芳香族异氰酸酯_尝试2",
]
FRAGMENT_FAMILIES = {
    "methyl_n_methyl_carbamate": "aliphatic_urethane",
    "methyl_n_phenyl_carbamate": "aromatic_urethane",
    "ethyl_isocyanate": "aliphatic_terminal_isocyanate",
    "phenyl_isocyanate": "aromatic_terminal_isocyanate",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def classify_fragment(fragment_name: str) -> str:
    if fragment_name not in FRAGMENT_FAMILIES:
        raise ValueError(f"未知RESP验证片段: {fragment_name}")
    return FRAGMENT_FAMILIES[fragment_name]


def verify_fragment_directory(directory: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    manifest_path = directory / "RESP烟雾清单.json"
    if not manifest_path.is_file():
        raise ValueError(f"缺少RESP烟雾清单: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed_native_two_stage_resp_smoke":
        raise ValueError(f"RESP片段未完成: {directory}")
    for name, record in manifest.get("files", {}).items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"RESP片段文件哈希不闭合: {path}")
    charges = pd.read_csv(directory / "resp_charges.csv")
    required = {
        "atom_index_zero_based",
        "element",
        "stage1_resp_charge_e",
        "stage2_resp_charge_e",
    }
    missing = sorted(required.difference(charges.columns))
    if missing or len(charges) != int(manifest["atom_count"]):
        raise ValueError(f"RESP逐原子电荷表不闭合: {directory}, missing={missing}")
    stage2_sum = float(math.fsum(charges["stage2_resp_charge_e"].tolist()))
    if abs(stage2_sum - float(manifest["target_total_charge_e"])) > 1.0e-8:
        raise ValueError(f"RESP逐原子电荷和不闭合: {directory}")
    return manifest, charges


def write_release(
    directories: Sequence[Path], output_root: Path, *, release_id: str
) -> dict[str, Any]:
    if len(directories) < 4:
        raise ValueError("片段覆盖至少需要脂肪/芳香氨基甲酸酯和异氰酸酯四类")
    summary_rows: list[dict[str, Any]] = []
    charge_frames: list[pd.DataFrame] = []
    inputs: dict[str, dict[str, Any]] = {}
    seen_families: set[str] = set()
    for directory in directories:
        manifest, charges = verify_fragment_directory(directory)
        fragment_name = str(manifest["fragment_name"])
        family = classify_fragment(fragment_name)
        if family in seen_families:
            raise ValueError(f"RESP片段家族重复: {family}")
        seen_families.add(family)
        metrics = manifest["charge_metrics"]
        summary_rows.append(
            {
                "fragment_name": fragment_name,
                "validation_family": family,
                "smiles": manifest["smiles"],
                "atom_count": int(manifest["atom_count"]),
                "method": manifest["method"],
                "basis": manifest["basis"],
                "vdw_point_density": float(manifest["vdw_point_density"]),
                "stage2_resp_charge_sum_e": float(
                    metrics["stage2_resp_charge_sum_e"]
                ),
                "stage1_to_stage2_resp_rms_e": float(
                    metrics["stage1_to_stage2_resp_rms_e"]
                ),
                "stage2_resp_min_e": float(metrics["stage2_resp_min_e"]),
                "stage2_resp_max_e": float(metrics["stage2_resp_max_e"]),
                "resp_status": manifest["status"],
                "transfer_status": "single_conformer_fragment_only",
                "production_md_permission": "blocked_multiconformer_transfer_validation",
            }
        )
        charges = charges.copy()
        charges.insert(0, "validation_family", family)
        charges.insert(0, "fragment_name", fragment_name)
        charge_frames.append(charges)
        manifest_path = directory / "RESP烟雾清单.json"
        inputs[fragment_name] = {
            "directory": str(directory),
            "manifest_bytes": manifest_path.stat().st_size,
            "manifest_sha256": sha256(manifest_path),
        }

    required_families = set(FRAGMENT_FAMILIES.values())
    if seen_families != required_families:
        raise ValueError(f"RESP片段家族覆盖不闭合: {sorted(seen_families)}")
    summary = pd.DataFrame(summary_rows).sort_values(
        "validation_family", kind="stable"
    )
    charges = pd.concat(charge_frames, ignore_index=True).sort_values(
        ["validation_family", "atom_index_zero_based"], kind="stable"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    summary_path = output_root / "RESP片段汇总.csv"
    charges_path = output_root / "RESP片段逐原子电荷.csv"
    report_path = output_root / "RESP片段验证说明.md"
    _atomic_text(summary_path, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(charges_path, charges.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_path,
        "\n".join(
            [
                "# RESP片段验证说明",
                "",
                "四个模型分别覆盖脂肪族/芳香族氨基甲酸酯主链和脂肪族/芳香族残余异氰酸酯端基。",
                "全部采用HF/6-31G(d)、VDW缩放1.4/1.6/1.8/2.0、点密度1.0和两阶段RESP，逐原子电荷和均闭合。",
                "芳香族异氰酸酯初次拟合出现奇异矩阵；发布结果使用带条件数检查的最小二乘回退，失败尝试留在服务器，不改写为成功。",
                "",
                "当前仅覆盖每个片段一个MMFF构象。下一门是多构象/多取向联合拟合、点密度敏感性和片段到完整TPU链的等价原子约束；通过前不得用于生产MD。",
                "",
            ]
        ),
    )
    files = [summary_path, charges_path, report_path]
    manifest = {
        "release_id": release_id,
        "status": "four_fragment_resp_coverage_completed_transfer_validation_pending",
        "counts": {
            "fragments": len(summary),
            "validation_families": len(seen_families),
            "atoms": len(charges),
            "completed": int(
                summary["resp_status"]
                .eq("completed_native_two_stage_resp_smoke")
                .sum()
            ),
        },
        "maximum_absolute_stage2_charge_sum_error_e": float(
            summary["stage2_resp_charge_sum_e"].abs().max()
        ),
        "inputs": inputs,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_multiconformer_transfer_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "RESP片段发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--片段目录", action="append", type=Path)
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "RESP片段验证",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-resp-four-fragment-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.片段目录 or DEFAULT_DIRECTORIES,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
