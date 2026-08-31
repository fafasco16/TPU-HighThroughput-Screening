"""物化论文可映射的EOS TPU 1301 SLS工艺拉伸金标准端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd

from 接入DRUM机械回收 import _derive_endpoints


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Mendeley_SLS_TPU工艺力学"
)
TABLE_DIR = SOURCE_DIR / "结构化表格"
SUMMARY = SOURCE_DIR / "内容审计摘要.json"
OUT = ROOT / "结果" / "定向筛选" / "SLS_TPU1301工艺拉伸端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "SLS_TPU1301工艺力学发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _gold_files() -> list[str]:
    payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
    return list(payload["发表论文可映射金标准"]["文件"])


def _readable_path(source_name: str) -> Path:
    source = TABLE_DIR / source_name
    if not source.exists():
        raise FileNotFoundError(f"缺少金标准工作簿：{source.name}")
    return source


def _workbook_sheets(path: Path) -> dict[str, pd.DataFrame]:
    return pd.read_excel(path, sheet_name=None, header=None)


def _curve_sheet(sheets: dict[str, pd.DataFrame], path: Path) -> pd.DataFrame:
    candidates = [frame for name, frame in sheets.items() if "Valeurs" in name]
    if len(candidates) != 1:
        raise ValueError(f"无法唯一识别曲线工作表：{path.name}")
    return candidates[0]


def _result_sheet(sheets: dict[str, pd.DataFrame], path: Path) -> pd.DataFrame:
    candidates = [frame for name, frame in sheets.items() if "sultats" in name]
    if len(candidates) != 1:
        raise ValueError(f"无法唯一识别结果工作表：{path.name}")
    return candidates[0]


def _results(frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    columns = [
        "source_Fmax_MPa",
        "source_strain_at_Fmax_percent",
        "source_fracture_stress_MPa",
        "source_elongation_at_break_percent",
        "source_young_modulus_MPa",
    ]
    result: dict[str, dict[str, float]] = {}
    for _, row in frame.iloc[2:].iterrows():
        if pd.isna(row.iloc[0]):
            continue
        sample = str(row.iloc[0])
        values = pd.to_numeric(row.iloc[1:6], errors="coerce")
        if values.isna().any():
            raise ValueError(f"直接端点缺失：{sample}")
        result[sample] = {
            key: float(value) for key, value in zip(columns, values, strict=True)
        }
    return result


def _curves(frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    sample_headers = frame.iloc[0]
    variable_headers = frame.iloc[1].astype(str)
    samples = []
    for sample in sample_headers.dropna().astype(str).drop_duplicates():
        columns = [index for index, value in sample_headers.items() if str(value) == sample]
        stress_columns = [
            index for index in columns if "Force standard" in variable_headers[index]
        ]
        extension_columns = [
            index for index in columns if "Allongement" in variable_headers[index]
        ]
        if not extension_columns:
            extension_columns = [
                index for index in columns if "Course standard" in variable_headers[index]
            ]
        if len(stress_columns) != 1 or len(extension_columns) != 1:
            raise ValueError(f"试样列映射不唯一：{sample}")
        curve = frame.iloc[3:, [extension_columns[0], stress_columns[0]]].copy()
        curve.columns = ["strain", "stress"]
        curve = curve.apply(pd.to_numeric, errors="coerce").dropna()
        samples.append((sample, curve))
    return samples


def _legacy_curves(
    sheets: dict[str, pd.DataFrame], sample_names: list[str]
) -> list[tuple[str, pd.DataFrame]]:
    curves = []
    for sample in sample_names:
        if sample not in sheets:
            raise ValueError(f"旧XLS缺少试样曲线工作表：{sample}")
        frame = sheets[sample]
        curve = frame.iloc[3:, [0, 1]].copy()
        curve.columns = ["strain", "stress"]
        curve = curve.apply(pd.to_numeric, errors="coerce").dropna()
        curves.append((sample, curve))
    return curves


def build_release() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for source_name in _gold_files():
        path = _readable_path(source_name)
        condition_id = Path(source_name).stem
        file_hash = _sha256(path)
        sheets = _workbook_sheets(path)
        direct_results = _results(_result_sheet(sheets, path))
        workbook_curves = (
            _legacy_curves(sheets, list(direct_results))
            if path.suffix.lower() == ".xls"
            else _curves(_curve_sheet(sheets, path))
        )
        for sample, curve in workbook_curves:
            if sample not in direct_results:
                raise ValueError(f"曲线试样未映射到结果表：{condition_id}|{sample}")
            endpoints = _derive_endpoints(curve)
            rows.append(
                {
                    "source_id": "source_mendeley_wfsm6f9rbn_v1",
                    "material_grade": "EOS TPU 1301",
                    "formulation_id": "EOS TPU 1301",
                    "condition_id": condition_id,
                    "source_workbook_name": source_name,
                    "parsed_workbook_name": path.name,
                    "sample_id": f"{condition_id}|{sample}",
                    "source_sample_label": sample,
                    **direct_results[sample],
                    **endpoints,
                    "toughness_semantics": (
                        "direct_tensile_curve_area_application_"
                        "not_independent_chemistry"
                    ),
                    "material_class": "SLS_printed_thermoplastic_polyurethane",
                    "chemistry_mapping_status": (
                        "commercial_grade_process_condition_partial"
                    ),
                    "target_role": "direct_tensile_toughness_application",
                    "model_admission_layer": "core_tpu_application_experimental",
                    "usage_mode": "gold_process_transfer_after_condition_group_split",
                    "sample_weight_ceiling": 0.35,
                    "process_mapping_status": (
                        "paper_condition_mapped_detailed_parameters_partial"
                    ),
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
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    return {
        "release_id": "sls_tpu1301_process_tensile_gold_v1",
        "source": {
            "dataset_doi": "10.17632/wfsm6f9rbn.1",
            "license": "CC-BY-4.0",
        },
        "counts": {
            "material_grade_count": 1,
            "gold_process_condition_count": int(frame["condition_id"].nunique()),
            "gold_physical_specimen_count": int(len(frame)),
            "gold_curve_point_count": int(frame["curve_point_count"].sum()),
            "direct_endpoint_scalar_count": int(len(frame) * 5),
            "published_compact_row_count": int(len(frame)),
            "all_deduplicated_curve_count_in_source": int(
                summary["拉伸数据去重后"]["完整应力-应变曲线"]
            ),
            "silver_specimen_count_not_published": int(
                summary["探索性银标准"]["试样"]
            ),
            "recognized_simulation_run_count": 0,
        },
        "policy": {
            "raw_curves_republished": False,
            "silver_curves_republished": False,
            "negative_modulus_exploratory_series_republished": False,
            "process_conditions_increase_material_count": False,
            "cross_source_canonical_material_key": "EOS TPU 1301",
            "split_group_rule": "dataset_doi|material_grade",
            "sample_weight_ceiling": 0.35,
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
        raise SystemExit("SLS TPU1301工艺力学发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("SLS TPU1301端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("SLS TPU1301发布清单不一致")
    print("SLS TPU1301工艺力学检查通过")


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
