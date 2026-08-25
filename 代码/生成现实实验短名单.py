"""从12条商业高层候选生成6条校准—探索—专用分层实验短名单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SELECTION_POLICY = [
    {
        "formulation_id": "commercial_system_59ebf4f5a2e01a54_d4bd65fe8c7c0e5c",
        "experiment_order": 1,
        "experiment_stage": "A_calibration",
        "experiment_role": "aromatic_commercial_reference_low_hard_segment",
        "selection_reason": "4,4'-MDI/PTMG-2000/BDO commercial reference and prereaction Pareto anchor",
    },
    {
        "formulation_id": "commercial_system_8f78b79e85d09a49_aa4e010292f6d778",
        "experiment_order": 2,
        "experiment_stage": "A_calibration",
        "experiment_role": "aromatic_composition_contrast_high_hard_segment",
        "selection_reason": "same MDI/BDO family with PTMG-1000 and 0.45 hard-segment contrast",
    },
    {
        "formulation_id": "commercial_system_7faa7e08bccea0b7_aa4e010292f6d778",
        "experiment_order": 3,
        "experiment_stage": "A_calibration",
        "experiment_role": "aliphatic_diisocyanate_matched_control",
        "selection_reason": "IPDI/PTMG-1000/BDO matched 0.45 hard-segment light-stable control",
    },
    {
        "formulation_id": "commercial_system_1e9a19535918fee7_707fe304b16717b6",
        "experiment_order": 4,
        "experiment_stage": "B_priority_exploration",
        "experiment_role": "renewable_aliphatic_dii_cyclic_extender_exploration",
        "selection_reason": "PDI/PTMG-1400/CHDM prereaction Pareto and distinct renewable/cyclic chemistry",
    },
    {
        "formulation_id": "commercial_system_275f68de57f6a031_707fe304b16717b6",
        "experiment_order": 5,
        "experiment_stage": "B_priority_exploration",
        "experiment_role": "light_stable_cycloaliphatic_branched_extender",
        "selection_reason": "H12MDI/PTMG-1800/NPG complete prereaction starts and light-stable family",
    },
    {
        "formulation_id": "commercial_system_50aa43042a8e77b6_707fe304b16717b6",
        "experiment_order": 6,
        "experiment_stage": "C_specialty_deferred",
        "experiment_role": "specialty_aromatic_elastomer_high_complexity",
        "selection_reason": "NDI/PTMG-650/HQEE prereaction Pareto with specialty processing and molten-extender burden",
    },
]
AROMATIC_DIISOCYANATES = {
    "commercial_mdi_44",
    "commercial_ndi_15",
    "commercial_tdi_24",
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


def build_shortlist(
    candidates: pd.DataFrame,
    md_plan: pd.DataFrame,
    parameter_audit: pd.DataFrame,
) -> pd.DataFrame:
    policy = pd.DataFrame(SELECTION_POLICY)
    if not policy["formulation_id"].is_unique or not policy["experiment_order"].is_unique:
        raise ValueError("实验短名单策略ID和顺序必须唯一")
    required = {
        "formulation_id",
        "diisocyanate_id",
        "diisocyanate_name",
        "macrodiol_id",
        "macrodiol_name",
        "chain_extender_id",
        "chain_extender_name",
        "hard_segment_mass_fraction_target",
        "nco_oh_ratio_target",
        "procurement_review_status",
        "sds_review_status",
        "experiment_use_status",
        "updated_dft_priority_rank",
        "updated_dft_priority_class",
        "prereaction_pareto_is_nondominated",
        "macrodiol_pair__pair_status",
        "chain_extender_pair__pair_status",
    }
    missing = sorted(required.difference(candidates.columns))
    if missing:
        raise ValueError(f"实验短名单候选缺字段: {missing}")
    joined = policy.merge(
        candidates,
        on="formulation_id",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    if not joined["_merge"].eq("both").all():
        raise ValueError("实验短名单策略存在未找到候选")
    joined = joined.drop(columns="_merge")
    joined = joined.merge(
        md_plan[
            [
                "formulation_id",
                "macrodiol_count",
                "chain_extender_count",
                "diisocyanate_count",
                "realized_hard_segment_mass_fraction",
                "hard_segment_fraction_abs_error",
                "estimated_nominal_chain_mass_g_mol",
                "estimated_atom_count",
            ]
        ],
        on="formulation_id",
        how="left",
        validate="one_to_one",
    )
    joined = joined.merge(
        parameter_audit[
            [
                "formulation_id",
                "alternate_parameter_event_count",
                "events_per_estimated_urethane_bond",
                "parameter_validation_status",
            ]
        ],
        on="formulation_id",
        how="left",
        validate="one_to_one",
    )
    if joined[
        [
            "estimated_atom_count",
            "alternate_parameter_event_count",
        ]
    ].isna().any().any():
        raise ValueError("实验短名单未完全连接MD计量或参数审计")
    joined["forcefield_family_status"] = joined["diisocyanate_id"].map(
        lambda value: (
            "blocked_aromatic_urethane_rigid_scan_failed"
            if value in AROMATIC_DIISOCYANATES
            else "blocked_aliphatic_urethane_relaxed_validation_pending"
        )
    )
    joined["experiment_release_status_current"] = (
        "blocked_pending_quote_sds_coa_and_local_approval"
    )
    joined["shortlist_interpretation"] = (
        "calibration_or_exploration_candidate_not_performance_winner"
    )
    joined["performance_claim_status"] = "no_performance_claim"
    output_columns = [
        "experiment_order",
        "experiment_stage",
        "experiment_role",
        "formulation_id",
        "diisocyanate_id",
        "diisocyanate_name",
        "macrodiol_id",
        "macrodiol_name",
        "chain_extender_id",
        "chain_extender_name",
        "hard_segment_mass_fraction_target",
        "nco_oh_ratio_target",
        "macrodiol_count",
        "chain_extender_count",
        "diisocyanate_count",
        "realized_hard_segment_mass_fraction",
        "hard_segment_fraction_abs_error",
        "estimated_nominal_chain_mass_g_mol",
        "estimated_atom_count",
        "updated_dft_priority_rank",
        "updated_dft_priority_class",
        "prereaction_pareto_is_nondominated",
        "macrodiol_pair__pair_status",
        "chain_extender_pair__pair_status",
        "alternate_parameter_event_count",
        "events_per_estimated_urethane_bond",
        "forcefield_family_status",
        "procurement_review_status",
        "sds_review_status",
        "experiment_use_status",
        "experiment_release_status_current",
        "selection_reason",
        "shortlist_interpretation",
        "performance_claim_status",
    ]
    return joined[output_columns].sort_values("experiment_order").reset_index(
        drop=True
    )


def write_release(
    candidate_path: Path,
    md_plan_path: Path,
    parameter_audit_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (candidate_path, md_plan_path, parameter_audit_path):
        if not path.is_file():
            raise ValueError(f"实验短名单输入不存在: {path}")
    shortlist = build_shortlist(
        pd.read_csv(candidate_path),
        pd.read_csv(md_plan_path),
        pd.read_csv(parameter_audit_path),
    )
    output_root.mkdir(parents=True, exist_ok=True)
    table_path = output_root / "实验短名单6.csv"
    report_path = output_root / "实验短名单说明.md"
    _atomic_text(table_path, shortlist.to_csv(index=False, float_format="%.12g"))
    stages = shortlist.groupby("experiment_stage").size().to_dict()
    _atomic_text(
        report_path,
        "\n".join(
            [
                "# 现实TPU实验短名单",
                "",
                "短名单不是性能排名，而是校准对照、优先探索和高复杂度专用体系的分层设计。全部原料均有制造商或目录证据，但实时库存、报价、批次CoA、SDS、本单位EHS和实验审批均未闭合。",
                "",
                "## A：校准对照",
                "",
                "1. MDI/PTMG-2000/BDO，硬段0.35：芳香族商业参考。",
                "2. MDI/PTMG-1000/BDO，硬段0.45：与1形成软段Mn/硬段联合对照。",
                "3. IPDI/PTMG-1000/BDO，硬段0.45：与2形成芳香/脂环二异氰酸酯对照。",
                "",
                "## B：优先探索",
                "",
                "4. PDI/PTMG-1400/CHDM：可再生脂肪族DII与环状扩链剂组合。",
                "5. H12MDI/PTMG-1800/NPG：耐候脂环DII与支化扩链剂组合。",
                "",
                "## C：专用条件后置",
                "",
                "6. NDI/PTMG-650/HQEE：高硬段密度专用弹性体路线，但HQEE熔融处理、NDI专用工艺和芳香族力场失败使其后置。",
                "",
                "任何候选在报价、SDS、CoA/OH值/含水量、催化剂和本地审批闭合前均不得采购或投料。计算代理不构成高性能宣称。",
                "",
            ]
        ),
    )
    files = [table_path, report_path]
    manifest = {
        "release_id": release_id,
        "status": "six_candidate_experiment_design_selected_execution_blocked",
        "counts": {
            "candidates": len(shortlist),
            "stages": stages,
            "diisocyanates": shortlist["diisocyanate_id"].nunique(),
            "macrodiol_grades": shortlist["macrodiol_id"].nunique(),
            "chain_extenders": shortlist["chain_extender_id"].nunique(),
        },
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (candidate_path, md_plan_path, parameter_audit_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "experiment_release_status": "blocked_pending_quote_sds_coa_and_local_approval",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "实验短名单发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--候选",
        type=Path,
        default=ROOT
        / "计算"
        / "现实预反应复合物"
        / "筛选更新"
        / "高层DFT候选_预反应更新.csv",
    )
    parser.add_argument(
        "--MD计量",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链计量计划.csv",
    )
    parser.add_argument(
        "--参数审计",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "参数验证" / "GAFF2替代参数逐配方.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "实验短名单",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-experiment-shortlist-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.候选,
        args.MD计量,
        args.参数审计,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
