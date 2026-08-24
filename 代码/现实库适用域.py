"""评估现实构件/配方相对现有GNN训练结构的Morgan指纹适用域。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem

from GNN数据 import load_computational_observations


ROOT = Path(__file__).resolve().parents[1]


def fingerprint(smiles: str, radius: int = 2, bits: int = 2048):
    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        raise ValueError(f"无法解析适用域SMILES: {smiles}")
    return AllChem.GetMorganGenerator(radius=radius, fpSize=bits).GetFingerprint(mol)


def build_reference_structures(observations: pd.DataFrame) -> pd.DataFrame:
    required = {"canonical_structure", "leakage_group", "development_split"}
    missing = sorted(required.difference(observations.columns))
    if missing:
        raise ValueError(f"适用域参考缺少字段: {missing}")
    reference = (
        observations[["canonical_structure", "leakage_group", "development_split"]]
        .drop_duplicates("canonical_structure")
        .sort_values("canonical_structure")
        .reset_index(drop=True)
    )
    if reference.empty:
        raise ValueError("适用域参考结构为空")
    return reference


def component_structure_table(
    components: pd.DataFrame,
    macro_models: pd.DataFrame,
) -> pd.DataFrame:
    model_map = macro_models.set_index("component_id")["representative_smiles"].to_dict()
    rows = []
    for row in components.itertuples(index=False):
        if row.role == "macrodiol":
            smiles = str(model_map.get(row.component_id, ""))
            representation = "single_oligomer_proxy_for_product_distribution"
        else:
            smiles = str(row.canonical_smiles)
            representation = str(row.identity_kind)
        if not smiles:
            raise ValueError(f"现实构件缺少适用域结构: {row.component_id}")
        rows.append(
            {
                "component_id": row.component_id,
                "preferred_name": row.preferred_name,
                "role": row.role,
                "representation_smiles": smiles,
                "representation_scope": representation,
            }
        )
    output = pd.DataFrame(rows)
    if not output["component_id"].is_unique:
        raise ValueError("现实构件适用域ID不唯一")
    return output


def evaluate_components(
    components: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    radius: int = 2,
    bits: int = 2048,
    in_domain_threshold: float = 0.60,
    boundary_threshold: float = 0.40,
) -> pd.DataFrame:
    if not 0 <= boundary_threshold <= in_domain_threshold <= 1:
        raise ValueError("适用域阈值不合法")
    reference_fps = [fingerprint(value, radius, bits) for value in reference["canonical_structure"]]
    rows = []
    for component in components.itertuples(index=False):
        fp = fingerprint(component.representation_smiles, radius, bits)
        similarities = DataStructs.BulkTanimotoSimilarity(fp, reference_fps)
        best = max(range(len(similarities)), key=lambda index: (similarities[index], -index))
        similarity = float(similarities[best])
        status = (
            "within_training_structure_domain"
            if similarity >= in_domain_threshold
            else "near_training_domain_boundary"
            if similarity >= boundary_threshold
            else "outside_training_structure_domain"
        )
        nearest = reference.iloc[best]
        rows.append(
            {
                **component._asdict(),
                "nearest_training_structure": nearest.canonical_structure,
                "nearest_training_leakage_group": nearest.leakage_group,
                "nearest_training_split": nearest.development_split,
                "max_morgan_tanimoto": similarity,
                "applicability_domain_status": status,
                "model_prediction_permission": (
                    "diagnostic_only_requires_formulation_model"
                    if status != "outside_training_structure_domain"
                    else "blocked_outside_structure_domain"
                ),
                "fingerprint_radius": radius,
                "fingerprint_bits": bits,
            }
        )
    return pd.DataFrame(rows).sort_values(["role", "component_id"]).reset_index(drop=True)


def evaluate_formulations(
    formulations: pd.DataFrame,
    component_domain: pd.DataFrame,
) -> pd.DataFrame:
    similarity = component_domain.set_index("component_id")["max_morgan_tanimoto"]
    status = component_domain.set_index("component_id")["applicability_domain_status"]
    output = formulations.copy()
    role_columns = {
        "diisocyanate": "diisocyanate_id",
        "macrodiol": "macrodiol_id",
        "chain_extender": "chain_extender_id",
    }
    for role, column in role_columns.items():
        mapped = output[column].map(similarity)
        if mapped.isna().any():
            raise ValueError(f"现实配方存在未评估构件: {role}")
        output[f"{role}_max_morgan_tanimoto"] = mapped
        output[f"{role}_domain_status"] = output[column].map(status)
    similarity_columns = [f"{role}_max_morgan_tanimoto" for role in role_columns]
    output["formulation_domain_floor"] = output[similarity_columns].min(axis=1)
    output["weakest_domain_role"] = output[similarity_columns].idxmin(axis=1).str.replace(
        "_max_morgan_tanimoto", "", regex=False
    )
    output["formulation_applicability_status"] = output["formulation_domain_floor"].map(
        lambda value: (
            "component_structures_within_or_near_domain"
            if value >= 0.40
            else "blocked_component_outside_training_domain"
        )
    )
    output["ml_prediction_status"] = "blocked_pending_multicomponent_formulation_model"
    output["performance_claim_status"] = "no_performance_claim"
    return output


def write_outputs(
    observations_path: Path,
    components_path: Path,
    macro_models_path: Path,
    formulations_path: Path,
    component_output: Path,
    formulation_output: Path,
    manifest_output: Path,
) -> dict[str, Any]:
    observations, targets = load_computational_observations(observations_path)
    reference = build_reference_structures(observations)
    structures = component_structure_table(
        pd.read_csv(components_path), pd.read_csv(macro_models_path)
    )
    component_domain = evaluate_components(structures, reference)
    formulation_domain = evaluate_formulations(
        pd.read_csv(formulations_path), component_domain
    )
    component_output.parent.mkdir(parents=True, exist_ok=True)
    component_domain.to_csv(component_output, index=False, encoding="utf-8")
    formulation_domain.to_csv(formulation_output, index=False, encoding="utf-8")
    manifest = {
        "status": "completed",
        "targets_defining_training_view": list(targets),
        "counts": {
            "training_observation_rows": len(observations),
            "unique_training_structures": len(reference),
            "reality_components": len(component_domain),
            "reality_formulations": len(formulation_domain),
        },
        "component_status_counts": component_domain["applicability_domain_status"].value_counts().astype(int).to_dict(),
        "formulation_status_counts": formulation_domain["formulation_applicability_status"].value_counts().astype(int).to_dict(),
        "method": "Morgan_radius2_2048bit_max_Tanimoto",
        "interpretation_limit": "component-level structural AD only; no multicomponent TPU performance prediction",
    }
    manifest_output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--计算观测", type=Path, default=ROOT / "结果" / "可用数据集" / "计算观测.csv.gz")
    parser.add_argument("--构件", type=Path, default=ROOT / "数据" / "现实库" / "构件.csv")
    parser.add_argument("--宏二醇模型", type=Path, default=ROOT / "数据" / "现实库" / "PTMG代表模型.csv")
    parser.add_argument("--配方", type=Path, default=ROOT / "数据" / "现实库" / "配方.csv")
    parser.add_argument("--构件输出", type=Path, default=ROOT / "数据" / "现实库" / "构件适用域.csv")
    parser.add_argument("--配方输出", type=Path, default=ROOT / "数据" / "现实库" / "配方适用域.csv")
    parser.add_argument("--清单输出", type=Path, default=ROOT / "数据" / "现实库" / "适用域运行清单.json")
    args = parser.parse_args(argv)
    manifest = write_outputs(
        args.计算观测,
        args.构件,
        args.宏二醇模型,
        args.配方,
        args.构件输出,
        args.配方输出,
        args.清单输出,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
