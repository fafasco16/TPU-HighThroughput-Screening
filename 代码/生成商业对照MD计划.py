"""为芳香/脂肪商业校准体系生成生产MD预注册计划，不执行MD。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
TARGET_EXPERIMENT_ORDERS = {2: "aromatic_urethane", 3: "aliphatic_urethane"}


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def stable_seed(formulation_id: str, replica_index: int) -> int:
    payload = f"tpu-commercial-md-v1|{formulation_id}|{replica_index}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big") & 0x7FFFFFFF


def chain_count_for_target(atom_count: int, target_atoms: int = 10_000) -> int:
    if atom_count < 1 or target_atoms < 1:
        raise ValueError("商业对照MD原子数必须为正")
    minimum = max(4, math.ceil(target_atoms / atom_count))
    return int(math.ceil(minimum / 4) * 4)


def build_plan(
    shortlist: pd.DataFrame,
    chains: pd.DataFrame,
    parameter_gate: dict[str, Any],
    *,
    replicates: int = 3,
    target_atoms: int = 10_000,
) -> pd.DataFrame:
    if replicates < 3:
        raise ValueError("商业对照生产MD至少需要3个独立重复")
    required_shortlist = {
        "experiment_order",
        "experiment_stage",
        "formulation_id",
        "diisocyanate_name",
        "macrodiol_name",
        "chain_extender_name",
        "hard_segment_mass_fraction_target",
        "nco_oh_ratio_target",
    }
    required_chains = {"formulation_id", "atom_count"}
    missing = {
        "shortlist": sorted(required_shortlist.difference(shortlist.columns)),
        "chains": sorted(required_chains.difference(chains.columns)),
    }
    if any(missing.values()):
        raise ValueError(f"商业对照MD计划输入缺字段: {missing}")
    selected = shortlist.loc[
        shortlist["experiment_order"].astype(int).isin(TARGET_EXPERIMENT_ORDERS)
    ].copy()
    if len(selected) != 2 or set(selected["experiment_order"].astype(int)) != set(
        TARGET_EXPERIMENT_ORDERS
    ):
        raise ValueError("商业对照MD必须恰含实验顺序2和3")
    if not selected["experiment_stage"].eq("A_calibration").all():
        raise ValueError("商业对照MD体系必须属于A_calibration")
    selected = selected.merge(
        chains[["formulation_id", "atom_count"]],
        on="formulation_id",
        how="left",
        validate="one_to_one",
    )
    if selected["atom_count"].isna().any():
        raise ValueError("商业对照MD体系缺低聚链原子数")
    gate_status = str(parameter_gate["forcefield_parameter_gate"]["status"])
    if not str(parameter_gate.get("production_md_permission", "")).startswith(
        "blocked"
    ):
        raise ValueError("本计划生成器只允许在生产MD仍阻断时预注册")
    rows = []
    for source in selected.sort_values("experiment_order").to_dict(orient="records"):
        experiment_order = int(source["experiment_order"])
        family = TARGET_EXPERIMENT_ORDERS[experiment_order]
        atom_count = int(source["atom_count"])
        chain_count = chain_count_for_target(atom_count, target_atoms)
        for replica_index in range(1, replicates + 1):
            rows.append(
                {
                    "formulation_id": source["formulation_id"],
                    "experiment_order": experiment_order,
                    "validation_family": family,
                    "diisocyanate_name": source["diisocyanate_name"],
                    "macrodiol_name": source["macrodiol_name"],
                    "chain_extender_name": source["chain_extender_name"],
                    "hard_segment_mass_fraction_target": source[
                        "hard_segment_mass_fraction_target"
                    ],
                    "nco_oh_ratio_target": source["nco_oh_ratio_target"],
                    "proxy_chain_atom_count": atom_count,
                    "planned_chain_count": chain_count,
                    "estimated_box_atom_count": atom_count * chain_count,
                    "replica_index": replica_index,
                    "packing_seed": stable_seed(
                        str(source["formulation_id"]), replica_index
                    ),
                    "protocol_id": "tpu-commercial-control-md-v1",
                    "protocol_stage_ids": (
                        "pack_low_density;minimize;anneal_high_T;"
                        "npt_compress;cool_high_T_to_300K;npt_300K_production"
                    ),
                    "initial_density_g_cm3": 0.20,
                    "anneal_temperature_k_provisional": 500.0,
                    "anneal_temperature_gate": "blocked_pending_tga_and_forcefield_stability",
                    "production_temperature_k": 300.0,
                    "production_pressure_atm": 1.0,
                    "parameter_gate_status": gate_status,
                    "real_ptmg_batch_status": "blocked_missing_coa_oh_water_mn_mw_pdi",
                    "execution_status": "planned_not_executable_parameter_and_batch_gates",
                    "property_claim_status": "no_density_tg_or_mechanical_claim",
                }
            )
    plan = pd.DataFrame(rows)
    if len(plan) != 2 * replicates or plan.duplicated(
        ["formulation_id", "replica_index"]
    ).any():
        raise ValueError("商业对照MD重复计划不闭合")
    return plan


def write_release(
    shortlist_path: Path,
    chain_path: Path,
    parameter_gate_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (shortlist_path, chain_path, parameter_gate_path):
        if not path.is_file():
            raise ValueError(f"商业对照MD计划输入不存在: {path}")
    parameter_gate = json.loads(parameter_gate_path.read_text(encoding="utf-8"))
    plan = build_plan(
        pd.read_csv(shortlist_path), pd.read_csv(chain_path), parameter_gate
    )
    output_root.mkdir(parents=True, exist_ok=True)
    plan_out = output_root / "商业对照MD计划.csv"
    trajectory_template_out = output_root / "商业对照MD轨迹模板.csv"
    report_out = output_root / "商业对照MD计划说明.md"
    _atomic_text(plan_out, plan.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        trajectory_template_out,
        pd.DataFrame(
            columns=[
                "formulation_id",
                "replica_index",
                "time_ps",
                "density_g_cm3",
                "potential_energy_kcal_mol",
                "volume_a3",
                "radius_of_gyration_a",
                "end_to_end_distance_a",
                "temperature_k",
                "pressure_atm",
            ]
        ).to_csv(index=False),
    )
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 商业对照生产MD预注册计划",
                "",
                "体系固定为MDI/PTMG-1000/BDO芳香族对照与IPDI/PTMG-1000/BDO脂肪族对照，各3个独立盒。链数按约10,000原子并向上取4的倍数确定。",
                "",
                "计划只冻结体系、重复、种子和阶段顺序，不创建LAMMPS生产输入。真实PTMG批次CoA、OH值、水分、Mn/Mw/PDI、完整链电荷、外部扭转验证和凝聚相参数门未闭合前，`execution_status`保持不可执行。",
                "",
                "建议阶段顺序为低密度打包、能量最小化、高温退火、NPT压缩、冷却到300 K和300 K NPT生产。高温暂记500 K，但必须由真实材料TGA与力场稳定性复核后冻结；不得把此前1 ps、0.20 g cm⁻³烟雾测试解释为密度预测。",
                "",
            ]
        ),
    )
    files = [plan_out, trajectory_template_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": "commercial_control_md_preregistered_execution_blocked",
        "counts": {
            "systems": plan["formulation_id"].nunique(),
            "replicas": len(plan),
            "aromatic_replicas": int(
                plan["validation_family"].eq("aromatic_urethane").sum()
            ),
            "aliphatic_replicas": int(
                plan["validation_family"].eq("aliphatic_urethane").sum()
            ),
        },
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (shortlist_path, chain_path, parameter_gate_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked",
        "performance_claim_status": "no_performance_claim",
        "convergence_validator": "代码/验证商业对照MD收敛.py",
    }
    _atomic_text(
        output_root / "商业对照MD计划发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--实验短名单",
        type=Path,
        default=ROOT
        / "结果"
        / "现实筛选"
        / "实验短名单"
        / "实验短名单6.csv",
    )
    parser.add_argument(
        "--低聚链",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链化学图.csv.gz",
    )
    parser.add_argument(
        "--参数门",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "参数验证" / "生产参数门.json",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "商业对照MD计划",
    )
    parser.add_argument(
        "--发布ID",
        default="tpu-reality-md-commercial-controls-preregistered-20260825-v1",
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.实验短名单,
        args.低聚链,
        args.参数门,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
