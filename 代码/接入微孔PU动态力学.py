"""提取微孔PU三密度DMA与九个SHPB动态冲击条件端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    / "ScienceDB_微孔PU动态力学"
)
RAW_DIR = SOURCE_DIR / "原始文件"
DMA_OUT = ROOT / "结果" / "定向筛选" / "微孔PU_DMA端点.csv"
SHPB_OUT = ROOT / "结果" / "定向筛选" / "微孔PU_SHPB端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "微孔PU动态力学发布清单.json"
GRADES = ("400M", "600M", "800M")
DENSITIES = {"400M": 400, "600M": 600, "800M": 800}
VELOCITIES = (30, 48, 62)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_curve(x: pd.Series, y: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame({"x": x, "y": y}).apply(
        pd.to_numeric, errors="coerce"
    ).dropna()
    return frame.groupby("x", as_index=False)["y"].median().sort_values("x")


def _interp(curve: pd.DataFrame, target: float) -> float:
    return float(np.interp(target, curve["x"], curve["y"]))


def _build_dma() -> pd.DataFrame:
    modulus_path = RAW_DIR / "Figure 7a-20240329.csv"
    tan_path = RAW_DIR / "Figure 7b-20240329.csv"
    modulus = pd.read_csv(modulus_path, header=None, encoding_errors="replace")
    tan = pd.read_csv(tan_path, header=None, encoding_errors="replace")
    rows = []
    for index, grade in enumerate(GRADES):
        storage = _clean_curve(
            modulus.iloc[2:, index * 3], modulus.iloc[2:, index * 3 + 1]
        )
        loss = _clean_curve(
            modulus.iloc[2:, index * 3], modulus.iloc[2:, index * 3 + 2]
        )
        tan_curve = _clean_curve(
            tan.iloc[2:, index * 2], tan.iloc[2:, index * 2 + 1]
        )
        peak = tan_curve.loc[tan_curve["y"].idxmax()]
        rows.append(
            {
                "source_id": "source_sciencedb_j00189_00045_v1",
                "density_grade": grade,
                "apparent_density_kg_m3": DENSITIES[grade],
                "storage_curve_point_count": int(len(storage)),
                "loss_curve_point_count": int(len(loss)),
                "tan_delta_curve_point_count": int(len(tan_curve)),
                "tan_delta_peak_temperature_degC": float(peak["x"]),
                "tan_delta_peak_value": float(peak["y"]),
                "storage_modulus_at_minus50C_MPa": _interp(storage, -50),
                "storage_modulus_at_0C_MPa": _interp(storage, 0),
                "storage_modulus_at_20C_MPa": _interp(storage, 20),
                "loss_modulus_at_20C_MPa": _interp(loss, 20),
                "target_role": "DMA_thermomechanical_transition_transfer",
                "model_admission_layer": "microporous_PU_dynamic_transfer",
                "usage_mode": "DMA_transition_and_damping_auxiliary",
                "direct_decomposition_stability": False,
                "sample_weight_ceiling": 0.25,
                "split_group": (
                    f"10.57760/sciencedb.j00189.00045|{grade}|DMA"
                ),
                "source_locator": (
                    f"{modulus_path.relative_to(ROOT).as_posix()}+"
                    f"{tan_path.relative_to(ROOT).as_posix()}"
                ),
                "modulus_file_sha256": _sha256(modulus_path),
                "tan_delta_file_sha256": _sha256(tan_path),
                "license": "CC-BY-4.0",
                "citation_keys": "reference-57;reference-58",
            }
        )
    return pd.DataFrame(rows)


def _channel_endpoint(curve: pd.DataFrame) -> dict[str, float | int]:
    time = curve["x"].to_numpy(dtype=float)
    stress = curve["y"].to_numpy(dtype=float)
    peak_index = int(np.argmax(stress))
    return {
        "point_count": int(len(curve)),
        "peak_stress_MPa": float(stress[peak_index]),
        "time_to_peak_us": float(time[peak_index]),
        "stress_time_impulse_MPa_us": float(
            np.trapezoid(np.maximum(stress, 0.0), time)
        ),
        "record_duration_us": float(time[-1] - time[0]),
    }


def _build_shpb() -> pd.DataFrame:
    files = {
        "400M": RAW_DIR / "Figure 8a-20290330.xlsx",
        "600M": RAW_DIR / "Figure 8b-20290330.csv",
        "800M": RAW_DIR / "Figure 8c-20290330.csv",
    }
    rows = []
    for grade, path in files.items():
        frame = (
            pd.read_excel(path, header=None)
            if path.suffix.lower() == ".xlsx"
            else pd.read_csv(path, header=None, encoding_errors="replace")
        )
        for velocity_index, velocity in enumerate(VELOCITIES):
            channel1 = _clean_curve(
                frame.iloc[2:, velocity_index * 4],
                frame.iloc[2:, velocity_index * 4 + 1],
            )
            channel2 = _clean_curve(
                frame.iloc[2:, velocity_index * 4 + 2],
                frame.iloc[2:, velocity_index * 4 + 3],
            )
            first = _channel_endpoint(channel1)
            second = _channel_endpoint(channel2)
            rows.append(
                {
                    "source_id": "source_sciencedb_j00189_00045_v1",
                    "density_grade": grade,
                    "apparent_density_kg_m3": DENSITIES[grade],
                    "impact_velocity_source_label": velocity,
                    "sensor_channel_count": 2,
                    "physical_impact_condition_count": 1,
                    **{f"sigma1_{key}": value for key, value in first.items()},
                    **{f"sigma2_{key}": value for key, value in second.items()},
                    "peak_stress_channel_ratio_sigma2_over_sigma1": (
                        second["peak_stress_MPa"] / first["peak_stress_MPa"]
                    ),
                    "target_role": "SHPB_dynamic_peak_stress_transfer_proxy",
                    "model_admission_layer": "microporous_PU_dynamic_transfer",
                    "usage_mode": "dynamic_impact_auxiliary_not_toughness",
                    "complete_stress_strain_toughness_available": False,
                    "sample_weight_ceiling": 0.25,
                    "split_group": (
                        "10.57760/sciencedb.j00189.00045|"
                        f"{grade}|SHPB|{velocity}"
                    ),
                    "source_locator": path.relative_to(ROOT).as_posix(),
                    "source_file_sha256": _sha256(path),
                    "license": "CC-BY-4.0",
                    "citation_keys": "reference-57;reference-58",
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["density_grade", "impact_velocity_source_label"]
    ).reset_index(drop=True)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    return _build_dma(), _build_shpb()


def _manifest(
    dma: pd.DataFrame,
    shpb: pd.DataFrame,
    dma_hash: str,
    shpb_hash: str,
) -> dict[str, object]:
    return {
        "release_id": "microporous_pu_dynamic_transfer_v1",
        "source": {
            "dataset_doi": "10.57760/sciencedb.j00189.00045",
            "article_doi": "10.1007/s10118-024-3134-4",
            "license": "CC-BY-4.0",
        },
        "counts": {
            "density_grade_count": 3,
            "DMA_endpoint_row_count": int(len(dma)),
            "DMA_response_curve_count": 9,
            "SHPB_physical_condition_count": int(len(shpb)),
            "SHPB_sensor_curve_count": int(
                shpb["sensor_channel_count"].sum()
            ),
            "published_compact_row_count": int(len(dma) + len(shpb)),
        },
        "policy": {
            "raw_curves_republished": False,
            "sensor_channels_increase_physical_condition_count": False,
            "DMA_response_channels_increase_material_count": False,
            "direct_toughness_available": False,
            "direct_decomposition_stability_available": False,
            "drop_test_simulation_republished": False,
        },
        "outputs": {DMA_OUT.name: dma_hash, SHPB_OUT.name: shpb_hash},
    }


def write_release(dma: pd.DataFrame, shpb: pd.DataFrame) -> None:
    dma.to_csv(DMA_OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    shpb.to_csv(SHPB_OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(
            _manifest(dma, shpb, _sha256(DMA_OUT), _sha256(SHPB_OUT)),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(dma: pd.DataFrame, shpb: pd.DataFrame) -> None:
    if not all(path.exists() for path in (DMA_OUT, SHPB_OUT, MANIFEST)):
        raise SystemExit("微孔PU动态力学发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        for path, frame in ((DMA_OUT, dma), (SHPB_OUT, shpb)):
            candidate = Path(directory) / path.name
            frame.to_csv(
                candidate, index=False, encoding="utf-8-sig", lineterminator="\n"
            )
            if _sha256(candidate) != _sha256(path):
                raise SystemExit(f"微孔PU动态力学输出不一致：{path.name}")
    expected = _manifest(dma, shpb, _sha256(DMA_OUT), _sha256(SHPB_OUT))
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("微孔PU动态力学发布清单不一致")
    print("微孔PU动态力学检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    dma, shpb = build_release()
    if args.检查:
        check_release(dma, shpb)
    else:
        write_release(dma, shpb)
        print(json.dumps({"dma": len(dma), "shpb": len(shpb)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
