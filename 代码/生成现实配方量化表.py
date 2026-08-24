"""把离散商业构件系综和 PTMG 单链代理连接为现实配方量化表。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from 配方系综特征 import aggregate_formulation_features


ROOT = Path(__file__).resolve().parents[1]
PTMG_SCOPE = "single_oligomer_proxy_for_product_distribution"
ROLE_ID_COLUMNS = {
    "diisocyanate": "diisocyanate_id",
    "macrodiol": "macrodiol_id",
    "chain_extender": "chain_extender_id",
}
COMPONENT_METADATA = {
    "candidate_id",
    "component_id",
    "component_role",
    "commercial_role",
    "canonical_smiles",
    "preferred_name",
    "role",
    "identity_kind",
    "direct_commercial_evidence_status",
    "ehs_review_status",
    "experiment_use_status",
    "ensemble_status",
    "complete_weighted_release",
    "calculation_status",
    "descriptor_fidelity",
    "representation_scope",
    "distribution_claim_status",
    "proxy_interpretation_limit",
}


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


def _truth(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes"}


def _validate_ensemble(frame: pd.DataFrame, label: str) -> None:
    _required(
        frame,
        {
            "candidate_id",
            "component_role",
            "ensemble_status",
            "failure_count",
            "complete_weighted_release",
            "conformer_count_input",
            "conformer_count_success",
        },
        label,
    )
    if frame.empty:
        raise ValueError(f"{label}不能为空")
    if frame["candidate_id"].isna().any() or not frame["candidate_id"].is_unique:
        raise ValueError(f"{label} candidate_id不唯一或为空")
    complete = frame["ensemble_status"].astype(str).eq("complete")
    release = frame["complete_weighted_release"].map(_truth)
    failures = pd.to_numeric(frame["failure_count"], errors="coerce").eq(0)
    count_input = pd.to_numeric(frame["conformer_count_input"], errors="coerce")
    count_success = pd.to_numeric(frame["conformer_count_success"], errors="coerce")
    count_closed = count_input.notna() & count_input.eq(count_success) & count_input.gt(0)
    valid = complete & release & failures & count_closed
    if not valid.all():
        ids = frame.loc[~valid, "candidate_id"].astype(str).tolist()
        raise ValueError(f"{label}存在未通过完整发布门的构件: {ids}")


def _uncertainty(frame: pd.DataFrame) -> pd.Series:
    fields = [
        column
        for column in (
            "homo_lumo_gap_ev_weighted_sd",
            "site_charge_e_mean_weighted_sd",
            "site_relative_sasa_mean_weighted_sd",
        )
        if column in frame.columns
    ]
    if not fields:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    numeric = frame[fields].apply(pd.to_numeric, errors="coerce")
    return np.sqrt(numeric.pow(2).mean(axis=1, skipna=False))


def build_component_descriptor_table(
    discrete_ensembles: pd.DataFrame,
    ptmg_proxies: pd.DataFrame,
    commercial_components: pd.DataFrame,
) -> pd.DataFrame:
    """形成一行一个现实构件的混合保真度描述符表。"""

    _validate_ensemble(discrete_ensembles, "离散构件系综")
    _validate_ensemble(ptmg_proxies, "PTMG代理")
    if not discrete_ensembles["component_role"].isin(
        ["diisocyanate", "chain_extender"]
    ).all():
        raise ValueError("离散构件系综含非二异氰酸酯/扩链剂角色")
    if not ptmg_proxies["component_role"].astype(str).eq("macrodiol_proxy").all():
        raise ValueError("PTMG代理component_role必须是macrodiol_proxy")
    ptmg_count = pd.to_numeric(
        ptmg_proxies["conformer_count_input"], errors="coerce"
    )
    if not ptmg_count.eq(1).all():
        raise ValueError("PTMG代理必须严格为单构象输入")
    combined_ids = pd.concat(
        [discrete_ensembles["candidate_id"], ptmg_proxies["candidate_id"]],
        ignore_index=True,
    )
    if not combined_ids.is_unique:
        raise ValueError("离散与PTMG描述符candidate_id不唯一")
    _required(
        commercial_components,
        {
            "component_id",
            "preferred_name",
            "role",
            "identity_kind",
            "direct_commercial_evidence_status",
            "ehs_review_status",
            "experiment_use_status",
        },
        "现实构件表",
    )
    if not commercial_components["component_id"].is_unique:
        raise ValueError("现实构件表component_id不唯一")
    expected = set(commercial_components["component_id"].astype(str))
    observed = set(combined_ids.astype(str))
    if expected != observed:
        raise ValueError(
            "现实构件与量化描述符集合不一致: "
            f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )

    discrete = discrete_ensembles.copy()
    discrete["descriptor_fidelity"] = "crest_ensemble"
    discrete["representation_scope"] = "exact_discrete_commercial_substance"
    discrete["distribution_claim_status"] = "not_applicable"
    discrete["proxy_interpretation_limit"] = "gas_phase_component_descriptor_only"
    macro = ptmg_proxies.copy()
    macro["descriptor_fidelity"] = "single_conformer_proxy"
    macro["representation_scope"] = PTMG_SCOPE
    macro["distribution_claim_status"] = "no_distribution_claim"
    macro["proxy_interpretation_limit"] = (
        "single_chain_proxy_not_commercial_Mn_Mw_PDI_distribution"
    )
    combined = pd.concat([discrete, macro], ignore_index=True, sort=False)
    combined = combined.rename(columns={"candidate_id": "component_id"})
    combined["calculation_status"] = "completed"
    combined["conformer_uncertainty"] = _uncertainty(combined)

    metadata = commercial_components[
        [
            "component_id",
            "preferred_name",
            "role",
            "identity_kind",
            "direct_commercial_evidence_status",
            "ehs_review_status",
            "experiment_use_status",
        ]
    ]
    output = combined.merge(metadata, on="component_id", how="left", validate="one_to_one")
    expected_role = output["component_role"].replace({"macrodiol_proxy": "macrodiol"})
    if not expected_role.eq(output["role"].astype(str)).all():
        bad = output.loc[~expected_role.eq(output["role"].astype(str)), "component_id"]
        raise ValueError(f"现实构件角色与量化角色不一致: {bad.tolist()}")
    output["commercial_role"] = output["role"]
    ordered = [
        "component_id",
        "preferred_name",
        "commercial_role",
        "component_role",
        "identity_kind",
        "descriptor_fidelity",
        "representation_scope",
        "distribution_claim_status",
        "proxy_interpretation_limit",
        "calculation_status",
        "direct_commercial_evidence_status",
        "ehs_review_status",
        "experiment_use_status",
    ]
    remaining = [column for column in output.columns if column not in ordered]
    return output[ordered + remaining].sort_values(
        ["commercial_role", "component_id"], kind="stable"
    ).reset_index(drop=True)


def build_formulation_descriptor_table(
    formulations: pd.DataFrame,
    formulation_domain: pd.DataFrame,
    component_descriptors: pd.DataFrame,
) -> pd.DataFrame:
    """把 19 个构件描述符通过三角色稳定 ID 连接到现实配方。"""

    _required(
        formulations,
        {"formulation_id", *ROLE_ID_COLUMNS.values(), "performance_claim_status"},
        "现实配方",
    )
    if formulations.empty or not formulations["formulation_id"].is_unique:
        raise ValueError("现实配方formulation_id必须非空唯一")
    if not formulations["performance_claim_status"].astype(str).eq(
        "no_performance_claim"
    ).all():
        raise ValueError("现实配方不得包含性能宣称")
    _required(
        formulation_domain,
        {
            "formulation_id",
            "formulation_domain_floor",
            "weakest_domain_role",
            "formulation_applicability_status",
            "ml_prediction_status",
        },
        "现实配方适用域",
    )
    if not formulation_domain["formulation_id"].is_unique:
        raise ValueError("现实配方适用域formulation_id不唯一")
    formula_ids = set(formulations["formulation_id"].astype(str))
    domain_ids = set(formulation_domain["formulation_id"].astype(str))
    if formula_ids != domain_ids:
        raise ValueError("适用域与配方ID集合不一致")
    domain_columns = [
        "formulation_id",
        "formulation_domain_floor",
        "weakest_domain_role",
        "formulation_applicability_status",
        "ml_prediction_status",
    ]
    enriched = formulations.merge(
        formulation_domain[domain_columns],
        on="formulation_id",
        how="left",
        validate="one_to_one",
    )
    scientific_features = [
        column
        for column in component_descriptors.columns
        if column not in COMPONENT_METADATA
        and pd.api.types.is_numeric_dtype(component_descriptors[column])
    ]
    if not scientific_features:
        raise ValueError("现实构件没有可连接的数值量化描述符")
    result = aggregate_formulation_features(
        enriched,
        component_descriptors,
        component_id_column="component_id",
        role_id_columns=ROLE_ID_COLUMNS,
        feature_columns=scientific_features,
        status_column="calculation_status",
        uncertainty_columns=("conformer_uncertainty",),
        formulation_columns=list(enriched.columns),
    )
    indexed = component_descriptors.set_index("component_id", drop=False)
    for role, id_column in ROLE_ID_COLUMNS.items():
        result[f"{role}__descriptor_fidelity"] = enriched[id_column].map(
            indexed["descriptor_fidelity"]
        ).to_numpy()
        result[f"{role}__representation_scope"] = enriched[id_column].map(
            indexed["representation_scope"]
        ).to_numpy()
    domain_ready = result["formulation_applicability_status"].eq(
        "component_structures_within_or_near_domain"
    )
    descriptor_ready = result["descriptor_join_status"].eq("ready")
    result["screening_input_status"] = np.select(
        [~descriptor_ready, ~domain_ready],
        [
            "closed_quantum_descriptor_gate",
            "ready_for_quantum_proxy_screen_outside_gnn_domain",
        ],
        default="ready_for_quantum_proxy_screen",
    )
    result["quantum_descriptor_screen_permission"] = np.where(
        descriptor_ready, "allowed_with_fidelity_labels", "blocked"
    )
    result["gnn_prediction_permission"] = np.where(
        domain_ready,
        "diagnostic_only_requires_multicomponent_model",
        "blocked_outside_training_structure_domain",
    )
    result["validation_priority"] = np.where(
        domain_ready, "standard", "high_out_of_domain"
    )
    result["quantum_descriptor_scope"] = (
        "mixed_crest_ensemble_and_single_oligomer_proxy"
    )
    result["performance_claim_status"] = "no_performance_claim"
    if len(result) != len(formulations) or not result["formulation_id"].is_unique:
        raise ValueError("现实配方量化连接发生行膨胀或ID丢失")
    return result.reset_index(drop=True)


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8", float_format="%.12g")
    temporary.replace(path)


def _atomic_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_release(
    discrete_path: Path,
    ptmg_path: Path,
    components_path: Path,
    formulations_path: Path,
    domain_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    inputs = {
        "discrete_component_ensembles": discrete_path,
        "ptmg_single_conformer_proxies": ptmg_path,
        "commercial_components": components_path,
        "commercial_formulations": formulations_path,
        "formulation_applicability_domain": domain_path,
    }
    for label, path in inputs.items():
        if not path.is_file():
            raise ValueError(f"输入不存在: {label}={path}")
    components = build_component_descriptor_table(
        pd.read_csv(discrete_path),
        pd.read_csv(ptmg_path),
        pd.read_csv(components_path),
    )
    formulations = build_formulation_descriptor_table(
        pd.read_csv(formulations_path),
        pd.read_csv(domain_path),
        components,
    )
    component_output = output_root / "构件量化描述符.csv"
    formulation_output = output_root / "配方量化描述符.csv"
    _atomic_csv(components, component_output)
    _atomic_csv(formulations, formulation_output)
    manifest = {
        "release_id": release_id,
        "status": "completed",
        "counts": {
            "components": len(components),
            "formulations": len(formulations),
        },
        "status_counts": formulations["screening_input_status"]
        .value_counts()
        .astype(int)
        .to_dict(),
        "descriptor_fidelity_counts": components["descriptor_fidelity"]
        .value_counts()
        .astype(int)
        .to_dict(),
        "inputs": {
            label: {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
            for label, path in inputs.items()
        },
        "files": {
            component_output.name: {
                "bytes": component_output.stat().st_size,
                "sha256": sha256(component_output),
            },
            formulation_output.name: {
                "bytes": formulation_output.stat().st_size,
                "sha256": sha256(formulation_output),
            },
        },
        "interpretation_limit": (
            "quantum proxy screening only; PTMG uses one deterministic oligomer chain; "
            "no direct TPU mechanical-performance claim"
        ),
    }
    _atomic_json(manifest, output_root / "量化描述符发布清单.json")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--离散构件",
        type=Path,
        default=ROOT / "计算" / "现实xTB系综" / "聚合" / "构件系综描述符.csv",
    )
    parser.add_argument(
        "--PTMG代理",
        type=Path,
        default=ROOT / "计算" / "现实PTMG_xTB" / "聚合" / "构件系综描述符.csv",
    )
    parser.add_argument(
        "--现实构件", type=Path, default=ROOT / "数据" / "现实库" / "构件.csv"
    )
    parser.add_argument(
        "--现实配方", type=Path, default=ROOT / "数据" / "现实库" / "配方.csv"
    )
    parser.add_argument(
        "--配方适用域",
        type=Path,
        default=ROOT / "数据" / "现实库" / "配方适用域.csv",
    )
    parser.add_argument(
        "--输出目录", type=Path, default=ROOT / "数据" / "现实库"
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-quantum-formulations-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.离散构件,
        args.PTMG代理,
        args.现实构件,
        args.现实配方,
        args.配方适用域,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
