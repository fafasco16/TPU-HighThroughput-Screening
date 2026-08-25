"""把低阶家族特异扭转修正候选映射到12条现实TPU低聚链。"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from rdkit import Chem

from 汇总RESP敏感性 import sha256
from 验证RESP核心转移 import classify_n_substituent


ROOT = Path(__file__).resolve().parents[1]
URETHANE_PATTERN = Chem.MolFromSmarts("OC(=O)N")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _atomic_gzip_csv(path: Path, frame: pd.DataFrame) -> None:
    payload = frame.to_csv(index=False, float_format="%.12g").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(gzip.compress(payload, mtime=0))
    temporary.replace(path)


def validate_coefficients(coefficients: pd.DataFrame) -> pd.DataFrame:
    required = {
        "validation_family",
        "periodicity",
        "coefficient_for_cos_nphi_minus_one_kcal_mol",
        "amber_candidate_magnitude_kcal_mol",
        "amber_candidate_phase_degrees",
        "fourier_order",
    }
    missing = sorted(required.difference(coefficients.columns))
    if missing:
        raise ValueError(f"现实链扭转候选系数缺字段: {missing}")
    expected = {"aliphatic_urethane", "aromatic_urethane"}
    if set(coefficients["validation_family"].astype(str)) != expected:
        raise ValueError("现实链扭转候选必须同时包含脂肪与芳香家族")
    if coefficients.duplicated(["validation_family", "periodicity"]).any():
        raise ValueError("现实链扭转候选家族/周期项重复")
    for family, subset in coefficients.groupby("validation_family"):
        order = int(subset["fourier_order"].iloc[0])
        periodicities = sorted(subset["periodicity"].astype(int))
        if not subset["fourier_order"].astype(int).eq(order).all():
            raise ValueError(f"现实链扭转候选阶数不一致: {family}")
        if periodicities != list(range(1, order + 1)) or order > 2:
            raise ValueError(f"现实链扭转候选周期项不闭合或超过二阶: {family}")
    return coefficients.copy()


def map_chain_torsions(
    formulation_id: str,
    canonical_smiles: str,
    coefficients: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    molecule = Chem.MolFromSmiles(canonical_smiles)
    if molecule is None:
        raise ValueError(f"现实低聚链SMILES无法解析: {formulation_id}")
    coefficients = validate_coefficients(coefficients)
    rows = []
    family_counts = {"aliphatic_urethane": 0, "aromatic_urethane": 0}
    torsion_keys: set[tuple[int, int, int, int]] = set()
    for occurrence_index, match in enumerate(
        molecule.GetSubstructMatches(URETHANE_PATTERN)
    ):
        alkoxy_oxygen, carbonyl_carbon, carbonyl_oxygen, nitrogen = match
        environment = classify_n_substituent(
            molecule, nitrogen, carbonyl_carbon
        )
        family = f"{environment}_urethane"
        external = [
            neighbor.GetIdx()
            for neighbor in molecule.GetAtomWithIdx(nitrogen).GetNeighbors()
            if neighbor.GetIdx() != carbonyl_carbon and neighbor.GetAtomicNum() > 1
        ]
        if len(external) != 1:
            raise ValueError(
                f"现实链氨基甲酸酯N外部重原子邻居不是1个: {formulation_id}"
            )
        torsion = (carbonyl_oxygen, carbonyl_carbon, nitrogen, external[0])
        if torsion in torsion_keys:
            raise ValueError(f"现实链目标扭转重复: {formulation_id}:{torsion}")
        torsion_keys.add(torsion)
        family_counts[family] += 1
        family_coefficients = coefficients.loc[
            coefficients["validation_family"].eq(family)
        ].sort_values("periodicity", kind="stable")
        if family_coefficients.empty:
            raise ValueError(f"现实链目标扭转缺家族系数: {family}")
        symbols = [molecule.GetAtomWithIdx(index).GetSymbol() for index in torsion]
        for coefficient in family_coefficients.to_dict(orient="records"):
            rows.append(
                {
                    "formulation_id": formulation_id,
                    "urethane_occurrence_index": occurrence_index,
                    "validation_family": family,
                    "torsion_atom_1_zero_based": torsion[0],
                    "torsion_atom_2_zero_based": torsion[1],
                    "torsion_atom_3_zero_based": torsion[2],
                    "torsion_atom_4_zero_based": torsion[3],
                    "torsion_atom_1_one_based": torsion[0] + 1,
                    "torsion_atom_2_one_based": torsion[1] + 1,
                    "torsion_atom_3_one_based": torsion[2] + 1,
                    "torsion_atom_4_one_based": torsion[3] + 1,
                    "torsion_elements": "-".join(symbols),
                    "periodicity": int(coefficient["periodicity"]),
                    "coefficient_for_cos_nphi_minus_one_kcal_mol": float(
                        coefficient[
                            "coefficient_for_cos_nphi_minus_one_kcal_mol"
                        ]
                    ),
                    "amber_candidate_magnitude_kcal_mol": float(
                        coefficient["amber_candidate_magnitude_kcal_mol"]
                    ),
                    "amber_candidate_phase_degrees": float(
                        coefficient["amber_candidate_phase_degrees"]
                    ),
                    "mapping_scope": "candidate_only_not_written_to_forcefield",
                }
            )
    occurrence_count = len(torsion_keys)
    mapping = pd.DataFrame(rows)
    summary = {
        "formulation_id": formulation_id,
        "urethane_occurrence_count": occurrence_count,
        "aliphatic_urethane_occurrences": family_counts["aliphatic_urethane"],
        "aromatic_urethane_occurrences": family_counts["aromatic_urethane"],
        "mapped_torsion_instance_count": occurrence_count,
        "coefficient_term_assignment_count": len(mapping),
        "mapping_status": "candidate_mapping_complete_external_validation_pending",
        "production_md_permission": "blocked_external_and_condensed_phase_validation",
    }
    return mapping, summary


def write_release(
    graph_path: Path,
    plan_path: Path,
    resp_summary_path: Path,
    coefficient_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (graph_path, plan_path, resp_summary_path, coefficient_path):
        if not path.is_file():
            raise ValueError(f"现实链扭转映射输入不存在: {path}")
    graphs = pd.read_csv(graph_path)
    plans = pd.read_csv(plan_path)
    resp = pd.read_csv(resp_summary_path)
    coefficients = validate_coefficients(pd.read_csv(coefficient_path))
    for frame, label in [(graphs, "低聚链"), (plans, "计量"), (resp, "RESP")]:
        if not frame["formulation_id"].is_unique:
            raise ValueError(f"现实链扭转映射{label}配方ID不唯一")
    merged = graphs.merge(
        plans[["formulation_id", "estimated_urethane_bond_count"]],
        on="formulation_id",
        how="inner",
        validate="one_to_one",
    ).merge(
        resp[
            [
                "formulation_id",
                "urethane_occurrence_count",
                "aliphatic_urethane_occurrences",
                "aromatic_urethane_occurrences",
            ]
        ].rename(
            columns={
                "urethane_occurrence_count": "resp_urethane_occurrence_count",
                "aliphatic_urethane_occurrences": "resp_aliphatic_occurrences",
                "aromatic_urethane_occurrences": "resp_aromatic_occurrences",
            }
        ),
        on="formulation_id",
        how="inner",
        validate="one_to_one",
    )
    if len(merged) != 12:
        raise ValueError(f"现实链扭转映射不是12条链: {len(merged)}")
    mapping_frames = []
    summary_rows = []
    for source in merged.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        mapping, summary = map_chain_torsions(
            str(source["formulation_id"]),
            str(source["canonical_smiles"]),
            coefficients,
        )
        if summary["urethane_occurrence_count"] != int(
            source["estimated_urethane_bond_count"]
        ):
            raise ValueError("扭转映射与整数计量氨基甲酸酯数不一致")
        if summary["urethane_occurrence_count"] != int(
            source["resp_urethane_occurrence_count"]
        ):
            raise ValueError("扭转映射与RESP核心氨基甲酸酯数不一致")
        if summary["aliphatic_urethane_occurrences"] != int(
            source["resp_aliphatic_occurrences"]
        ) or summary["aromatic_urethane_occurrences"] != int(
            source["resp_aromatic_occurrences"]
        ):
            raise ValueError("扭转映射与RESP核心家族分类不一致")
        mapping_frames.append(mapping)
        summary_rows.append(summary)
    mappings = pd.concat(mapping_frames, ignore_index=True).sort_values(
        ["formulation_id", "urethane_occurrence_index", "periodicity"],
        kind="stable",
    )
    summaries = pd.DataFrame(summary_rows).sort_values(
        "formulation_id", kind="stable"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    mapping_out = output_root / "现实链扭转候选映射.csv.gz"
    summary_out = output_root / "现实链扭转候选逐配方.csv"
    report_out = output_root / "现实链扭转候选映射说明.md"
    _atomic_gzip_csv(mapping_out, mappings)
    _atomic_text(summary_out, summaries.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 现实链氨基甲酸酯扭转候选映射",
                "",
                "逐条链匹配`OC(=O)N`，目标四原子固定为羰基O–羰基C–N–N外部重原子；依据N外部原子是否芳香选择脂肪或芳香家族。",
                "映射数同时与整数计量计划和RESP核心转移结果核对。候选系数只写入映射表，不修改GAFF2、LAMMPS data或任何生产参数文件。",
                "",
                "外部片段、完整链电荷、凝聚相商业对照和重复MD门全部通过前，生产MD继续阻断。",
                "",
            ]
        ),
    )
    files = [mapping_out, summary_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": "twelve_chain_torsion_candidate_mapping_completed_external_validation_pending",
        "counts": {
            "formulations": len(summaries),
            "urethane_torsion_instances": int(
                summaries["mapped_torsion_instance_count"].sum()
            ),
            "coefficient_term_assignments": len(mappings),
            "aliphatic_urethane_instances": int(
                summaries["aliphatic_urethane_occurrences"].sum()
            ),
            "aromatic_urethane_instances": int(
                summaries["aromatic_urethane_occurrences"].sum()
            ),
        },
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (graph_path, plan_path, resp_summary_path, coefficient_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "mapping_scope": "candidate_only_not_written_to_forcefield",
        "production_md_permission": "blocked_external_and_condensed_phase_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "现实链扭转候选映射发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--低聚链",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链化学图.csv.gz",
    )
    parser.add_argument(
        "--计量计划",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链计量计划.csv",
    )
    parser.add_argument(
        "--RESP汇总",
        type=Path,
        default=ROOT
        / "计算"
        / "现实MD"
        / "RESP核心转移"
        / "RESP核心转移逐配方.csv",
    )
    parser.add_argument(
        "--候选系数",
        type=Path,
        default=ROOT
        / "计算"
        / "现实MD"
        / "氨基甲酸酯松弛扭转修正"
        / "松弛扭转修正候选系数.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "现实链扭转候选映射",
    )
    parser.add_argument(
        "--发布ID",
        default="tpu-reality-md-chain-torsion-candidate-mapping-20260825-v1",
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.低聚链,
        args.计量计划,
        args.RESP汇总,
        args.候选系数,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
