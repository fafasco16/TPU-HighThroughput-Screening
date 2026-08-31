"""提取HDB/HA开孔PU泡沫六温度动态压缩与能量吸收端点。"""

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
    / "Mendeley_PU泡沫动态力学_精选表"
)
HDB = SOURCE_DIR / "HDB_StressStrain.xlsx"
HA = SOURCE_DIR / "HA_StressStrain.xlsx"
ENERGY = SOURCE_DIR / "Energy_Absorption.xlsx"
OUT = ROOT / "结果" / "定向筛选" / "PU泡沫动态压缩端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "PU泡沫动态压缩发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _energy_to_strain(curve: pd.DataFrame, target: float) -> float:
    below = curve[curve["strain"] < target].copy()
    stress_at_target = float(
        np.interp(target, curve["strain"], curve["stress_Pa"])
    )
    terminal = pd.DataFrame({"strain": [target], "stress_Pa": [stress_at_target]})
    integrated = pd.concat([below, terminal], ignore_index=True)
    return float(np.trapezoid(integrated["stress_Pa"], integrated["strain"]))


def _curve_endpoints(curve: pd.DataFrame) -> dict[str, object]:
    curve = curve.apply(pd.to_numeric, errors="coerce").dropna()
    curve.columns = ["strain", "stress_Pa"]
    curve = (
        curve.groupby("strain", as_index=False)["stress_Pa"]
        .median()
        .sort_values("strain")
    )
    maximum_strain = float(curve["strain"].max())
    reaches_65pct = maximum_strain >= 0.65
    peak_index = int(curve["stress_Pa"].idxmax())
    full_energy = float(np.trapezoid(curve["stress_Pa"], curve["strain"]))
    energy65 = _energy_to_strain(curve, 0.65) if reaches_65pct else float("nan")
    stress65 = (
        float(np.interp(0.65, curve["strain"], curve["stress_Pa"]) / 1e6)
        if reaches_65pct
        else float("nan")
    )
    return {
        "curve_point_count": int(len(curve)),
        "minimum_observed_strain": float(curve["strain"].min()),
        "maximum_observed_strain": maximum_strain,
        "curve_reaches_65pct_strain": reaches_65pct,
        "peak_stress_MPa": float(curve.loc[peak_index, "stress_Pa"] / 1e6),
        "strain_at_peak_stress": float(curve.loc[peak_index, "strain"]),
        "stress_at_65pct_strain_MPa": stress65,
        "full_observed_energy_absorption_MJ_m3": full_energy / 1e6,
        "energy_absorption_to_65pct_MJ_m3": energy65 / 1e6,
        "full_observed_energy_absorption_J_m3": full_energy,
        "energy_absorption_to_65pct_J_m3": energy65,
    }


def _energy_summary(
    frame: pd.DataFrame, column: int
) -> dict[str, object]:
    full = pd.to_numeric(frame.iloc[2:7, column], errors="coerce").dropna()
    normalized = pd.to_numeric(frame.iloc[7:12, column], errors="coerce").dropna()
    return {
        "source_full_energy_observation_count": int(len(full)),
        "source_full_energy_mean_J_m3": float(full.mean()),
        "source_full_energy_std_J_m3": float(full.std(ddof=1))
        if len(full) > 1
        else float("nan"),
        "source_65pct_energy_observation_count": int(len(normalized)),
        "source_65pct_energy_mean_J_m3": float(normalized.mean()),
        "source_65pct_energy_std_J_m3": float(normalized.std(ddof=1))
        if len(normalized) > 1
        else float("nan"),
        "source_full_energy_values": ";".join(f"{value:.12g}" for value in full),
    }


