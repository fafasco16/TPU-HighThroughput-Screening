"""物化 Mendeley 植物基 PU 泡沫温湿老化压缩端点。

该来源只有泡沫压缩力—位移曲线，配方和逐试样尺寸映射未闭合；输出只作
环境老化/压缩迁移参考，不生成致密 TPU 的应力或断裂韧性标签。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Mendeley_植物基PU泡沫温湿老化压缩"
)
CURVE_DIR = SOURCE_DIR / "数值数据" / "压缩试验曲线"
BATCH_STATS = SOURCE_DIR / "批次统计.tsv"
AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUT = ROOT / "结果" / "定向筛选" / "植物基PU泡沫温湿老化压缩端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "植物基PU泡沫温湿老化发布清单.json"

DATASET_DOI = "10.17632/2sp8fyvhfm.3"
LICENSE = "CC-BY-4.0"
SOURCE_ID = "source_mendeley_2sp8fyvhfm_v3"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_curve(path: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    frame = pd.read_csv(path, sep=";", header=0, skiprows=[1], encoding="cp1252")
    frame.columns = [str(column).strip() for column in frame.columns]
    if len(frame.columns) != 3:
        raise ValueError(f"TRA列数异常: {path.name}")
    numeric = frame.apply(pd.to_numeric, errors="coerce").dropna()
    if numeric.empty:
        raise ValueError(f"TRA没有有限数值行: {path.name}")
    numeric.columns = ["time_s", "displacement_mm", "force_N"]
    return numeric, {
        "time_column": frame.columns[0],
        "displacement_column": frame.columns[1],
        "force_column": frame.columns[2],
    }


def _batch_lookup() -> dict[int, dict[str, object]]:
    stats = pd.read_csv(BATCH_STATS, sep="\t")
    required = {"批次", "暴露温度_C", "相对湿度_pct", "TRA数据点"}
    missing = required - set(stats.columns)
    if missing:
        raise ValueError(f"批次统计缺少字段: {sorted(missing)}")
    return {
        int(row["批次"]): row.to_dict()
        for _, row in stats.iterrows()
    }


def _duplicate_map() -> dict[str, str]:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for group in audit["重复与缺陷审计"]["TRA完全重复组"]:
        for filename in group["文件"]:
            mapping[filename] = str(group["组ID"])
    return mapping


def _endpoint(frame: pd.DataFrame) -> dict[str, object]:
    curve = (
        frame.groupby("displacement_mm", as_index=False)["force_N"]
        .median()
        .sort_values("displacement_mm")
    )
    displacement = curve["displacement_mm"].to_numpy(dtype=float)
    force = curve["force_N"].to_numpy(dtype=float)
    positive_force = np.maximum(force, 0.0)
    return {
        "unique_displacement_point_count": int(len(curve)),
        "maximum_displacement_mm": float(displacement.max()),
        "peak_force_N": float(force.max()),
        "force_displacement_work_J": float(
            np.trapezoid(positive_force, displacement) / 1000.0
        ),
        "force_at_10mm_N": float(np.interp(10.0, displacement, force)),
        "force_at_20mm_N": float(np.interp(20.0, displacement, force)),
        "force_at_30mm_N": float(np.interp(30.0, displacement, force)),
    }


def build_release() -> pd.DataFrame:
    batches = _batch_lookup()
    duplicates = _duplicate_map()
    paths = sorted(CURVE_DIR.glob("批次*/*.TRA"), key=lambda path: path.name)
    if len(paths) != 90:
        raise ValueError(f"TRA曲线数量异常: {len(paths)}")
    rows = []
    for path in paths:
        match = re.fullmatch(r"(\d+)BAT-(DIR\d+)-(\d+)", path.stem)
        if not match:
            raise ValueError(f"无法解析TRA身份: {path.name}")
        batch, direction, sample = match.groups()
        batch_number = int(batch)
        frame, columns = _read_curve(path)
        batch_row = batches[batch_number]
        duplicate_group = duplicates.get(path.name, "")
        endpoint = _endpoint(frame)
        rows.append(
            {
                "source_id": SOURCE_ID,
                "dataset_doi": DATASET_DOI,
                "material_family": "plant_based_polyurethane_foam_unknown_formula",
                "material_grade": "aged_vegetable_based_PU_foam_unknown_grade",
                "formulation_id": "plant_based_PUF_unknown_formula",
                "batch": batch_number,
                "direction": direction,
                "sample_id": path.stem,
                "exposure_temperature_degC": float(batch_row["暴露温度_C"]),
                "exposure_relative_humidity_pct": float(batch_row["相对湿度_pct"]),
                "exposure_duration_status": "unknown_batch_exposure_duration",
                "raw_point_count": int(len(frame)),
                **endpoint,
                "force_unit": "N",
                "displacement_unit": "mm",
                "force_displacement_work_semantics": (
                    "mechanical_work_proxy_not_energy_density"
                ),
                "absolute_stress_available": False,
                "complete_toughness_available": False,
                "target_role": "temperature_humidity_aging_compression_transfer",
                "model_admission_layer": "plant_based_PU_foam_aging_transfer",
                "chemistry_mapping_status": (
                    "plant_based_PU_foam_formula_unresolved"
                ),
                "geometry_mapping_status": (
                    "batch_level_dimensions_not_safe_to_attach_to_specimen"
                ),
                "gold_admission_status": (
                    "conditional_duplicate_content"
                    if duplicate_group
                    else "conditional_reference"
                ),
                "duplicate_content_group": duplicate_group,
                "training_weight_ceiling": 0.0 if duplicate_group else 0.35,
                "current_training_weight": 0.0,
                "split_group": (
                    f"{DATASET_DOI}|plant_based_PUF|"
                    f"{int(batch_row['暴露温度_C'])}C_"
                    f"{int(batch_row['相对湿度_pct'])}RH|batch{batch_number}"
                ),
                "source_file": path.relative_to(ROOT).as_posix(),
                "source_file_sha256": _sha256(path),
                "source_locator": path.relative_to(ROOT).as_posix(),
                "source_columns": json.dumps(columns, ensure_ascii=False),
                "license": LICENSE,
                "citation_keys": "reference-84",
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["batch", "direction", "sample_id"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    return {
        "release_id": "aged_vegetable_pu_foam_compression_transfer_v1",
        "source": {
            "source_id": SOURCE_ID,
            "dataset_doi": DATASET_DOI,
            "license": LICENSE,
            "audit_summary_sha256": _sha256(AUDIT),
            "batch_stats_sha256": _sha256(BATCH_STATS),
            "selective_download_file_count": audit["选择性下载"]["下载文件数"],
            "selective_download_bytes": audit["选择性下载"]["下载字节数"],
        },
        "counts": {
            "batch_count": int(frame["batch"].nunique()),
            "temperature_condition_count": int(
                frame["exposure_temperature_degC"].nunique()
            ),
            "direction_count": int(frame["direction"].nunique()),
            "physical_curve_count": len(frame),
            "default_trainable_curve_count": int(
                frame["duplicate_content_group"].eq("").sum()
            ),
            "isolated_duplicate_curve_count": int(
                frame["duplicate_content_group"].ne("").sum()
            ),
            "raw_point_count": int(frame["raw_point_count"].sum()),
            "published_compact_row_count": len(frame),
        },
        "policy": {
            "raw_curve_points_republished": False,
            "curve_area_is_force_displacement_work_proxy": True,
            "stress_or_energy_density_derived": False,
            "tpu_core_supervision": False,
            "exposure_duration_imputed": False,
            "dimensions_attached_to_each_specimen": False,
            "duplicate_curves_default_isolated": True,
            "environment_is_thermal_decomposition": False,
            "split_rule": "dataset|material_family|temperature_rh|batch",
        },
        "output": {
            "path": OUT.relative_to(ROOT).as_posix(),
            "sha256": output_hash,
        },
    }


def write_release(frame: pd.DataFrame) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(_manifest(frame, _sha256(OUT)), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def check_release(frame: pd.DataFrame) -> None:
    if not OUT.exists() or not MANIFEST.exists():
        raise SystemExit("植物基PU泡沫温湿老化发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("植物基PU泡沫温湿老化端点无法确定性重建")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("植物基PU泡沫温湿老化发布清单不一致")
    print("植物基PU泡沫温湿老化检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(json.dumps({"curves": len(frame)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
