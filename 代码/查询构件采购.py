"""运行统一采购接口并将结果合并回构件采购摘要。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 采购接口 import (
    EMoleculesLinkAdapter,
    EvidenceCache,
    MolBloomAdapter,
    PubChemVendorAdapter,
    SmallWorldAdapter,
    load_config,
    merge_evidence,
    package_versions,
    query_with_cache,
    sha256_file,
    ComponentQuery,
)


ROOT = Path(__file__).resolve().parents[1]


def load_components(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "candidate_id" not in frame.columns and "component_id" in frame.columns:
        frame = frame.rename(columns={"component_id": "candidate_id"})
    required = {"candidate_id", "canonical_smiles"}
    if required.difference(frame.columns):
        raise ValueError(f"采购查询输入缺少字段: {sorted(required.difference(frame.columns))}")
    frame = frame.loc[
        frame["canonical_smiles"].notna()
        & frame["canonical_smiles"].astype(str).str.strip().ne("")
    ].copy()
    if frame.empty:
        raise ValueError("采购查询输入没有可按离散结构查询的构件")
    if frame["candidate_id"].isna().any() or not frame["candidate_id"].is_unique:
        raise ValueError("采购查询输入candidate_id必须非空且唯一")
    components = []
    for row in frame.itertuples(index=False):
        query = ComponentQuery.from_values(row.candidate_id, row.canonical_smiles)
        components.append(
            {
                **row._asdict(),
                "canonical_smiles": query.canonical_smiles,
                "inchi_key": query.inchi_key,
            }
        )
    return pd.DataFrame(components)


def existing_pubchem_evidence(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        return []
    frame = pd.read_csv(source)
    cid_column = "pubchem_cid" if "pubchem_cid" in frame.columns else "pubchem_cid_x" if "pubchem_cid_x" in frame.columns else ""
    count_column = (
        "pubchem_vendor_record_count"
        if "pubchem_vendor_record_count" in frame.columns
        else "pubchem_vendor_record_count_x"
        if "pubchem_vendor_record_count_x" in frame.columns
        else ""
    )
    required = {
        "candidate_id",
        "inchi_key",
        "query_status",
        "pubchem_vendor_names",
        "queried_utc",
    }
    if required.difference(frame.columns) or not cid_column or not count_column:
        return []
    rows = []
    for row in frame.itertuples(index=False):
        raw_count = getattr(row, count_column)
        raw_cid = getattr(row, cid_column)
        count = int(raw_count) if pd.notna(raw_count) else 0
        cid = str(int(raw_cid)) if pd.notna(raw_cid) else ""
        rows.append(
            {
                "component_id": row.candidate_id,
                "inchi_key": row.inchi_key,
                "interface_name": "pubchem_vendor",
                "query_status": "completed" if row.query_status == "completed" else str(row.query_status),
                "evidence_level": "vendor_directory",
                "query_value": cid,
                "result_count": count,
                "result_summary": "" if pd.isna(row.pubchem_vendor_names) else str(row.pubchem_vendor_names),
                "result_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}" if cid else "",
                "queried_utc": str(row.queried_utc),
                "cache_key": "",
                "limitations": "从既有PubChem查询复用；目录不代表实时库存",
            }
        )
    return rows


def run_interfaces(
    input_path: str | Path,
    config_path: str | Path,
    evidence_path: str | Path,
    summary_path: str | Path,
    manifest_path: str | Path,
    *,
    refresh: bool = False,
    enable_smallworld: bool = False,
) -> dict[str, Any]:
    config = load_config(config_path)
    components = load_components(input_path)
    cache_path = ROOT / str(config.get("cache_path", "候选/采购接口缓存.json"))
    cache = EvidenceCache(cache_path)
    adapters = []
    if config["pubchem_vendor"].get("enabled", True):
        adapters.append(PubChemVendorAdapter(timeout_seconds=config["pubchem_vendor"].get("timeout_seconds", 30)))
    if config["molbloom"].get("enabled", True):
        adapters.append(
            MolBloomAdapter(
                catalog=config["molbloom"].get("catalog", "zinc-instock"),
                canonicalize=config["molbloom"].get("canonicalize", True),
                check_common=config["molbloom"].get("check_common", True),
            )
        )
    if config["emolecules_link"].get("enabled", True):
        adapters.append(EMoleculesLinkAdapter())
    smallworld_limit = 0
    if enable_smallworld:
        smallworld_limit = int(config["smallworld"].get("max_candidates", 8))
        adapters.append(
            SmallWorldAdapter(
                distance=config["smallworld"].get("distance", 0),
                database=config["smallworld"].get("database", "zinc"),
            )
        )
    evidence_rows = []
    cache_hits = 0
    existing_rows = (
        existing_pubchem_evidence(summary_path)
        if config["pubchem_vendor"].get("reuse_existing_summary", True) and not refresh
        else []
    )
    existing_by_component = {row["component_id"]: row for row in existing_rows}
    for position, row in enumerate(components.itertuples(index=False), start=1):
        component = ComponentQuery(row.candidate_id, row.canonical_smiles, row.inchi_key)
        for adapter in adapters:
            if adapter.name == "smallworld" and position > smallworld_limit:
                continue
            if adapter.name == "pubchem_vendor" and component.component_id in existing_by_component:
                evidence_rows.append(existing_by_component[component.component_id])
                cache_hits += 1
                continue
            result, cached = query_with_cache(adapter, component, cache, refresh=refresh)
            evidence_rows.append(result.to_dict())
            cache_hits += int(cached)
            delay = 0.0
            if adapter.name == "pubchem_vendor":
                delay = float(config["pubchem_vendor"].get("minimum_delay_seconds", 0.25))
            elif adapter.name == "smallworld":
                delay = float(config["smallworld"].get("minimum_delay_seconds", 5.0))
            if delay and not cached:
                time.sleep(delay)
        if position % 10 == 0 or position == len(components):
            print(f"统一采购接口进度: {position}/{len(components)}", flush=True)
    cache.save()
    evidence = pd.DataFrame(evidence_rows)
    output_evidence = Path(evidence_path)
    output_evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.to_csv(output_evidence, index=False, encoding="utf-8")
    summary = merge_evidence(
        components,
        evidence,
        experiment_release_status=config["aggregation"]["experiment_release_status"],
    )
    output_summary = Path(summary_path)
    temporary = output_summary.with_name(f".{output_summary.name}.tmp")
    summary.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(output_summary)
    manifest = {
        "status": "completed",
        "input": {"path": str(input_path), "sha256": sha256_file(input_path), "rows": len(components)},
        "configuration": {"path": str(config_path), "sha256": sha256_file(config_path)},
        "outputs": {
            "evidence": {"path": str(output_evidence), "sha256": sha256_file(output_evidence), "rows": len(evidence)},
            "summary": {"path": str(output_summary), "sha256": sha256_file(output_summary), "rows": len(summary)},
        },
        "interfaces": sorted(evidence["interface_name"].unique().tolist()),
        "cache_hits": cache_hits,
        "refresh": refresh,
        "smallworld_enabled": enable_smallworld,
        "package_versions": package_versions(),
        "query_status_counts": evidence.groupby(["interface_name", "query_status"]).size().astype(int).to_dict(),
        "catalog_status_counts": summary["unified_catalog_status"].value_counts().astype(int).to_dict(),
        "experiment_release_status": config["aggregation"]["experiment_release_status"],
    }
    # JSON不支持tuple键，改成稳定字符串。
    manifest["query_status_counts"] = {
        f"{key[0]}::{key[1]}": value for key, value in manifest["query_status_counts"].items()
    }
    output_manifest = Path(manifest_path)
    output_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--输入", type=Path, default=ROOT / "候选" / "当前82构件实验门审计.csv")
    parser.add_argument("--配置", type=Path, default=ROOT / "配置" / "采购接口.yaml")
    parser.add_argument("--证据输出", type=Path, default=ROOT / "候选" / "采购接口证据.csv")
    parser.add_argument("--摘要输出", type=Path, default=ROOT / "候选" / "当前82构件采购查询.csv")
    parser.add_argument("--清单输出", type=Path, default=ROOT / "候选" / "采购接口运行清单.json")
    parser.add_argument("--刷新", action="store_true")
    parser.add_argument("--启用SmallWorld", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = run_interfaces(
        args.输入,
        args.配置,
        args.证据输出,
        args.摘要输出,
        args.清单输出,
        refresh=args.刷新,
        enable_smallworld=args.启用SmallWorld,
    )
    print(json.dumps({"status": manifest["status"], "interfaces": manifest["interfaces"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
