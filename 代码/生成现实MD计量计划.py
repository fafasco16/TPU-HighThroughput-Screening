"""为现实高层候选生成满足目标硬段分数和NCO/OH的整数低聚链计量计划。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors


ROOT = Path(__file__).resolve().parents[1]
MAX_HARD_SEGMENT_ERROR = 0.015


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


def _molecule_metrics(smiles: str, label: str) -> tuple[float, int]:
    molecule = Chem.MolFromSmiles(str(smiles))
    if molecule is None:
        raise ValueError(f"{label}结构无法解析")
    return float(Descriptors.MolWt(molecule)), int(Chem.AddHs(molecule).GetNumAtoms())


def _component_maps(
    components: pd.DataFrame, macro_models: pd.DataFrame
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    _required(
        components,
        {"component_id", "role", "canonical_smiles"},
        "现实构件",
    )
    _required(
        macro_models,
        {
            "component_id",
            "nominal_mn_g_mol",
            "representative_smiles",
            "approximation_status",
        },
        "PTMG代表模型",
    )
    if not components["component_id"].is_unique or not macro_models[
        "component_id"
    ].is_unique:
        raise ValueError("现实构件或PTMG模型ID不唯一")
    component_map = {
        str(row["component_id"]): row for row in components.to_dict(orient="records")
    }
    macro_map = {
        str(row["component_id"]): row for row in macro_models.to_dict(orient="records")
    }
    return component_map, macro_map


def _integer_plan(
    *,
    target_hard_fraction: float,
    macro_mn: float,
    diisocyanate_mw: float,
    extender_mw: float,
) -> dict[str, Any]:
    if not 0.1 <= target_hard_fraction <= 0.8:
        raise ValueError("目标硬段质量分数超出0.1–0.8")
    if min(macro_mn, diisocyanate_mw, extender_mw) <= 0:
        raise ValueError("构件分子量必须为正")
    candidates: list[tuple[float, int, int, int, dict[str, Any]]] = []
    for macro_count in range(1, 5):
        for extender_count in range(1, 21):
            diisocyanate_count = macro_count + extender_count
            hard_mass = (
                diisocyanate_count * diisocyanate_mw
                + extender_count * extender_mw
            )
            soft_mass = macro_count * macro_mn
            realized = hard_mass / (hard_mass + soft_mass)
            error = abs(realized - target_hard_fraction)
            total_units = macro_count + extender_count + diisocyanate_count
            plan = {
                "macrodiol_count": macro_count,
                "chain_extender_count": extender_count,
                "diisocyanate_count": diisocyanate_count,
                "realized_hard_segment_mass_fraction": realized,
                "hard_segment_fraction_abs_error": error,
                "estimated_nominal_chain_mass_g_mol": hard_mass + soft_mass,
                "realized_nco_oh_ratio": diisocyanate_count
                / (macro_count + extender_count),
                "estimated_urethane_bond_count": 2 * diisocyanate_count - 1,
                "residual_nco_end_groups": 1,
                "residual_oh_end_groups": 1,
            }
            candidates.append(
                (error, total_units, macro_count, extender_count, plan)
            )
    within_tolerance = [
        candidate
        for candidate in candidates
        if candidate[0] <= MAX_HARD_SEGMENT_ERROR
    ]
    if within_tolerance:
        return min(
            within_tolerance,
            key=lambda value: (value[1], value[0], value[2], value[3]),
        )[4]
    return min(
        candidates,
        key=lambda value: (value[0], value[1], value[2], value[3]),
    )[4]


def build_md_stoichiometry_plan(
    formulations: pd.DataFrame,
    components: pd.DataFrame,
    macro_models: pd.DataFrame,
) -> pd.DataFrame:
    _required(
        formulations,
        {
            "formulation_id",
            "diisocyanate_id",
            "macrodiol_id",
            "chain_extender_id",
            "macrodiol_nominal_mn_g_mol",
            "hard_segment_mass_fraction_target",
            "nco_oh_ratio_target",
            "performance_claim_status",
        },
        "现实高层候选",
    )
    if formulations.empty or not formulations["formulation_id"].is_unique:
        raise ValueError("现实高层候选formulation_id必须非空唯一")
    if not formulations["performance_claim_status"].astype(str).eq(
        "no_performance_claim"
    ).all():
        raise ValueError("MD计量输入不得包含性能宣称")
    ratios = pd.to_numeric(formulations["nco_oh_ratio_target"], errors="coerce")
    if ratios.isna().any() or (~ratios.isin([1.0, 1.02])).any():
        raise ValueError("当前单链计量生成器只接受NCO/OH=1.00或1.02")
    component_map, macro_map = _component_maps(components, macro_models)
    rows: list[dict[str, Any]] = []
    for source in formulations.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        di_id = str(source["diisocyanate_id"])
        macro_id = str(source["macrodiol_id"])
        extender_id = str(source["chain_extender_id"])
        for component_id, expected_role in (
            (di_id, "diisocyanate"),
            (macro_id, "macrodiol"),
            (extender_id, "chain_extender"),
        ):
            record = component_map.get(component_id)
            if record is None or str(record["role"]) != expected_role:
                raise ValueError(
                    f"构件{component_id}缺失或角色不是{expected_role}"
                )
        macro_model = macro_map.get(macro_id)
        if macro_model is None:
            raise ValueError(f"宏二醇{macro_id}缺少代表模型")
        if str(macro_model["approximation_status"]) != (
            "single_oligomer_proxy_for_product_distribution"
        ):
            raise ValueError(f"宏二醇{macro_id}代理范围不合格")
        macro_mn = float(source["macrodiol_nominal_mn_g_mol"])
        if abs(macro_mn - float(macro_model["nominal_mn_g_mol"])) > 1e-8:
            raise ValueError(f"宏二醇{macro_id}名义Mn不一致")
        di_mw, di_atoms = _molecule_metrics(
            component_map[di_id]["canonical_smiles"], di_id
        )
        extender_mw, extender_atoms = _molecule_metrics(
            component_map[extender_id]["canonical_smiles"], extender_id
        )
        _, macro_atoms = _molecule_metrics(
            macro_model["representative_smiles"], macro_id
        )
        plan = _integer_plan(
            target_hard_fraction=float(
                source["hard_segment_mass_fraction_target"]
            ),
            macro_mn=macro_mn,
            diisocyanate_mw=di_mw,
            extender_mw=extender_mw,
        )
        plan["estimated_atom_count"] = (
            plan["diisocyanate_count"] * di_atoms
            + plan["chain_extender_count"] * extender_atoms
            + plan["macrodiol_count"] * macro_atoms
        )
        rows.append(
            {
                "formulation_id": source["formulation_id"],
                "diisocyanate_id": di_id,
                "macrodiol_id": macro_id,
                "chain_extender_id": extender_id,
                "hard_segment_mass_fraction_target": float(
                    source["hard_segment_mass_fraction_target"]
                ),
                "nco_oh_ratio_target": float(source["nco_oh_ratio_target"]),
                "single_chain_backbone_nco_oh_ratio": 1.0,
                "nco_excess_fraction_batch_context": float(
                    source["nco_oh_ratio_target"]
                )
                - 1.0,
                "macrodiol_nominal_mn_g_mol": macro_mn,
                "diisocyanate_mol_weight_g_mol": di_mw,
                "chain_extender_mol_weight_g_mol": extender_mw,
                **plan,
                "hard_segment_definition": (
                    "(diisocyanate_mass+chain_extender_mass)/total_mass"
                ),
                "sequence_policy": (
                    "linear_alternating_DII_with_segmented_macrodiol_extender_order"
                ),
                "model_scope": "single_sequence_oligomer_proxy",
                "macrodiol_distribution_status": "single_chain_proxy_not_product_distribution",
                "nco_excess_representation": (
                    "separate_batch_and_multichain_context_not_embedded_in_single_chain"
                    if float(source["nco_oh_ratio_target"]) > 1.0
                    else "not_applicable"
                ),
                "md_execution_status": (
                    "blocked_pending_forcefield_COA_and_chain_distribution_validation"
                ),
                "performance_claim_status": "no_performance_claim",
            }
        )
    result = pd.DataFrame(rows)
    if not result["formulation_id"].is_unique:
        raise ValueError("MD计量计划formulation_id不唯一")
    return result.sort_values("formulation_id").reset_index(drop=True)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    formulations_path: Path,
    components_path: Path,
    macro_models_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (formulations_path, components_path, macro_models_path):
        if not path.is_file():
            raise ValueError(f"MD计量输入不存在: {path}")
    plan = build_md_stoichiometry_plan(
        pd.read_csv(formulations_path),
        pd.read_csv(components_path),
        pd.read_csv(macro_models_path),
    )
    table_path = output_root / "低聚链计量计划.csv"
    _atomic_text(table_path, plan.to_csv(index=False, float_format="%.12g"))
    note_path = output_root / "使用边界.md"
    _atomic_text(
        note_path,
        "\n".join(
            [
                "# 现实MD低聚链计量使用边界",
                "",
                "本表把目标硬段质量分数转换为整数构件数，只定义单条线性低聚链代理。",
                "单链主骨架固定为1:1交替连接；NCO/OH=1.02的2%过量只保留为批次/多链分布上下文，不用超长单链伪造精确比例。",
                "PTMG仍由单一代表链表示，不包含商品Mn/Mw/PDI、含水量、OH值或批次CoA分布。",
                "每条代理链保留一个NCO端和一个OH端；不得解释为最终聚合物真实端基分布或转化率。",
                "力场覆盖、部分电荷、链数/盒尺寸、密度、退火、平衡和独立重复未闭合前禁止启动块体MD。",
                "本计划不产生强度、韧性、Tg、DMA或相分离性能宣称。",
                "",
            ]
        ),
    )
    manifest = {
        "release_id": release_id,
        "status": "stoichiometry_ready_md_execution_blocked",
        "counts": {
            "formulations": len(plan),
            "within_tolerance": int(
                plan["hard_segment_fraction_abs_error"]
                .le(MAX_HARD_SEGMENT_ERROR)
                .sum()
            ),
        },
        "maximum_hard_segment_fraction_error": MAX_HARD_SEGMENT_ERROR,
        "inputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (formulations_path, components_path, macro_models_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (table_path, note_path)
        },
    }
    _atomic_text(
        output_root / "MD计量发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--高层候选",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "高层DFT候选12.csv",
    )
    parser.add_argument(
        "--现实构件", type=Path, default=ROOT / "数据" / "现实库" / "构件.csv"
    )
    parser.add_argument(
        "--PTMG模型",
        type=Path,
        default=ROOT / "数据" / "现实库" / "PTMG代表模型.csv",
    )
    parser.add_argument(
        "--输出目录", type=Path, default=ROOT / "计算" / "现实MD"
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-stoichiometry-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.高层候选,
        args.现实构件,
        args.PTMG模型,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
