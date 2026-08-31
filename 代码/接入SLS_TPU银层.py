"""物化EOS TPU 1301 SLS来源中工艺映射不完整的210个银层试样。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd

from 接入DRUM机械回收 import _derive_endpoints
from 接入SLS_TPU工艺力学 import (
    TABLE_DIR,
    _curve_sheet,
    _curves,
    _gold_files,
    _legacy_curves,
    _result_sheet,
    _results,
    _workbook_sheets,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "结果" / "定向筛选" / "SLS_TPU1301银层工艺拉伸端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "SLS_TPU1301银层发布清单.json"
SILVER_FILES = (
    "TPU_1.xls",
    "TPU_10.xls",
    "TPU_1_1 (45°).xlsx",
    "TPU_1_1 (on edge).xlsx",
    "TPU_1_1 (upright).xlsx",
    "TPU_1_1-zstLSy.xls",
    "TPU_1_double_contour.xls",
    "TPU_1_double_contour_45.xls",
    "TPU_1_double_contour__v2_45.xls",
    "TPU_1_double_contour__v2_flat.xls",
    "TPU_1_double_contour__v2_on-edge.xls",
    "TPU_1_double_contour__v2_upright.xls",
    "TPU_1_double_contour__v3_45.xls",
    "TPU_1_double_contour__v3_flat.xls",
    "TPU_1_double_contour__v3_on-edge.xls",
    "TPU_1_double_contour__v3_upright.xls",
    "TPU_1_double_contour_flat.xls",
    "TPU_1_double_contour_on-edge.xls",
    "TPU_1_double_contour_upright.xls",
    "TPU_1_double_contour_v2.xls",
    "TPU_1_double_contour_v3.xls",
    "TPU_1_edge_45.xls",
    "TPU_1_edge__v2_45.xls",
    "TPU_1_edge__v2_flat.xls",
    "TPU_1_edge__v2_on-edge.xls",
    "TPU_1_edge__v2_upright.xls",
    "TPU_1_edge__v3_45.xls",
    "TPU_1_edge__v3_flat.xls",
    "TPU_1_edge__v3_on-edge.xls",
    "TPU_1_edge__v3_upright.xls",
    "TPU_1_edge_flat.xls",
    "TPU_1_edge_on-edge.xls",
    "TPU_1_edge_upright.xls",
    "TPU_2 (45°).xlsx",
    "TPU_2 (on edge).xlsx",
    "TPU_2 (upright).xlsx",
    "TPU_2-N5LUYA.xls",
    "TPU_2.xls",
    "TPU_3-J2I0kh.xls",
    "TPU_3.xls",
    "TPU_4-XGnnvk.xls",
    "TPU_4.xls",
    "TPU_5-GO63nq.xls",
    "TPU_5.xls",
)
SCIENTIFIC_DUPLICATE_FILES_EXCLUDED = (
    "TPU_1_double_contour__v2_flat-nv6xwz.xls",
    "TPU_1_double_contour__v2_on-edge-uyTymf.xls",
)
EXACT_DUPLICATE_XLSX_GROUP_COUNT = 4


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _silver_files() -> list[Path]:
    paths = [TABLE_DIR / name for name in SILVER_FILES]
    if len(paths) != 44 or not all(path.exists() for path in paths):
        raise ValueError("SLS TPU银层canonical工作簿选择不等于44")
    if set(SILVER_FILES) & set(_gold_files()):
        raise ValueError("SLS TPU金银层文件白名单发生交叠")
    return paths


def _curve_sha256(curve: pd.DataFrame) -> str:
    payload = curve.to_csv(
        index=False, header=False, float_format="%.12g", lineterminator="\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _gold_tpu10_hashes() -> set[str]:
    path = TABLE_DIR / "TPU_10sem5.xls"
    sheets = _workbook_sheets(path)
    results = _results(_result_sheet(sheets, path))
    curves = _legacy_curves(sheets, list(results))
    return {_curve_sha256(curve) for _, curve in curves}


def build_release() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    gold_tpu10_hashes = _gold_tpu10_hashes()
    for path in _silver_files():
        condition_id = path.stem
        sheets = _workbook_sheets(path)
        direct_results = _results(_result_sheet(sheets, path))
        curves = (
            _legacy_curves(sheets, list(direct_results))
            if path.suffix.lower() == ".xls"
            else _curves(_curve_sheet(sheets, path))
        )
        if set(sample for sample, _ in curves) != set(direct_results):
            raise ValueError(f"银层结果与曲线试样不一致：{path.name}")
        file_hash = _sha256(path)
        for sample, curve in curves:
            endpoints = _derive_endpoints(curve)
            curve_hash = _curve_sha256(curve)
            cross_gold_duplicate = curve_hash in gold_tpu10_hashes
            condition_quarantined = condition_id == "TPU_2 (45°)"
            source_modulus = direct_results[sample]["source_young_modulus_MPa"]
            modulus_qc = (
                "quarantined_negative_source_modulus"
                if source_modulus < 0
                else "condition_quarantined_due_negative_modulus_replicates"
                if condition_quarantined
                else "valid"
            )
            weight = 0.0 if cross_gold_duplicate or condition_quarantined else 0.15
            rows.append(
                {
                    "source_id": "source_mendeley_wfsm6f9rbn_v1",
                    "material_grade": "EOS TPU 1301",
                    "formulation_id": "EOS TPU 1301",
                    "condition_id": condition_id,
                    "source_workbook_name": path.name,
                    "sample_id": f"silver|{condition_id}|{sample}",
                    "source_sample_label": sample,
                    **direct_results[sample],
                    **endpoints,
                    "curve_sha256": curve_hash,
                    "cross_gold_curve_duplicate": cross_gold_duplicate,
                    "source_young_modulus_qc_status": modulus_qc,
                    "toughness_semantics": (
                        "direct_tensile_curve_area_silver_process_"
                        "not_new_chemistry"
                    ),
                    "material_class": "SLS_printed_thermoplastic_polyurethane",
                    "chemistry_mapping_status": "commercial_grade_known",
                    "process_mapping_status": (
                        "exploratory_condition_code_detailed_mapping_incomplete"
                    ),
                    "target_role": "direct_tensile_toughness_silver_process",
                    "model_admission_layer": "SLS_TPU_process_silver",
                    "usage_mode": "low_weight_process_transfer_only",
                    "sample_weight_ceiling": weight,
                    "process_model_weight_ceiling": 0.0,
                    "split_group": "10.17632/wfsm6f9rbn.1|EOS TPU 1301",
                    "source_locator": (
                        f"{path.relative_to(ROOT).as_posix()}#sample={sample}"
                    ),
                    "source_file_sha256": file_hash,
                    "license": "CC-BY-4.0",
                    "citation_keys": "reference-59",
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["condition_id", "source_sample_label"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    paths = _silver_files()
    return {
        "release_id": "sls_tpu1301_process_tensile_silver_v1",
        "source": {
            "dataset_doi": "10.17632/wfsm6f9rbn.1",
            "license": "CC-BY-4.0",
        },
        "counts": {
            "material_grade_count": 1,
            "silver_process_condition_count": int(frame["condition_id"].nunique()),
            "silver_physical_specimen_count": int(len(frame)),
            "silver_curve_point_count": int(frame["curve_point_count"].sum()),
            "direct_endpoint_scalar_count": int(len(frame) * 5),
            "canonical_legacy_xls_count": sum(
                path.suffix.lower() == ".xls" for path in paths
            ),
            "canonical_direct_xlsx_count": sum(
                path.suffix.lower() == ".xlsx" for path in paths
            ),
            "scientific_duplicate_sequence_count_excluded": len(
                SCIENTIFIC_DUPLICATE_FILES_EXCLUDED
            ),
            "exact_duplicate_xlsx_group_count_excluded": (
                EXACT_DUPLICATE_XLSX_GROUP_COUNT
            ),
            "cross_gold_duplicate_curve_count": int(
                frame["cross_gold_curve_duplicate"].sum()
            ),
            "unique_silver_curve_count": int(frame["curve_sha256"].nunique()),
            "new_unique_curve_count_beyond_gold": int(
                (~frame["cross_gold_curve_duplicate"]).sum()
            ),
            "combined_gold_silver_unique_curve_count": 346,
            "negative_source_modulus_count": int(
                frame["source_young_modulus_qc_status"].eq(
                    "quarantined_negative_source_modulus"
                ).sum()
            ),
            "zero_weight_row_count": int(frame["sample_weight_ceiling"].eq(0).sum()),
            "positive_weight_row_count": int(frame["sample_weight_ceiling"].gt(0).sum()),
            "published_compact_row_count": int(len(frame)),
        },
        "policy": {
            "raw_curves_republished": False,
            "gold_specimens_duplicated": False,
            "individual_dot1_dot2_workbooks_count_as_new_sequences": False,
            "random_suffix_exact_duplicates_count_as_new_sequences": False,
            "process_conditions_increase_material_count": False,
            "cross_source_canonical_material_key": "EOS TPU 1301",
            "sample_weight_ceiling": 0.15,
            "process_model_weight_ceiling_without_mapping": 0.0,
        },
        "inputs": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(path),
            }
            for path in paths
        ],
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
        raise SystemExit("SLS TPU1301银层发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("SLS TPU1301银层端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("SLS TPU1301银层发布清单不一致")
    print("SLS TPU1301银层检查通过")


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
                    "conditions": int(frame["condition_id"].nunique()),
                    "specimens": len(frame),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
