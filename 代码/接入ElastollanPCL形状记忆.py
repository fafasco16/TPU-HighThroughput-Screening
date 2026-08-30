"""物化Elastollan 1154D/CAPA 6500共混物的文献形状记忆端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DIRECTED = ROOT / "结果" / "定向筛选"
OUTPUT = DIRECTED / "ElastollanPCL形状记忆端点.csv"
MANIFEST = DIRECTED / "ElastollanPCL形状记忆发布清单.json"
RELEASE_ID = "elastollan-1154d-pcl-shape-memory-2021-v1"
SOURCE_ID = "JMERD-2021-44-11-197-206"
SOURCE_URL = (
    "https://www.researchgate.net/publication/357355659_"
    "Investigating_Mechanical_and_Shape_Memory_Properties_of_"
    "TPUPCL_Blends_For_Thermoplastic_Splinting_Applications"
)
LICENSE_STATUS = "paper_license_not_verified_numeric_facts_only"
CITATION = "reference-187"
SOURCE_ROWS = (
    (30.0, 70.0, 97.1, 76.8),
    (45.0, 55.0, 90.8, 81.2),
    (60.0, 40.0, 84.5, 85.7),
)
EVIDENCE_TEXT = """Pilehrood, S. S.; Saba, V.; Gorgani-Firuzjaee, S.
Investigating Mechanical and Shape Memory Properties of TPU/PCL Blends for Thermoplastic Splinting Applications.
Journal of Mechanical Engineering Research and Developments 2021, 44(11), 197-206.
Materials: BASF Elastollan 1154D; Perstorp CAPA 6500, Mn=50000 g/mol.
Table 1: PCL-30%TPU Rf=97.1%, Rr=76.8%; PCL-45%TPU Rf=90.8%, Rr=81.2%; PCL-60%TPU Rf=84.5%, Rr=85.7%.
Protocol: 65 C for 180 s; stretch to 50% at 50 mm/min; cool to ambient at fixed strain for 180 s; unload; recover at 65 C.
Processing: melt blend 30 min at 200 C and 100 rpm; compression mold at 190 C.
Source locator: public full text, Table 1, page 206; experimental section, pages 199-200.
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def evidence_sha256() -> str:
    return hashlib.sha256(EVIDENCE_TEXT.encode("utf-8")).hexdigest()


def build_release() -> pd.DataFrame:
    rows = []
    for tpu_fraction, pcl_fraction, fixity, recovery in SOURCE_ROWS:
        material_id = f"Elastollan1154D_{int(tpu_fraction)}wt_PCL_{int(pcl_fraction)}wt"
        rows.append(
            {
                "release_id": RELEASE_ID,
                "source_id": SOURCE_ID,
                "material_id": material_id,
                "material_family": "commercial_TPU_PCL_shape_memory_blend",
                "TPU_grade": "Elastollan 1154D",
                "TPU_supplier": "BASF",
                "TPU_wt_percent": tpu_fraction,
                "PCL_grade": "CAPA 6500",
                "PCL_supplier": "Perstorp",
                "PCL_nominal_Mn_g_mol": 50000.0,
                "PCL_wt_percent": pcl_fraction,
                "chemistry_mapping_status": (
                    "commercial_grade_and_blend_fraction_mapped"
                ),
                "shape_fixity_ratio_percent": fixity,
                "shape_recovery_ratio_percent": recovery,
                "programming_temperature_C": 65.0,
                "programming_hold_s": 180.0,
                "programming_strain_percent": 50.0,
                "programming_crosshead_speed_mm_min": 50.0,
                "fixing_temperature": "ambient",
                "fixing_hold_s": 180.0,
                "recovery_temperature_C": 65.0,
                "mixing_temperature_C": 200.0,
                "mixing_time_min": 30.0,
                "mixing_speed_rpm": 100.0,
                "compression_molding_temperature_C": 190.0,
                "direct_shape_fixity_available": True,
                "direct_shape_recovery_available": True,
                "replicate_count": pd.NA,
                "uncertainty_status": "not_reported_in_table",
                "model_admission_layer": "core_tpu_blend_published_summary",
                "usage_mode": "direct_shape_memory_summary_supervision",
                "future_weight_ceiling": 0.45,
                "split_group": f"{SOURCE_ID}|{material_id}",
                "source_locator": f"{SOURCE_URL}#Table-1-page-206",
                "license_status": LICENSE_STATUS,
                "citation_keys": CITATION,
            }
        )
    return pd.DataFrame(rows)


def write_release(frame: pd.DataFrame) -> None:
    DIRECTED.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUTPUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    payload = {
        "release_id": RELEASE_ID,
        "counts": {
            "blend_formulation_count": len(frame),
            "direct_shape_fixity_rows": int(
                frame["shape_fixity_ratio_percent"].notna().sum()
            ),
            "direct_shape_recovery_rows": int(
                frame["shape_recovery_ratio_percent"].notna().sum()
            ),
            "published_compact_row_count": len(frame),
        },
        "source": {
            "source_id": SOURCE_ID,
            "url": SOURCE_URL,
            "title": (
                "Investigating Mechanical and Shape Memory Properties of "
                "TPU/PCL Blends for Thermoplastic Splinting Applications"
            ),
            "table_locator": "Table 1, page 206",
            "accessed_on": "2026-08-31",
            "license_status": LICENSE_STATUS,
            "raw_paper_redistributed": False,
            "evidence_sha256": evidence_sha256(),
        },
        "policy": {
            "published_summary_not_raw_curve": True,
            "replicate_count_known": False,
            "uncertainty_known": False,
            "exact_TPU_grade_known": True,
            "exact_TPU_molecular_composition_known": False,
        },
        "output": {
            "path": OUTPUT.relative_to(ROOT).as_posix(),
            "bytes": OUTPUT.stat().st_size,
            "sha256": _sha256(OUTPUT),
        },
    }
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_release(frame: pd.DataFrame) -> None:
    with tempfile.TemporaryDirectory(prefix="elastollan-pcl-check-") as directory:
        temporary = Path(directory) / OUTPUT.name
        frame.to_csv(
            temporary, index=False, encoding="utf-8-sig", lineterminator="\n"
        )
        if _sha256(temporary) != _sha256(OUTPUT):
            raise SystemExit("Elastollan/PCL形状记忆输出不一致")
    print("Elastollan/PCL形状记忆数据检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(
            json.dumps(
                {
                    "blend_formulations": len(frame),
                    "direct_shape_recovery_rows": int(
                        frame["shape_recovery_ratio_percent"].notna().sum()
                    ),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
