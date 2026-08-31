"""发布MDPI三配比PU分子动力学表格中通过质量门的79条Gold-C描述符。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "MDPI_MDI聚醚双组分PU分子动力学"
)
OBSERVATIONS = SOURCE_DIR / "计算观测清单.tsv"
SYSTEMS = SOURCE_DIR / "计算体系清单.tsv"
ARTICLE = SOURCE_DIR / "PMC全文.xml"
OUT = ROOT / "结果" / "定向筛选" / "MDPI_PU分子动力学描述符.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "MDPI_PU分子动力学发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _temperature(condition: object) -> int:
    match = re.search(r"temperature=(\d+) K", str(condition))
    if not match:
        raise ValueError(f"无法解析温度条件：{condition}")
    return int(match.group(1))


def _ratio(composition: object) -> float | None:
    match = re.search(r"mass ratio=1:(0\.\d+)", str(composition))
    return float(match.group(1)) if match else None


def build_release() -> pd.DataFrame:
    observations = pd.read_csv(OBSERVATIONS, sep="\t")
    systems = pd.read_csv(SYSTEMS, sep="\t")
    candidates = observations[observations["target_candidate"].eq(True)].copy()
    system_columns = systems[
        [
            "system_id",
            "chemistry_or_material",
            "composition_or_condition",
            "mapping_type",
            "method_or_solver",
            "protocol_branch_count",
            "reported_seed_replicate_count",
        ]
    ]
    frame = candidates.merge(system_columns, on="system_id", how="left", validate="many_to_one")
    if frame["method_or_solver"].isna().any():
        raise ValueError("MDPI PU计算观测缺少体系映射")
    frame["temperature_K"] = frame["condition"].map(_temperature)
    frame["polyol_to_MDI_mass_ratio_MDI_part"] = frame[
        "composition_or_condition"
    ].map(_ratio)
    frame["source_id"] = "source_mdpi_ma16031006"
    frame["model_admission_layer"] = "md_computed_descriptor_reference"
    frame["usage_mode"] = "descriptor_pretraining_after_structure_mapping"
    frame["structure_identity_status"] = "polyether_structure_unresolved"
    frame["model_ready"] = False
    frame["training_weight"] = 0.0
    frame["direct_toughness_label"] = False
    frame["reported_seed_replicate_count_known"] = frame[
        "reported_seed_replicate_count"
    ].notna()
    frame["license"] = "CC-BY-4.0"
    frame["citation_keys"] = "reference-125"
    columns = [
        "source_id",
        "record_id",
        "system_id",
        "chemistry_or_material",
        "composition_or_condition",
        "polyol_to_MDI_mass_ratio_MDI_part",
        "temperature_K",
        "property_name",
        "value",
        "unit",
        "quality_evidence",
        "source_location",
        "method_or_solver",
        "protocol_branch_count",
        "reported_seed_replicate_count_known",
        "decision",
        "future_weight_ceiling",
        "split_group",
        "model_admission_layer",
        "usage_mode",
        "structure_identity_status",
        "model_ready",
        "training_weight",
        "direct_toughness_label",
        "license",
        "citation_keys",
    ]
    return frame[columns].sort_values(
        ["system_id", "temperature_K", "property_name", "record_id"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    all_rows = pd.read_csv(OBSERVATIONS, sep="\t")
    return {
        "release_id": "mdpi_pu_md_descriptors_v1",
        "source": {
            "article_doi": "10.3390/ma16031006",
            "license": "CC-BY-4.0",
            "article_xml_sha256": _sha256(ARTICLE),
        },
        "counts": {
            "source_observation_count": int(len(all_rows)),
            "published_candidate_record_count": int(len(frame)),
            "mixture_formulation_count": 3,
            "shared_polyol_reference_system_count": 1,
            "temperature_condition_count": int(frame["temperature_K"].nunique()),
            "property_count": int(frame["property_name"].nunique()),
            "intermediate_energy_record_count_excluded": int(
                all_rows["decision"].eq("reference_only_intermediate_energy").sum()
            ),
            "definition_duplicate_record_count_excluded": int(
                all_rows["decision"].eq("derived_duplicate_of_lame_mu").sum()
            ),
            "low_fit_quality_record_count_excluded": int(
                all_rows["decision"].eq("hard_zero_low_fit_quality").sum()
            ),
            "published_compact_row_count": int(len(frame)),
        },
        "published_property_counts": {
            str(key): int(value)
            for key, value in frame["property_name"].value_counts().sort_index().items()
        },
        "policy": {
            "raw_trajectories_available": False,
            "reported_independent_seed_count_known": False,
            "model_ready_before_polyether_structure_mapping": False,
            "training_weight_before_mapping": 0.0,
            "direct_toughness_label": False,
            "mu_and_G_double_supervision": False,
        },
        "output_sha256": output_hash,
    }


def write_release(frame: pd.DataFrame) -> None:
    frame.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(_manifest(frame, _sha256(OUT)), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def check_release(frame: pd.DataFrame) -> None:
    if not OUT.exists() or not MANIFEST.exists():
        raise SystemExit("MDPI PU分子动力学发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("MDPI PU分子动力学描述符与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("MDPI PU分子动力学发布清单不一致")
    print("MDPI PU分子动力学检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(json.dumps({"records": len(frame)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
