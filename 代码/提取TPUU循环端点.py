"""从DRUM TPUU加载—卸载曲线提取逐循环恢复和滞后端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DIRECTED = ROOT / "结果" / "定向筛选"
INPUT = DIRECTED / "三目标实验标签.csv.gz"
OUTPUT = DIRECTED / "TPUU循环端点.csv"
MANIFEST = DIRECTED / "TPUU循环端点发布清单.json"
RELEASE_ID = "tpuu-cyclic-endpoints-2026-08-30-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path: Path) -> dict[str, object]:
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _cycle_segments(strain: np.ndarray) -> list[tuple[int, int, int]]:
    maximum = float(np.nanmax(strain))
    if not np.isfinite(maximum) or maximum <= 0:
        return []
    peak_enter = 0.98 * maximum
    peak_exit = 0.95 * maximum
    valley = 0.02 * maximum
    segments: list[tuple[int, int, int]] = []
    start = 0
    size = len(strain)
    while start < size - 4:
        peak_candidates = np.flatnonzero(strain[start:] >= peak_enter)
        if len(peak_candidates) == 0:
            break
        peak_start = start + int(peak_candidates[0])
        peak_end = peak_start
        while peak_end + 1 < size and strain[peak_end + 1] >= peak_exit:
            peak_end += 1
        peak = peak_start + int(np.nanargmax(strain[peak_start : peak_end + 1]))
        valley_candidates = np.flatnonzero(strain[peak_end + 1 :] <= valley)
        if len(valley_candidates) == 0:
            break
        end = peak_end + 1 + int(valley_candidates[0])
        if peak - start >= 2 and end - peak >= 2:
            segments.append((start, peak, end))
        start = end
    return segments


def _zero_stress_strain(strain: np.ndarray, stress: np.ndarray) -> float:
    for index in range(1, len(stress)):
        if stress[index - 1] > 0 >= stress[index]:
            s0, s1 = float(stress[index - 1]), float(stress[index])
            e0, e1 = float(strain[index - 1]), float(strain[index])
            if s0 == s1:
                return e1
            return e0 + (0.0 - s0) * (e1 - e0) / (s1 - s0)
    return float("nan")


def extract_cycle_endpoints(
    strain_percent: np.ndarray,
    stress_kpa: np.ndarray,
) -> list[dict[str, float | int | str]]:
    """将连续加载—卸载序列分成循环并计算逐循环端点。"""

    strain = np.asarray(strain_percent, dtype=float)
    stress = np.asarray(stress_kpa, dtype=float)
    valid = np.isfinite(strain) & np.isfinite(stress)
    strain = strain[valid]
    stress = stress[valid]
    records: list[dict[str, float | int | str]] = []
    first_peak = float("nan")
    for cycle_number, (start, peak, end) in enumerate(_cycle_segments(strain), 1):
        cycle_strain = strain[start : end + 1]
        cycle_stress = stress[start : end + 1]
        local_peak = peak - start
        offset = float(np.mean([cycle_stress[0], cycle_stress[-1]]))
        corrected = cycle_stress - offset
        loading_strain = cycle_strain[: local_peak + 1] / 100.0
        unloading_strain = cycle_strain[local_peak:] / 100.0
        loading_stress = corrected[: local_peak + 1]
        unloading_stress = corrected[local_peak:]
        loading = float(np.trapezoid(loading_stress, loading_strain) / 1000.0)
        unloading = float(-np.trapezoid(unloading_stress, unloading_strain) / 1000.0)
        hysteresis = loading - unloading
        ratio = 100.0 * hysteresis / loading if loading > 0 else float("nan")
        residual = _zero_stress_strain(
            cycle_strain[local_peak:], cycle_stress[local_peak:]
        )
        maximum_strain = float(np.nanmax(cycle_strain))
        recovery = (
            100.0 * (maximum_strain - residual) / maximum_strain
            if np.isfinite(residual) and maximum_strain > 0
            else float("nan")
        )
        peak_stress = float(np.nanmax(corrected) / 1000.0)
        if cycle_number == 1:
            first_peak = peak_stress
        retention = (
            100.0 * peak_stress / first_peak
            if np.isfinite(first_peak) and first_peak != 0
            else float("nan")
        )
        quality = (
            "valid"
            if loading > 0 and unloading >= 0 and 0 <= ratio <= 100
            else "review_energy_balance"
        )
        records.append(
            {
                "cycle_number": cycle_number,
                "point_count": end - start + 1,
                "maximum_strain_percent": maximum_strain,
                "peak_stress_MPa": peak_stress,
                "peak_stress_retention_percent": retention,
                "residual_strain_percent": residual,
                "elastic_recovery_percent": recovery,
                "loading_energy_MJ_m3": loading,
                "unloading_energy_MJ_m3": unloading,
                "hysteresis_energy_MJ_m3": hysteresis,
                "energy_dissipation_percent": ratio,
                "quality_status": quality,
            }
        )
    return records


def build_endpoints(source: pd.DataFrame) -> pd.DataFrame:
    curves = source[source["property_name"].eq("cyclic_tensile_stress")].copy()
    records: list[dict[str, object]] = []
    for curve_id, group in curves.groupby("curve_id", sort=True):
        group = group.sort_values("point_index")
        first = group.iloc[0]
        endpoints = extract_cycle_endpoints(
            pd.to_numeric(group["condition_value"], errors="coerce").to_numpy(),
            pd.to_numeric(group["value"], errors="coerce").to_numpy(),
        )
        mapping = str(first["chemistry_mapping_status"])
        endpoint_use = (
            "eligible_after_feature_join"
            if mapping == "component_table_closed"
            else "family_calibration_only"
        )
        for endpoint in endpoints:
            records.append(
                {
                    "release_id": RELEASE_ID,
                    "curve_id": curve_id,
                    "source_id": first["source_id"],
                    "source_family_id": first["source_family_id"],
                    "formulation_id": first["formulation_id"],
                    "sample_id": first["sample_id"],
                    "chemistry_mapping_status": mapping,
                    "endpoint_use": endpoint_use,
                    **endpoint,
                    "source_locator": first["source_locator"],
                    "citation_keys": first["citation_keys"],
                }
            )
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError("没有找到可分段的TPUU循环曲线")
    return frame.sort_values(["curve_id", "cycle_number"]).reset_index(drop=True)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")


def _counts(frame: pd.DataFrame) -> dict[str, int]:
    return {
        "curve_count": frame["curve_id"].nunique(),
        "formulation_count": frame["formulation_id"].nunique(),
        "cycle_endpoint_rows": len(frame),
        "valid_energy_rows": int(frame["quality_status"].eq("valid").sum()),
    }


def _manifest(frame: pd.DataFrame, output: Path) -> dict[str, object]:
    return {
        "release_id": RELEASE_ID,
        "counts": _counts(frame),
        "algorithm": {
            "peak_enter_fraction": 0.98,
            "peak_exit_fraction": 0.95,
            "valley_fraction": 0.02,
            "stress_unit_input": "kPa",
            "strain_unit_input": "percent",
            "energy_unit_output": "MJ/m3",
            "energy_baseline": "mean_cycle_endpoint_stress",
        },
        "input_file": _entry(INPUT),
        "output_file": _entry(output),
    }


def write_release(frame: pd.DataFrame) -> None:
    _write_csv(frame, OUTPUT)
    MANIFEST.write_text(
        json.dumps(_manifest(frame, OUTPUT), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def check_release(frame: pd.DataFrame) -> None:
    if not OUTPUT.is_file() or not MANIFEST.is_file():
        raise SystemExit("缺少TPUU循环端点发布；请先运行生成模式")
    with tempfile.TemporaryDirectory(prefix="tpuu-cycle-check-") as directory:
        candidate = Path(directory) / OUTPUT.name
        _write_csv(frame, candidate)
        if _sha256(candidate) != _sha256(OUTPUT):
            raise SystemExit("TPUU循环端点与当前输入或算法不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(frame, OUTPUT):
        raise SystemExit("TPUU循环端点发布清单不一致")
    print("TPUU循环端点检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    source = pd.read_csv(INPUT, low_memory=False)
    frame = build_endpoints(source)
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(json.dumps(_counts(frame), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
