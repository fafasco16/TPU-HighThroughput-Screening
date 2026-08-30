"""将PCU85湿/干单根电纺PU纤维循环曲线压缩为嵌套端点。"""

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
    / "Texas_湿干单根电纺PU纤维力学"
)
DATA_DIR = SOURCE_DIR / "解压内容" / "Data"
ARCHIVE = SOURCE_DIR / "Data.zip"
OUT = ROOT / "结果" / "定向筛选" / "PCU85单纤维循环端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "PCU85单纤维循环发布清单.json"
CONDITIONS = ("Dry", "Soaked", "Submerged")
STRAIN_LEVELS = (10, 15, 20, 30)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _segment_metrics(
    segment: pd.DataFrame, zero_force_uN: float
) -> tuple[float, float, float, float]:
    force = np.maximum(
        segment["z_force_uN"].to_numpy(dtype=float) - zero_force_uN,
        0.0,
    )
    displacement = (
        segment["z_tip_um"].to_numpy(dtype=float)
        - segment["z_base_um"].to_numpy(dtype=float)
    )
    path = np.concatenate(
        ([0.0], np.cumsum(np.abs(np.diff(displacement))))
    )
    return (
        float(np.max(force)),
        float(np.trapezoid(force, path)),
        float(displacement[0]),
        float(displacement[-1]),
    )


