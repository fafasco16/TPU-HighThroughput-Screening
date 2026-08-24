"""为真实TPU实验建立商业可得、可合成且fail-closed的构件与组合视图。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_COLUMNS = (
    "stable_component_id",
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
    "synthesis_feasibility_status",
    "commercial_evidence_status",
    "ehs_review_status",
    "source_scope",
    "priority_class",
    "notes",
)


def load_config(path: str | Path) -> dict[str, Any]:
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    required = {"allowed_identity_kind", "required_status", "formulation_grid", "release_policy"}
    if not isinstance(config, dict) or required.difference(config):
        raise ValueError(f"实验候选硬门配置缺少分区: {sorted(required.difference(config or {}))}")
    return config


def _clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def validate_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(EVIDENCE_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(f"商用构件证据缺少字段: {missing}")
    output = frame.copy()
    for column in EVIDENCE_COLUMNS:
        if column != "nominal_mn_g_mol":
            output[column] = output[column].map(_clean_text)
    if output["stable_component_id"].eq("").any() or not output["stable_component_id"].is_unique:
        raise ValueError("stable_component_id必须非空且唯一")
    if output["role"].isin(["diisocyanate", "chain_extender"]).any():
        discrete = output["role"].isin(["diisocyanate", "chain_extender"])
        if output.loc[discrete, "canonical_smiles"].eq("").any():
            raise ValueError("离散二异氰酸酯/扩链剂必须提供SMILES")
        invalid = output.loc[discrete, "canonical_smiles"].map(Chem.MolFromSmiles).isna()
        if invalid.any():
            raise ValueError("商用构件证据含RDKit无法解析的SMILES")
    macro = output["role"].eq("macrodiol")
    if macro.any():
        mn = pd.to_numeric(output.loc[macro, "nominal_mn_g_mol"], errors="coerce")
        if mn.isna().any() or (mn <= 0).any() or output.loc[macro, "repeat_unit"].eq("").any():
            raise ValueError("商业宏二醇必须提供正Mn和重复单元说明")
    return output


def apply_component_gate(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    evidence = validate_evidence(frame)
    required_status = config["required_status"]
    pass_label = str(config["release_policy"]["component_gate_pass"])
    statuses: list[str] = []
    reasons: list[str] = []
    for row in evidence.itertuples(index=False):
        problems: list[str] = []
        allowed = set(config["allowed_identity_kind"].get(row.role, []))
        if row.identity_kind not in allowed:
            problems.append("identity_kind")
        for field, expected in required_status.items():
            if getattr(row, field) != expected:
                problems.append(field)
        for field in ("preferred_name", "cas_or_product_grade", "evidence_url", "accessed_date"):
            if not _clean_text(getattr(row, field)):
                problems.append(field)
        statuses.append("blocked" if problems else pass_label)
        reasons.append(";".join(sorted(set(problems))) if problems else "all_component_planning_gates_passed")
    output = evidence.copy()
    output["experimental_gate_status"] = statuses
    output["experimental_gate_reason"] = reasons
    output["experiment_release_status"] = str(
        config["release_policy"]["experiment_release_status"]
    )
    return output


def _stable_id(*parts: Any) -> str:
    payload = "|".join(map(str, parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def build_experimental_combinations(
    pool: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    pass_label = str(config["release_policy"]["component_gate_pass"])
    passed = pool.loc[pool["experimental_gate_status"].eq(pass_label)].copy()
    roles = {role: passed.loc[passed["role"].eq(role)].copy() for role in (
        "diisocyanate", "macrodiol", "chain_extender"
    )}
    missing = [role for role, rows in roles.items() if rows.empty]
    if missing:
        raise ValueError(f"缺少已通过构件角色: {missing}")
    grid = config["formulation_grid"]
    hard_segments = [float(value) for value in grid["hard_segment_mass_fraction_target"]]
    ratios = [float(value) for value in grid["nco_oh_ratio_target"]]
    if not hard_segments or not ratios or not all(0 < x < 1 for x in hard_segments):
        raise ValueError("配方网格不合法")
    rows: list[dict[str, Any]] = []
    for _, dii in roles["diisocyanate"].sort_values("stable_component_id").iterrows():
        for _, macro in roles["macrodiol"].sort_values("stable_component_id").iterrows():
            for _, extender in roles["chain_extender"].sort_values("stable_component_id").iterrows():
                base_id = f"commercial_system_{_stable_id(dii.stable_component_id, macro.stable_component_id, extender.stable_component_id)}"
                priority = {
                    "commercial_mdi_44": 1,
                    "commercial_ipdi": 2,
                    "commercial_h12mdi": 3,
                    "commercial_hdi": 4,
                }.get(dii.stable_component_id, 9)
                for hard_segment in hard_segments:
                    for ratio in ratios:
                        is_tier1 = (
                            dii.stable_component_id in {"commercial_mdi_44", "commercial_ipdi"}
                            and math.isclose(ratio, 1.02)
                            and (
                                (math.isclose(float(macro.nominal_mn_g_mol), 1000.0) and math.isclose(hard_segment, 0.45))
                                or (math.isclose(float(macro.nominal_mn_g_mol), 2000.0) and math.isclose(hard_segment, 0.35))
                            )
                        )
                        planning_tier = (
                            "tier1_small_control_matrix"
                            if is_tier1
                            else "tier2_control_grid"
                            if priority <= 2
                            else "tier3_commercial_comparison"
                        )
                        rows.append(
                            {
                                "formulation_id": f"{base_id}_{_stable_id(hard_segment, ratio)}",
                                "base_system_id": base_id,
                                "combination_id": base_id,
                                "baseline_priority": priority,
                                "planning_tier": planning_tier,
                                "diisocyanate_id": dii.stable_component_id,
                                "diisocyanate_name": dii.preferred_name,
                                "diisocyanate_smiles": dii.canonical_smiles,
                                "diisocyanate_cas_or_grade": dii.cas_or_product_grade,
                                "diisocyanate_evidence_url": dii.evidence_url,
                                "macrodiol_id": macro.stable_component_id,
                                "macrodiol_name": macro.preferred_name,
                                "macrodiol_identity_kind": macro.identity_kind,
                                "macrodiol_smiles": macro.canonical_smiles,
                                "macrodiol_repeat_unit": macro.repeat_unit,
                                "macrodiol_nominal_mn_g_mol": float(macro.nominal_mn_g_mol),
                                "macrodiol_cas_or_grade": macro.cas_or_product_grade,
                                "macrodiol_evidence_url": macro.evidence_url,
                                "chain_extender_id": extender.stable_component_id,
                                "chain_extender_name": extender.preferred_name,
                                "chain_extender_smiles": extender.canonical_smiles,
                                "chain_extender_cas_or_grade": extender.cas_or_product_grade,
                                "chain_extender_evidence_url": extender.evidence_url,
                                "component_ids": ";".join(
                                    [dii.stable_component_id, macro.stable_component_id, extender.stable_component_id]
                                ),
                                "hard_segment_mass_fraction_target": hard_segment,
                                "nco_oh_ratio_target": ratio,
                                "novelty_role": "commercial_control_or_comparison",
                                "procurement_review_status": "catalog_evidence_found_quote_required",
                                "sds_review_status": "required_before_order_or_use",
                                "experiment_release_status": config["release_policy"]["experiment_release_status"],
                                "performance_claim_status": "no_performance_claim",
                            }
                        )
    output = pd.DataFrame(rows).sort_values(
        ["baseline_priority", "macrodiol_nominal_mn_g_mol", "hard_segment_mass_fraction_target", "nco_oh_ratio_target"]
    ).reset_index(drop=True)
    if not output["formulation_id"].is_unique:
        raise RuntimeError("实验组合ID不唯一")
    return output


def parse_pubchem_vendors(payload: Mapping[str, Any]) -> tuple[int, list[str]]:
    categories = payload.get("SourceCategories", {}).get("Categories", [])
    vendor_category = next(
        (item for item in categories if _clean_text(item.get("Category")).casefold() == "chemical vendors"),
        None,
    )
    if vendor_category is None:
        return 0, []
    sources = vendor_category.get("Sources", [])
    names = sorted(
        {
            _clean_text(source.get("SourceName") or source.get("Name"))
            for source in sources
            if _clean_text(source.get("SourceName") or source.get("Name"))
        }
    )
    return len(sources), names


def _read_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "TPU-commercial-screen/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def query_pubchem_availability(smiles: str, *, timeout: float = 30.0) -> dict[str, Any]:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"query_status": "invalid_smiles"}
    inchikey = Chem.MolToInchiKey(mol)
    try:
        cid_payload = _read_json(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/{inchikey}/cids/JSON/",
            timeout,
        )
        cids = cid_payload.get("IdentifierList", {}).get("CID", [])
        if not cids:
            return {"query_status": "pubchem_not_found", "inchi_key": inchikey}
        cid = int(cids[0])
        vendor_payload = _read_json(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/categories/compound/{cid}/JSON/?response_type=view",
            timeout,
        )
        vendor_records, vendor_names = parse_pubchem_vendors(vendor_payload)
        return {
            "query_status": "completed",
            "inchi_key": inchikey,
            "pubchem_cid": cid,
            "pubchem_vendor_record_count": vendor_records,
            "pubchem_distinct_vendor_count": len(vendor_names),
            "pubchem_vendor_names": ";".join(vendor_names),
            "catalog_prefilter_status": "catalog_index_hit" if vendor_records else "no_vendor_index_hit",
        }
    except urllib.error.HTTPError as error:
        status = "pubchem_not_found" if error.code == 404 else f"http_error_{error.code}"
        return {"query_status": status, "inchi_key": inchikey}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return {"query_status": f"query_error_{type(error).__name__}", "inchi_key": inchikey}


def build_current82_audit(
    stage_path: str | Path,
    task_path: str | Path,
    query_path: str | Path | None = None,
) -> pd.DataFrame:
    stage = pd.read_csv(stage_path)
    tasks = pd.read_csv(task_path)
    output = stage[["candidate_id", "component_role", "ensemble_status"]].merge(
        tasks[["candidate_id", "component_role", "task_index", "canonical_smiles", "geometry_status"]],
        on=["candidate_id", "component_role"],
        how="left",
        validate="one_to_one",
    )
    output["inchi_key"] = output["canonical_smiles"].map(
        lambda value: Chem.MolToInchiKey(Chem.MolFromSmiles(value))
    )
    output["molecular_formula"] = output["canonical_smiles"].map(
        lambda value: rdMolDescriptors.CalcMolFormula(Chem.MolFromSmiles(value))
    )
    output["rdkit_mol_weight"] = output["canonical_smiles"].map(
        lambda value: float(Descriptors.MolWt(Chem.MolFromSmiles(value)))
    )
    if query_path is not None and Path(query_path).is_file():
        query = pd.read_csv(query_path)
        columns = [
            "inchi_key", "query_status", "pubchem_cid", "pubchem_vendor_record_count",
            "pubchem_distinct_vendor_count", "pubchem_vendor_names", "catalog_prefilter_status",
            "queried_utc",
        ]
        output = output.merge(query[columns], on="inchi_key", how="left", validate="one_to_one")
    output["experimental_gate_status"] = "blocked"
    output["experimental_gate_reason"] = output["component_role"].map(
        {
            "macrodiol_proxy": "structure_proxy_only_not_real_commercial_macrodiol",
            "diisocyanate": "direct_manufacturer_or_catalog_identity_not_verified",
            "chain_extender": "direct_manufacturer_or_catalog_identity_not_verified",
        }
    )
    output["allowed_use"] = "model_training_and_quantum_reference"
    return output.sort_values("task_index").reset_index(drop=True)


def query_current82(audit: pd.DataFrame, output_path: str | Path, *, delay_seconds: float = 0.25) -> pd.DataFrame:
    existing: dict[str, dict[str, Any]] = {}
    path = Path(output_path)
    if path.is_file():
        for row in pd.read_csv(path).to_dict("records"):
            if _clean_text(row.get("inchi_key")):
                existing[_clean_text(row["inchi_key"])] = row
    rows: list[dict[str, Any]] = []
    records = audit[["candidate_id", "component_role", "canonical_smiles", "inchi_key"]].to_dict("records")
    for index, record in enumerate(records, start=1):
        key = record["inchi_key"]
        if key in existing and existing[key].get("query_status") == "completed":
            result = existing[key]
        else:
            result = {**record, **query_pubchem_availability(record["canonical_smiles"])}
            result["queried_utc"] = pd.Timestamp.now(tz="UTC").isoformat()
            time.sleep(max(delay_seconds, 0.0))
        rows.append(result)
        if index % 10 == 0 or index == len(records):
            checkpoint = pd.DataFrame(rows).sort_values(["component_role", "candidate_id"]).reset_index(drop=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.to_csv(path, index=False, encoding="utf-8")
            print(f"PubChem采购预筛进度: {index}/{len(records)}", flush=True)
    output = pd.DataFrame(rows).sort_values(["component_role", "candidate_id"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(path, index=False, encoding="utf-8")
    return output


def write_outputs(
    evidence_path: str | Path,
    config_path: str | Path,
    stage_path: str | Path,
    task_path: str | Path,
    query_path: str | Path,
    pool_path: str | Path,
    audit_path: str | Path,
    combination_path: str | Path,
    *,
    run_query: bool,
) -> dict[str, int]:
    config = load_config(config_path)
    evidence = apply_component_gate(pd.read_csv(evidence_path), config)
    audit = build_current82_audit(stage_path, task_path, query_path if Path(query_path).is_file() else None)
    if run_query:
        query_current82(audit, query_path)
        audit = build_current82_audit(stage_path, task_path, query_path)
    combinations = build_experimental_combinations(evidence, config)
    for frame, path in ((evidence, pool_path), (audit, audit_path), (combinations, combination_path)):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(destination, index=False, encoding="utf-8")
    return {
        "commercial_components": len(evidence),
        "commercial_components_passed": int(evidence["experimental_gate_status"].eq("passed_for_planning").sum()),
        "current82_audited": len(audit),
        "current82_experiment_passed": int(audit["experimental_gate_status"].eq("passed_for_planning").sum()),
        "experimental_formulations": len(combinations),
        "base_systems": int(combinations["base_system_id"].nunique()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--查询PubChem", action="store_true")
    parser.add_argument("--证据", type=Path, default=ROOT / "候选" / "商用构件证据.csv")
    parser.add_argument("--配置", type=Path, default=ROOT / "配置" / "实验候选硬门.yaml")
    parser.add_argument("--阶段构件", type=Path, default=ROOT / "tmp" / "xTB构件级系综描述符_stage82.csv")
    parser.add_argument("--任务清单", type=Path, default=ROOT / "计算" / "DFT任务清单.csv")
    parser.add_argument("--查询输出", type=Path, default=ROOT / "候选" / "当前82构件采购查询.csv")
    parser.add_argument("--构件输出", type=Path, default=ROOT / "候选" / "实验可行构件.csv")
    parser.add_argument("--审计输出", type=Path, default=ROOT / "候选" / "当前82构件实验门审计.csv")
    parser.add_argument("--组合输出", type=Path, default=ROOT / "候选" / "实验合理组合.csv")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    counts = write_outputs(
        args.证据,
        args.配置,
        args.阶段构件,
        args.任务清单,
        args.查询输出,
        args.构件输出,
        args.审计输出,
        args.组合输出,
        run_query=args.查询PubChem,
    )
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
