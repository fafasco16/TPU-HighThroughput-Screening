"""构建只引用冻结资产的虚拟库索引和证据门控的现实库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import yaml
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: str | Path) -> dict[str, Any]:
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    required = {"release_id", "virtual_assets", "inputs", "ptmg_model", "outputs"}
    if not isinstance(config, dict) or required.difference(config):
        raise ValueError(f"双库配置缺少分区: {sorted(required.difference(config or {}))}")
    return config


def _csv_row_count(path: Path) -> int:
    return int(len(pd.read_csv(path)))


def build_virtual_asset_index(
    specs: Sequence[Mapping[str, Any]],
    release_manifest: Mapping[str, Any],
    *,
    root: str | Path = ROOT,
) -> pd.DataFrame:
    base = Path(root)
    rows: list[dict[str, Any]] = []
    for spec in specs:
        path = base / str(spec["path"])
        if not path.is_file():
            raise FileNotFoundError(path)
        count_source = str(spec["row_count_source"])
        if count_source.startswith("release_manifest:"):
            key = count_source.split(":", 1)[1]
            if key not in release_manifest.get("counts", {}):
                raise ValueError(f"发布清单缺少计数: {key}")
            row_count = int(release_manifest["counts"][key])
        elif count_source == "csv":
            row_count = _csv_row_count(path)
        else:
            raise ValueError(f"未知row_count_source: {count_source}")
        rows.append(
            {
                "asset_id": str(spec["asset_id"]),
                "path": path.relative_to(base).as_posix(),
                "layer": str(spec["layer"]),
                "row_count": row_count,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "allowed_use": str(spec["allowed_use"]),
                "storage_policy": "reference_existing_file",
                "experiment_component_eligibility": "not_applicable_virtual_asset",
            }
        )
    output = pd.DataFrame(rows)
    if output["asset_id"].duplicated().any() or output["path"].duplicated().any():
        raise ValueError("虚拟资产ID和路径必须唯一")
    return output


def build_reality_components(pool: pd.DataFrame) -> pd.DataFrame:
    required = {
        "stable_component_id",
        "preferred_name",
        "role",
        "identity_kind",
        "canonical_smiles",
        "repeat_unit",
        "nominal_mn_g_mol",
        "cas_or_product_grade",
        "supplier_or_manufacturer",
        "evidence_url",
        "accessed_date",
        "commercial_evidence_status",
        "experimental_gate_status",
        "experiment_release_status",
        "source_scope",
    }
    missing = sorted(required.difference(pool.columns))
    if missing:
        raise ValueError(f"现实构件输入缺少字段: {missing}")
    passed = pool.loc[pool["experimental_gate_status"].eq("passed_for_planning")].copy()
    if passed.empty:
        raise ValueError("没有通过商业实验规划门的构件")
    if not passed["source_scope"].eq("added_commercial_control").all():
        raise ValueError("现实构件混入未验证的虚拟来源")
    if not passed["commercial_evidence_status"].eq("catalog_or_manufacturer_evidence").all():
        raise ValueError("现实构件缺少直接商业证据")
    passed = passed.rename(columns={"stable_component_id": "component_id"})
    passed["library"] = "reality"
    passed["direct_commercial_evidence_status"] = "verified_dated_page"
    passed["current_stock_status"] = "requires_quote"
    passed["experiment_use_status"] = passed["experiment_release_status"]
    columns = [
        "component_id",
        "preferred_name",
        "synonym",
        "role",
        "identity_kind",
        "canonical_smiles",
        "repeat_unit",
        "nominal_mn_g_mol",
        "cas_or_product_grade",
        "supplier_or_manufacturer",
        "evidence_url",
        "accessed_date",
        "direct_commercial_evidence_status",
        "current_stock_status",
        "ehs_review_status",
        "experiment_use_status",
        "priority_class",
        "library",
        "notes",
    ]
    output = passed[columns].sort_values(["role", "component_id"]).reset_index(drop=True)
    if not output["component_id"].is_unique:
        raise ValueError("现实构件ID不唯一")
    return output


def build_reality_formulations(
    formulations: pd.DataFrame,
    reality_components: pd.DataFrame,
) -> pd.DataFrame:
    required = {"formulation_id", "diisocyanate_id", "macrodiol_id", "chain_extender_id"}
    if required.difference(formulations.columns):
        raise ValueError(f"现实配方输入缺少字段: {sorted(required.difference(formulations.columns))}")
    allowed = set(reality_components["component_id"])
    for column in ("diisocyanate_id", "macrodiol_id", "chain_extender_id"):
        leaked = sorted(set(formulations[column]).difference(allowed))
        if leaked:
            raise ValueError(f"现实配方混入非现实构件: {column}={leaked[:3]}")
    output = formulations.copy()
    output["library"] = "reality"
    output["reality_component_gate_status"] = "all_three_components_verified"
    output["experiment_use_status"] = "blocked_pending_quote_sds_and_local_approval"
    if not output["formulation_id"].is_unique:
        raise ValueError("现实配方ID不唯一")
    return output.sort_values(
        ["baseline_priority", "macrodiol_nominal_mn_g_mol", "hard_segment_mass_fraction_target", "nco_oh_ratio_target"]
    ).reset_index(drop=True)


def build_ptmg_representative(
    component_id: str,
    nominal_mn_g_mol: float,
    model_config: Mapping[str, Any],
) -> dict[str, Any]:
    end_mass = float(model_config["end_group_mass_g_mol"])
    repeat_mass = float(model_config["repeat_mass_g_mol"])
    if not math.isfinite(nominal_mn_g_mol) or nominal_mn_g_mol <= end_mass:
        raise ValueError("PTMG名义Mn不合法")
    repeat_count = max(1, int(round((nominal_mn_g_mol - end_mass) / repeat_mass)))
    smiles = "O" + str(model_config["repeat_smiles_fragment"]) * repeat_count
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise RuntimeError("PTMG代表SMILES生成失败")
    calculated_mass = float(Descriptors.MolWt(mol))
    return {
        "component_id": component_id,
        "nominal_mn_g_mol": float(nominal_mn_g_mol),
        "repeat_count": repeat_count,
        "representative_smiles": Chem.MolToSmiles(mol),
        "molecular_formula": rdMolDescriptors.CalcMolFormula(mol),
        "rdkit_mol_weight": calculated_mass,
        "nominal_mn_residual_g_mol": calculated_mass - float(nominal_mn_g_mol),
        "approximation_status": str(model_config["approximation_status"]),
        "distribution_claim_status": "no_distribution_claim",
        "required_product_fields": "OH_number;water;Mn;Mw;PDI;lot_COA",
    }


def build_ptmg_models(
    reality_components: pd.DataFrame,
    model_config: Mapping[str, Any],
) -> pd.DataFrame:
    macros = reality_components.loc[reality_components["role"].eq("macrodiol")]
    rows = [
        build_ptmg_representative(
            row.component_id,
            float(row.nominal_mn_g_mol),
            model_config,
        )
        for row in macros.itertuples(index=False)
    ]
    if not rows:
        raise ValueError("现实库没有商业宏二醇")
    return pd.DataFrame(rows).sort_values("nominal_mn_g_mol").reset_index(drop=True)


def _task_id(kind: str, identity: str) -> str:
    digest = hashlib.sha256(f"{kind}|{identity}".encode("utf-8")).hexdigest()[:16]
    return f"reality_{kind}_{digest}"


def build_calculation_tasks(
    components: pd.DataFrame,
    formulations: pd.DataFrame,
    ptmg_models: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    discrete = components.loc[
        components["identity_kind"].isin(["discrete_substance", "commercial_isomer_mixture"])
    ]
    for row in discrete.itertuples(index=False):
        rows.append(
            {
                "task_id": _task_id("component_crest_xtb", row.component_id),
                "task_kind": "component_crest_xtb",
                "target_id": row.component_id,
                "input_identity": row.canonical_smiles,
                "dependency_ids": "",
                "input_status": "ready_for_3d_generation",
                "calculation_status": "not_started",
                "result_use": "commercial_component_descriptor",
            }
        )
    for row in ptmg_models.itertuples(index=False):
        rows.append(
            {
                "task_id": _task_id("macrodiol_representative_crest_xtb", row.component_id),
                "task_kind": "macrodiol_representative_crest_xtb",
                "target_id": row.component_id,
                "input_identity": row.representative_smiles,
                "dependency_ids": "",
                "input_status": "ready_as_explicit_single_oligomer_proxy",
                "calculation_status": "not_started",
                "result_use": "product_grade_proxy_descriptor_no_distribution_claim",
            }
        )
    tier1 = formulations.loc[formulations["planning_tier"].eq("tier1_small_control_matrix")]
    component_task_by_target = {row["target_id"]: row["task_id"] for row in rows}
    for formulation in tier1.itertuples(index=False):
        dependencies = ";".join(
            component_task_by_target[target]
            for target in (
                formulation.diisocyanate_id,
                formulation.macrodiol_id,
                formulation.chain_extender_id,
            )
        )
        dft_id = _task_id("formulation_dft", formulation.formulation_id)
        rows.append(
            {
                "task_id": dft_id,
                "task_kind": "formulation_dft",
                "target_id": formulation.formulation_id,
                "input_identity": formulation.component_ids,
                "dependency_ids": dependencies,
                "input_status": "blocked_pending_component_descriptors_and_reacted_fragment",
                "calculation_status": "blocked",
                "result_use": "selected_reacted_fragment_electronic_validation",
            }
        )
        rows.append(
            {
                "task_id": _task_id("bulk_md", formulation.formulation_id),
                "task_kind": "bulk_md",
                "target_id": formulation.formulation_id,
                "input_identity": formulation.component_ids,
                "dependency_ids": dft_id,
                "input_status": "blocked_pending_reacted_chain_and_force_field",
                "calculation_status": "blocked",
                "result_use": "density_hydrogen_bond_morphology_and_mechanics",
            }
        )
    output = pd.DataFrame(rows)
    if not output["task_id"].is_unique:
        raise RuntimeError("现实计算任务ID不唯一")
    return output.reset_index(drop=True)


def build_screening_queue(formulations: pd.DataFrame) -> pd.DataFrame:
    output = formulations.copy()
    tier1 = output["planning_tier"].eq("tier1_small_control_matrix")
    output["queue_status"] = tier1.map({True: "tier1_component_calculation_queue", False: "held_after_tier1"})
    output["ml_status"] = "blocked_pending_formulation_representation"
    output["component_quantum_status"] = tier1.map({True: "queued", False: "held"})
    output["formulation_dft_status"] = "blocked_pending_component_descriptors_and_reacted_fragment"
    output["md_status"] = "blocked_pending_reacted_chain_and_force_field"
    output["ranking_status"] = "not_ranked_no_performance_claim"
    output["next_gate"] = tier1.map(
        {True: "commercial_component_crest_xtb", False: "wait_for_tier1_review"}
    )
    return output


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(path)


def write_dual_library(config_path: str | Path, *, root: str | Path = ROOT) -> dict[str, Any]:
    base = Path(root)
    config = load_config(config_path)
    inputs = {key: base / value for key, value in config["inputs"].items()}
    release_manifest = json.loads(inputs["release_manifest"].read_text(encoding="utf-8"))
    virtual_index = build_virtual_asset_index(config["virtual_assets"], release_manifest, root=base)
    commercial_pool = pd.read_csv(inputs["commercial_components"])
    reality_components = build_reality_components(commercial_pool)
    reality_formulations = build_reality_formulations(
        pd.read_csv(inputs["commercial_formulations"]), reality_components
    )
    evidence = pd.read_csv(inputs["commercial_evidence"])
    reality_evidence = evidence.loc[evidence["stable_component_id"].isin(reality_components["component_id"])].copy()
    ptmg_models = build_ptmg_models(reality_components, config["ptmg_model"])
    calculation_tasks = build_calculation_tasks(reality_components, reality_formulations, ptmg_models)
    screening_queue = build_screening_queue(reality_formulations)
    pending_macrodiols = commercial_pool.loc[
        commercial_pool["role"].eq("macrodiol")
        & commercial_pool["experimental_gate_status"].eq("blocked")
        & commercial_pool["commercial_evidence_status"].eq("catalog_or_manufacturer_evidence")
    ].copy()
    pending_macrodiols["library_status"] = "commercial_evidence_only_pending_representative_model"
    outputs = {key: base / value for key, value in config["outputs"].items()}
    frames = {
        "virtual_asset_index": virtual_index,
        "reality_components": reality_components,
        "reality_formulations": reality_formulations,
        "reality_evidence": reality_evidence,
        "ptmg_models": ptmg_models,
        "calculation_tasks": calculation_tasks,
        "screening_queue": screening_queue,
        "pending_macrodiols": pending_macrodiols,
    }
    for key, frame in frames.items():
        _write_csv(frame, outputs[key])
    reality_ids = set(reality_components["component_id"])
    formulation_ids = set(reality_formulations["diisocyanate_id"]) | set(reality_formulations["macrodiol_id"]) | set(reality_formulations["chain_extender_id"])
    if not formulation_ids <= reality_ids:
        raise RuntimeError("双库发布检测到虚拟构件泄漏")
    manifest = {
        "release_id": config["release_id"],
        "status": "completed",
        "configuration": {"path": str(config_path), "sha256": sha256_file(config_path)},
        "counts": {key: len(frame) for key, frame in frames.items()},
        "virtual_to_reality_component_leakage_count": 0,
        "inputs": {
            key: {"path": path.relative_to(base).as_posix(), "sha256": sha256_file(path)}
            for key, path in inputs.items()
        },
        "outputs": {
            key: {"path": outputs[key].relative_to(base).as_posix(), "sha256": sha256_file(outputs[key])}
            for key in frames
        },
        "experiment_release_status": "blocked_pending_quote_sds_and_local_approval",
        "gold_files_mutated": False,
    }
    outputs["manifest"].parent.mkdir(parents=True, exist_ok=True)
    outputs["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--配置", type=Path, default=ROOT / "配置" / "双库筛选.yaml")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = write_dual_library(args.配置)
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