def _read_fiber(path: Path) -> tuple[pd.DataFrame, float, float, int]:
    raw = pd.read_csv(path, encoding="cp1252")
    source_row_count = len(raw)
    frame = raw.iloc[:, [0, 1, 2, 3, 4, 5, 6, 10, 12]].copy()
    frame.columns = [
        "set_name",
        "cycle",
        "time_ms",
        "z_tip_um",
        "z_base_um",
        "current_size_um",
        "z_force_uN",
        "temperature_degC",
        "diameter_um",
    ]
    for column in frame.columns[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    diameter = float(frame["diameter_um"].dropna().iloc[0])
    temperature = float(frame["temperature_degC"].median())
    frame = frame.iloc[1:].copy()
    return frame, diameter, temperature, source_row_count


def _stage_row(
    frame: pd.DataFrame,
    strain_level: int,
    zero_force: float,
) -> dict[str, float | int]:
    stage = frame[frame["set_name"].eq(f"{strain_level}% Strain")]
    metrics: dict[tuple[int, str], tuple[float, float, float, float]] = {}
    for cycle in (1, 2):
        for branch in ("Compress", "Recover"):
            segment = stage[stage["cycle"].eq(f"{cycle}-{branch}")]
            if segment.empty:
                raise ValueError(
                    f"缺失循环段：{strain_level}% {cycle}-{branch}"
                )
            metrics[(cycle, branch)] = _segment_metrics(segment, zero_force)

    result: dict[str, float | int] = {"nominal_strain_percent": strain_level}
    for cycle in (1, 2):
        load_peak, load_work, load_start, load_end = metrics[(cycle, "Compress")]
        _, recover_work, _, recover_end = metrics[(cycle, "Recover")]
        loading_excursion = abs(load_end - load_start)
        residual = abs(recover_end - load_start)
        hysteresis = load_work - recover_work
        result.update(
            {
                f"peak_force_cycle{cycle}_uN": load_peak,
                f"loading_work_cycle{cycle}_pJ": load_work,
                f"recovery_work_cycle{cycle}_pJ": recover_work,
                f"hysteresis_work_cycle{cycle}_pJ": hysteresis,
                f"dissipation_fraction_cycle{cycle}": (
                    hysteresis / load_work if load_work > 0 else float("nan")
                ),
                f"loading_displacement_excursion_cycle{cycle}_um": (
                    loading_excursion
                ),
                f"residual_displacement_cycle{cycle}_um": residual,
                f"displacement_recovery_proxy_cycle{cycle}": (
                    1.0 - residual / loading_excursion
                    if loading_excursion > 0
                    else float("nan")
                ),
            }
        )
    result["cycle2_peak_force_retention"] = (
        result["peak_force_cycle2_uN"] / result["peak_force_cycle1_uN"]
        if result["peak_force_cycle1_uN"] > 0
        else float("nan")
    )
    result["cycle2_loading_work_retention"] = (
        result["loading_work_cycle2_pJ"] / result["loading_work_cycle1_pJ"]
        if result["loading_work_cycle1_pJ"] > 0
        else float("nan")
    )
    return result


def build_release() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for condition in CONDITIONS:
        for path in sorted((DATA_DIR / condition).glob("*.csv")):
            frame, diameter, temperature, source_row_count = _read_fiber(path)
            fiber_id = path.stem.removesuffix("Data")
            batch_match = re.search(r"_(\d{2}_\d{2}_\d{2})_", path.name)
            if not batch_match:
                raise ValueError(f"无法解析测试日期批次：{path.name}")
            date_batch = batch_match.group(1)
            zero_force = float(
                frame.loc[
                    frame["z_force_uN"].abs().nsmallest(
                        max(10, int(len(frame) * 0.05))
                    ).index,
                    "z_force_uN",
                ].median()
            )
            segment_count = int(
                frame[["set_name", "cycle"]].drop_duplicates().shape[0]
            )
            if segment_count != 17:
                raise ValueError(f"循环段数异常：{path.name} -> {segment_count}")
            for strain_level in STRAIN_LEVELS:
                rows.append(
                    {
                        "source_id": "source_texas_zyq5z1_v1",
                        "material_code": "PCU85",
                        "formulation_id": "PCU85",
                        "fiber_csv_id": fiber_id,
                        "hydration_condition": condition,
                        "test_date_batch": date_batch,
                        "diameter_um": diameter,
                        "temperature_degC": temperature,
                        **_stage_row(
                            frame, strain_level, zero_force
                        ),
                        "source_machine_row_count": source_row_count,
                        "nested_curve_segment_count": segment_count,
                        "initialization_transient_rows_excluded": 1,
                        "absolute_stress_available": False,
                        "complete_toughness_available": False,
                        "target_role": (
                            "single_fiber_cyclic_force_displacement_transfer"
                        ),
                        "chemistry_mapping_status": "material_code_only",
                        "model_admission_layer": (
                            "single_fiber_polyurethane_auxiliary"
                        ),
                        "usage_mode": (
                            "cyclic_transfer_and_wet_condition_external_validation"
                        ),
                        "sample_weight_ceiling": 0.0625,
                        "fiber_total_weight_ceiling": 0.25,
                        "split_group": (
                            "10.18738/T8/ZYQ5Z1|PCU85|"
                            f"{condition}|{date_batch}"
                        ),
                        "source_locator": str(path.relative_to(ROOT)).replace(
                            "\\", "/"
                        ),
                        "source_sha256": _sha256(path),
                        "license": "CC0-1.0",
                        "citation_keys": "reference-197",
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["hydration_condition", "fiber_csv_id", "nominal_strain_percent"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    unique_fibers = frame.drop_duplicates("fiber_csv_id")
    return {
        "release_id": "pcu85_single_fiber_cyclic_v1",
        "source": {
            "dataset_doi": "10.18738/T8/ZYQ5Z1",
            "license": "CC0-1.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "material_code_count": 1,
            "physical_fiber_count": int(frame["fiber_csv_id"].nunique()),
            "condition_count": int(frame["hydration_condition"].nunique()),
            "test_date_batch_count": int(frame["test_date_batch"].nunique()),
            "nested_endpoint_row_count": int(len(frame)),
            "curve_segment_count": int(
                unique_fibers["nested_curve_segment_count"].sum()
            ),
            "machine_source_row_count": int(
                unique_fibers["source_machine_row_count"].sum()
            ),
            "initialization_transient_row_count": int(
                unique_fibers["initialization_transient_rows_excluded"].sum()
            ),
            "sem_image_count": 85,
            "sem_images_mapped_to_mechanical_fibers": 83,
            "sem_images_without_mechanical_csv": 2,
        },
        "policy": {
            "raw_curves_republished": False,
            "bulk_TPU_toughness_available": False,
            "absolute_stress_available": False,
            "one_fiber_total_weight_ceiling": 0.25,
            "one_nested_row_weight_ceiling": 0.0625,
            "split_group_rule": (
                "dataset_doi|material_code|hydration_condition|test_date_batch"
            ),
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
        raise SystemExit("PCU85单纤维循环发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("PCU85单纤维循环端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("PCU85单纤维循环发布清单不一致")
    print("PCU85单纤维循环检查通过")


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
                    "rows": len(frame),
                    "fibers": int(frame["fiber_csv_id"].nunique()),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
