"""为6条现实实验短名单登记开放论文/专利先例与新颖性声明门。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SEARCH_DATE = "2026-08-25"
NOVELTY_RECORDS = [
    {
        "formulation_id": "commercial_system_59ebf4f5a2e01a54_d4bd65fe8c7c0e5c",
        "novelty_screen_status": "known_reference_family_control",
        "exact_or_adjacent_prior_art": "exact_component_family_MDI_PTMG_BDO",
        "primary_evidence_url": "https://doi.org/10.1002/polb.23053",
        "secondary_evidence_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9080844/",
        "search_query": "4,4'-MDI PTMG BDO thermoplastic polyurethane",
        "interpretation": "MDI/PTMG/BDO is established prior art and is retained only as a calibration control.",
    },
    {
        "formulation_id": "commercial_system_8f78b79e85d09a49_aa4e010292f6d778",
        "novelty_screen_status": "known_reference_family_control",
        "exact_or_adjacent_prior_art": "exact_component_family_MDI_PTMG_BDO",
        "primary_evidence_url": "https://doi.org/10.1002/polb.23053",
        "secondary_evidence_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9080844/",
        "search_query": "4,4'-MDI PTMG BDO thermoplastic polyurethane",
        "interpretation": "Composition variation may be useful experimentally but the chemistry family is not novel.",
    },
    {
        "formulation_id": "commercial_system_7faa7e08bccea0b7_aa4e010292f6d778",
        "novelty_screen_status": "known_reference_family_control",
        "exact_or_adjacent_prior_art": "direct_IPDI_PTMG1000_BDO_control_synthesis",
        "primary_evidence_url": "https://doi.org/10.1039/D2QM00259K",
        "secondary_evidence_url": "https://www.rsc.org/suppdata/d2/qm/d2qm00259k/d2qm00259k1.pdf",
        "search_query": "IPDI PTMG BDO polyurethane",
        "interpretation": "A directly documented IPDI/PTMG-1000/BDO control exists; this candidate is a calibration comparator.",
    },
    {
        "formulation_id": "commercial_system_1e9a19535918fee7_707fe304b16717b6",
        "novelty_screen_status": "potential_combination_novelty_closed_database_review_required",
        "exact_or_adjacent_prior_art": "adjacent_PDI_PTMG_diamine_cast_elastomer_patent",
        "primary_evidence_url": "https://patents.google.com/patent/US20250250389A1/en",
        "secondary_evidence_url": "",
        "search_query": "pentamethylene diisocyanate PTMG cyclohexanedimethanol polyurethane",
        "interpretation": "Open search found PDI/PTMG elastomer prior art with a diamine curative, but no exact PDI/PTMG-1400/CHDM record; no novelty claim is allowed without SciFinder/Reaxys and patent-claim review.",
    },
    {
        "formulation_id": "commercial_system_275f68de57f6a031_707fe304b16717b6",
        "novelty_screen_status": "potential_combination_novelty_closed_database_review_required",
        "exact_or_adjacent_prior_art": "adjacent_H12MDI_polyurethane_and_NPG_containing_polyol",
        "primary_evidence_url": "https://doi.org/10.1007/BF01492902",
        "secondary_evidence_url": "",
        "search_query": "H12MDI PTMG neopentyl glycol polyurethane",
        "interpretation": "H12MDI polyurethane and NPG-containing polyol prior art exists, but no exact H12MDI/PTMG-1800/NPG chain-extender record was located in the initial open search.",
    },
    {
        "formulation_id": "commercial_system_50aa43042a8e77b6_707fe304b16717b6",
        "novelty_screen_status": "close_prior_art_specialty_family_deferred",
        "exact_or_adjacent_prior_art": "direct_NDI_HQEE_family_patent_and_NDI_elastomer_literature",
        "primary_evidence_url": "https://patents.google.com/patent/US5599874A/en",
        "secondary_evidence_url": "https://doi.org/10.1177/009524438902100204",
        "search_query": "NDI PTMG HQEE polyurethane elastomer",
        "interpretation": "NDI/HQEE and high-performance NDI elastomer prior art is established; PTMG-650 may differ from disclosed polyols but chemistry-family novelty is weak.",
    },
]


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


def build_novelty_table(shortlist: pd.DataFrame) -> pd.DataFrame:
    evidence = pd.DataFrame(NOVELTY_RECORDS)
    if len(evidence) != 6 or not evidence["formulation_id"].is_unique:
        raise ValueError("新颖性初筛证据必须覆盖6条唯一候选")
    required = {
        "experiment_order",
        "experiment_stage",
        "formulation_id",
        "diisocyanate_name",
        "macrodiol_name",
        "chain_extender_name",
    }
    missing = sorted(required.difference(shortlist.columns))
    if missing:
        raise ValueError(f"新颖性初筛短名单缺字段: {missing}")
    joined = shortlist[list(required)].merge(
        evidence,
        on="formulation_id",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    if len(joined) != 6 or not joined["_merge"].eq("both").all():
        raise ValueError("新颖性初筛未完整连接6条短名单")
    joined = joined.drop(columns="_merge")
    joined["search_date"] = SEARCH_DATE
    joined["search_scope"] = (
        "initial_open_web_doi_publisher_and_patent_search_not_exhaustive"
    )
    joined["exact_novelty_determination"] = "not_determined"
    joined["novelty_claim_permission"] = "blocked_pending_closed_database_and_claim_review"
    joined["performance_claim_status"] = "no_performance_claim"
    return joined.sort_values("experiment_order", kind="stable").reset_index(
        drop=True
    )


def write_release(
    shortlist_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if not shortlist_path.is_file():
        raise ValueError(f"新颖性初筛短名单不存在: {shortlist_path}")
    table = build_novelty_table(pd.read_csv(shortlist_path))
    output_root.mkdir(parents=True, exist_ok=True)
    table_path = output_root / "新颖性开放检索初筛.csv"
    report_path = output_root / "新颖性检索边界.md"
    _atomic_text(table_path, table.to_csv(index=False))
    _atomic_text(
        report_path,
        "\n".join(
            [
                "# 实验短名单新颖性检索边界",
                "",
                "本轮只使用开放论文页、支持信息和专利公开文本做精确三元组合及相邻体系初筛，不是法律意见或穷尽性新颖性检索。",
                "",
                "MDI/PTMG/BDO与IPDI/PTMG/BDO有直接公开先例，明确作为校准对照。NDI/HQEE存在接近专利先例和成熟NDI高性能弹性体文献，化学家族新颖性较弱。PDI/PTMG-1400/CHDM和H12MDI/PTMG-1800/NPG在本轮开放查询未找到精确三元匹配，但已有相邻PDI/PTMG、H12MDI聚氨酯和NPG相关先例。",
                "",
                "任何‘未检出’都不得改写为‘无人研究’。进入论文新体系主张前，必须由人工完成SciFinder/Reaxys、Crossref、Google Patents/EPO/WIPO、专利权利要求及同义词/结构检索，并保存检索式、日期和导出结果。",
                "",
            ]
        ),
    )
    files = [table_path, report_path]
    manifest = {
        "release_id": release_id,
        "status": "open_novelty_prescreen_completed_all_claims_blocked",
        "counts": {
            "candidates": len(table),
            "known_reference_controls": int(
                table["novelty_screen_status"]
                .eq("known_reference_family_control")
                .sum()
            ),
            "potential_combinations_requiring_closed_database": int(
                table["novelty_screen_status"]
                .eq(
                    "potential_combination_novelty_closed_database_review_required"
                )
                .sum()
            ),
            "close_prior_art_deferred": int(
                table["novelty_screen_status"]
                .eq("close_prior_art_specialty_family_deferred")
                .sum()
            ),
        },
        "input": {
            "path": str(shortlist_path),
            "bytes": shortlist_path.stat().st_size,
            "sha256": sha256(shortlist_path),
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "novelty_claim_permission": "blocked_pending_closed_database_and_claim_review",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "新颖性初筛发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--短名单",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "实验短名单" / "实验短名单6.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "实验短名单",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-shortlist-open-novelty-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.短名单, args.输出目录, release_id=args.发布ID
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