def _records_for_material(
    path: Path,
    material_code: str,
    headers: list[tuple[int, str, int, str]],
    energy: pd.DataFrame,
) -> list[dict[str, object]]:
    frame = pd.read_excel(path, header=None)
    rows = []
    for pair_index, canonical_label, energy_column, label_status in headers:
        curve = frame.iloc[1:, [pair_index * 2, pair_index * 2 + 1]].copy()
        endpoints = _curve_endpoints(curve)
        source_energy = _energy_summary(energy, energy_column)
        source_values = [
            float(value)
            for value in source_energy["source_full_energy_values"].split(";")
        ]
        nearest_relative_error = min(
            abs(endpoints["full_observed_energy_absorption_J_m3"] - value)
            / max(abs(value), 1e-12)
            for value in source_values
        )
        match = nearest_relative_error <= 0.02
        temperature = 20 if canonical_label == "room_temperature" else int(canonical_label)
        rows.append(
            {
                "source_id": "source_mendeley_x6b72k59xn_v1",
                "material_code": material_code,
                "formulation_id": material_code,
                "temperature_degC": temperature,
                "source_temperature_header": frame.iloc[0, pair_index * 2],
                "temperature_label_status": label_status,
                **endpoints,
                **{key: value for key, value in source_energy.items() if key != "source_full_energy_values"},
                "curve_full_energy_nearest_source_relative_error": (
                    nearest_relative_error
                ),
                "curve_full_energy_matches_source_within_2pct": match,
                "specimen_dimensions_mm": "30x30x12.7",
                "drop_mass_kg": 2.2,
                "drop_height_m": 0.35,
                "impact_energy_J": 7.5,
                "impact_velocity_m_s": 2.6,
                "mean_strain_rate_s-1": 200,
                "stress_definition": "force_divided_by_initial_area",
                "strain_source": "DIC_or_digital_extensometer",
                "target_role": "dynamic_compression_energy_absorption_transfer",
                "material_class": "open_cell_polyurethane_foam",
                "chemistry_mapping_status": "commercial_foam_code_only",
                "model_admission_layer": "dynamic_PU_foam_transfer",
                "usage_mode": "dynamic_compression_transfer_not_quasistatic_TPU",
                "sample_weight_ceiling": 0.25,
                "split_group": (
                    f"10.17632/x6b72k59xn.1|{material_code}|{temperature}C"
                ),
                "source_locator": (
                    f"{path.relative_to(ROOT).as_posix()}#columns="
                    f"{pair_index * 2 + 1}:{pair_index * 2 + 2}"
                ),
                "source_file_sha256": _sha256(path),
                "license": "CC-BY-4.0",
                "citation_keys": "reference-65;reference-198",
            }
        )
    return rows


def build_release() -> pd.DataFrame:
    energy = pd.read_excel(ENERGY, header=None)
    hdb_headers = [
        (0, "-20", 0, "source_label_valid"),
        (1, "-10", 1, "source_label_valid"),
        (2, "0", 2, "source_label_valid"),
        (3, "10", 3, "source_label_valid"),
        (4, "room_temperature", 4, "source_label_valid"),
        (5, "40", 5, "source_label_valid"),
    ]
    ha_headers = [
        (0, "0", 8, "source_label_valid"),
        (1, "10", 9, "source_label_valid"),
        (2, "room_temperature", 10, "source_label_valid"),
        (3, "40", 11, "source_label_valid"),
        (4, "-10", 7, "resolved_duplicate_header_by_energy_match"),
        (5, "-20", 6, "resolved_duplicate_header_by_energy_match"),
    ]
    rows = _records_for_material(HDB, "HDB_foam", hdb_headers, energy)
    rows += _records_for_material(HA, "HA_foam", ha_headers, energy)
    return pd.DataFrame(rows).sort_values(
        ["material_code", "temperature_degC"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    energy = pd.read_excel(ENERGY, header=None)
    return {
        "release_id": "temperature_dynamic_pu_foam_compression_v1",
        "source": {
            "dataset_doi": "10.17632/x6b72k59xn.1",
            "article_doi": "10.1007/s11340-024-01054-0",
            "dataset_license": "CC-BY-4.0",
            "article_license": "CC-BY-4.0",
        },
        "counts": {
            "material_code_count": int(frame["material_code"].nunique()),
            "temperature_condition_count": int(len(frame)),
            "stress_strain_curve_count": int(len(frame)),
            "stress_strain_curve_point_count": int(frame["curve_point_count"].sum()),
            "source_energy_numeric_observation_count": int(
                pd.to_numeric(energy.iloc[2:12, :12].stack(), errors="coerce")
                .notna()
                .sum()
            ),
            "resolved_duplicate_temperature_header_count": int(
                frame["temperature_label_status"].eq(
                    "resolved_duplicate_header_by_energy_match"
                ).sum()
            ),
            "curve_energy_source_match_count": int(
                frame["curve_full_energy_matches_source_within_2pct"].sum()
            ),
            "curve_reaches_65pct_strain_count": int(
                frame["curve_reaches_65pct_strain"].sum()
            ),
            "published_compact_row_count": int(len(frame)),
        },
        "policy": {
            "raw_curves_republished": False,
            "stress_source_unit": "Pa_converted_to_MPa",
            "strain_source_unit": "dimensionless",
            "energy_conversion": "Pa_times_strain_to_J_per_m3_then_divide_1e6",
            "curve_to_energy_match_tolerance": "nearest_source_observation_relative_error_le_2pct",
            "curve_65pct_extrapolation_allowed": False,
            "quasistatic_TPU_toughness_claim": False,
            "cross_source_alias_HDB_to_98A_or_HA_to_ZAP_asserted": False,
            "local_subset_claimed_as_full_repository": False,
        },
        "inputs": {
            HDB.name: _sha256(HDB),
            HA.name: _sha256(HA),
            ENERGY.name: _sha256(ENERGY),
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
        raise SystemExit("PU泡沫动态压缩发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("PU泡沫动态压缩端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("PU泡沫动态压缩发布清单不一致")
    print("PU泡沫动态压缩检查通过")


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
