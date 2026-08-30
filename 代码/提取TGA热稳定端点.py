"""从定向实验长表的TGA/DTG曲线提取可审计热稳定端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "结果" / "定向筛选" / "三目标实验标签.csv.gz"
OUTPUT = ROOT / "结果" / "定向筛选" / "TGA热稳定端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "TGA端点发布清单.json"
RELEASE_ID = "tpu-directed-tga-endpoints-2026-08-30-v1"


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


def _threshold_temperature(
    temperature: np.ndarray,
    mass_percent: np.ndarray,
    threshold: float,
) -> float:
    hits = np.flatnonzero(mass_percent <= threshold)
    if len(hits) == 0:
        return float("nan")
    index = int(hits[0])
    if index == 0:
        return float(temperature[0])
    t0, t1 = float(temperature[index - 1]), float(temperature[index])
    m0, m1 = float(mass_percent[index - 1]), float(mass_percent[index])
    if m0 == m1:
        return t1
    fraction = (threshold - m0) / (m1 - m0)
    return t0 + fraction * (t1 - t0)


def extract_tga_endpoints(curve: pd.DataFrame) -> dict[str, float | int]:
    """对temperature/mass两列曲线做归一、单调包络和阈值插值。"""

    clean = curve[["temperature", "mass"]].apply(pd.to_numeric, errors="coerce")
    clean = clean.dropna().sort_values("temperature")
    clean = clean.groupby("temperature", as_index=False)["mass"].median()
    if len(clean) < 4:
        raise ValueError("TGA曲线少于4个有效温度点")
    temperature = clean["temperature"].to_numpy(dtype=float)
    mass = clean["mass"].to_numpy(dtype=float)
    baseline_points = max(3, int(np.ceil(len(mass) * 0.05)))
    baseline = float(np.nanmax(mass[:baseline_points]))
    if not np.isfinite(baseline) or baseline <= 0:
        raise ValueError("TGA初始质量无效")
    normalized = 100.0 * mass / baseline
    monotonic = np.minimum.accumulate(normalized)
    adjustment_count = int(np.count_nonzero(np.abs(monotonic - normalized) > 0.05))
    return {
        "point_count": int(len(clean)),
        "temperature_min_degC": float(temperature.min()),
        "temperature_max_degC": float(temperature.max()),
        "baseline_mass": baseline,
        "final_residual_mass_percent": float(monotonic[-1]),
        "monotonic_adjustment_point_count": adjustment_count,
        "T5_degC": _threshold_temperature(temperature, monotonic, 95.0),
        "T10_degC": _threshold_temperature(temperature, monotonic, 90.0),
        "T50_degC": _threshold_temperature(temperature, monotonic, 50.0),
    }


def _dtg_peak(dtg: pd.DataFrame, sample_id: object) -> tuple[object, float, float]:
    sample = dtg[dtg["sample_id"].fillna("").eq(str(sample_id))].copy()
    if sample.empty:
        return pd.NA, float("nan"), float("nan")
    sample["value"] = pd.to_numeric(sample["value"], errors="coerce")
    sample["secondary_condition_value"] = pd.to_numeric(
        sample["secondary_condition_value"], errors="coerce"
    )
    sample = sample.dropna(subset=["value", "secondary_condition_value"])
    if sample.empty:
        return pd.NA, float("nan"), float("nan")
    peak = sample.loc[sample["value"].idxmax()]
    return (
        peak["curve_id"],
        float(peak["secondary_condition_value"]),
        float(peak["value"]),
    )


def build_endpoints(source: pd.DataFrame) -> pd.DataFrame:
    mass_rows = source[source["property_name"].eq("tga_mass_signal")].copy()
    dtg_rows = source[source["property_name"].eq("dtg_mass_rate")].copy()
    records: list[dict[str, object]] = []
    for curve_id, group in mass_rows.groupby("curve_id", sort=True):
        first = group.iloc[0]
        curve = pd.DataFrame(
            {
                "temperature": group["secondary_condition_value"],
                "mass": group["value"],
            }
        )
        endpoints = extract_tga_endpoints(curve)
        dtg_id, dtg_temperature, dtg_rate = _dtg_peak(
            dtg_rows, first["sample_id"]
        )
        identity_conflict = (
            pd.isna(first["formulation_id"])
            or "conflict" in str(first["mapping_status"]).lower()
        )
        mapping = str(first["chemistry_mapping_status"])
        if identity_conflict:
            quality = "identity_conflict_endpoints_reference_only"
            endpoint_use = "reference_only_identity_conflict"
        elif any(pd.isna(endpoints[key]) for key in ("T5_degC", "T10_degC")):
            quality = "incomplete_temperature_range"
            endpoint_use = "reference_only_incomplete_curve"
        elif mapping == "component_table_closed":
            quality = "endpoint_ready"
            endpoint_use = "eligible_after_feature_join"
        else:
            quality = "endpoint_ready_component_mapping_pending"
            endpoint_use = "family_calibration_only"
        records.append(
            {
                "release_id": RELEASE_ID,
                "curve_id": curve_id,
                "source_id": first["source_id"],
                "source_family_id": first["source_family_id"],
                "formulation_id": first["formulation_id"],
                "sample_id": first["sample_id"],
                "original_mass_unit": first["unit"],
                "chemistry_mapping_status": mapping,
                "quality_status": quality,
                "endpoint_use": endpoint_use,
                **endpoints,
                "DTG_curve_id": dtg_id,
                "DTG_peak_temperature_degC": dtg_temperature,
                "DTG_peak_rate_source_unit": dtg_rate,
                "Td_onset_degC": pd.NA,
                "Td_onset_status": "not_derived_without_protocolized_tangent_method",
                "source_locator": first["source_locator"],
                "citation_keys": first["citation_keys"],
            }
        )
    return pd.DataFrame(records).sort_values("curve_id").reset_index(drop=True)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")


def _counts(frame: pd.DataFrame) -> dict[str, int]:
    return {
        "curve_count": len(frame),
        "identity_resolved_curve_count": int(frame["formulation_id"].notna().sum()),
        "t5_count": int(frame["T5_degC"].notna().sum()),
        "t10_count": int(frame["T10_degC"].notna().sum()),
        "t50_count": int(frame["T50_degC"].notna().sum()),
    }


def _manifest(frame: pd.DataFrame, output: Path) -> dict[str, object]:
    return {
        "release_id": RELEASE_ID,
        "algorithm": {
            "baseline": "maximum_of_first_5_percent_minimum_3_points",
            "normalization": "100*mass/baseline",
            "noise_handling": "cumulative_minimum_envelope",
            "thresholds_mass_percent": [95, 90, 50],
            "interpolation": "linear_between_bracketing_points",
            "Td_onset": "not_derived_without_protocolized_tangent_method",
        },
        "counts": _counts(frame),
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
        raise SystemExit("缺少TGA端点发布；请先运行生成模式")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="tpu-tga-check-") as directory:
        candidate = Path(directory) / OUTPUT.name
        _write_csv(frame, candidate)
        if _sha256(candidate) != _sha256(OUTPUT):
            raise SystemExit("TGA端点与当前输入或算法不一致")
    if manifest != _manifest(frame, OUTPUT):
        raise SystemExit("TGA端点发布清单不一致")
    print("TGA热稳定端点检查通过")


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
