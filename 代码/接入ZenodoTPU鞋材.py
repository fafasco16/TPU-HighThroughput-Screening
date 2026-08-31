"""接入Zenodo鞋材eTPU/TPU/PEBA的TGA端点与耐磨摘要。"""

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
    / "Zenodo_TPU鞋材热稳定与耐磨"
)
SOURCE_MANIFEST = SOURCE_DIR / "来源清单.json"
TGA_OUT = ROOT / "结果" / "定向筛选" / "TPU鞋材TGA端点.csv"
WEAR_OUT = ROOT / "结果" / "定向筛选" / "TPU鞋材耐磨摘要.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "TPU鞋材发布清单.json"

DATASET_DOI = "10.5281/zenodo.18149651"
ARTICLE_DOI = "10.1016/j.isci.2026.115868"

MATERIALS = [
    {
        "file": "TPU_orange.xlsx",
        "expected_md5": "37f2a3ff70cda8d245cbb32e40129060",
        "material_id": "eTPU_eSUN_95A_orange",
        "material_family": "expanded_thermoplastic_polyurethane",
        "supplier": "eSUN (Shenzhen, China)",
        "hardness_shore_A": 95,
        "density_g_cm3": 1.21,
        "raw_color_code": "orange",
        "mapping_status": "orange_assigned_to_eTPU_by_cross_file_elimination",
        "model_admission_layer": "core_TPU_application_experimental",
        "sample_weight_ceiling": 0.35,
    },
    {
        "file": "TPU 95A White.xlsx",
        "expected_md5": "89420102608b5cfdadd5169911de295d",
        "material_id": "TPU_Rosh_95A_white",
        "material_family": "thermoplastic_polyurethane",
        "supplier": "Rosh (Guangzhou, China)",
        "hardness_shore_A": 95,
        "density_g_cm3": 1.21,
        "raw_color_code": "white",
        "mapping_status": "explicit_raw_header_TPU_95A_white",
        "model_admission_layer": "core_TPU_application_experimental",
        "sample_weight_ceiling": 0.35,
    },
    {
        "file": "TPU_yellow.xlsx",
        "expected_md5": "6913cc12300e7ba3debd915680815dcb",
        "material_id": "PEBA_XinboChuan_85A_yellow",
        "material_family": "polyether_block_amide",
        "supplier": "Xinbo Chuan (Shenzhen, China)",
        "hardness_shore_A": 85,
        "density_g_cm3": 1.02,
        "raw_color_code": "yellow",
        "mapping_status": "raw_rheology_B_Noly_yellow_crosschecked_as_PEBA",
        "model_admission_layer": "commercial_elastomer_auxiliary",
        "sample_weight_ceiling": 0.20,
    },
]

WEAR_LOSS = {
    "eTPU_eSUN_95A_orange": [1.30, 1.90, 1.90, 1.94],
    "TPU_Rosh_95A_white": [2.89, 2.84, 2.46, 2.72],
    "PEBA_XinboChuan_85A_yellow": [0.64, 0.48, 0.43, 0.33],
}
RELATIVE_DENSITIES = [30, 40, 50, 70]


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return _hash(path, "sha256")


def _crossing_temperature(
    temperature: np.ndarray, remaining_pct: np.ndarray, target: float
) -> float:
    indices = np.flatnonzero((temperature >= 150) & (remaining_pct <= target))
    if not len(indices):
        return float("nan")
    current = int(indices[0])
    if current == 0:
        return float(temperature[current])
    t0, t1 = temperature[current - 1], temperature[current]
    y0, y1 = remaining_pct[current - 1], remaining_pct[current]
    if y0 == y1:
        return float(t1)
    return float(t0 + (target - y0) * (t1 - t0) / (y1 - y0))


def _read_tga(path: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    source = pd.read_excel(path, header=None)
    method_text = str(source.iloc[39, 1])
    balance_gas = str(source.iloc[20, 1])
    sample_gas = str(source.iloc[21, 1])
    if "20.00 °C/min" not in method_text or "800.00 °C" not in method_text:
        raise ValueError(f"TGA升温程序不符合来源记录: {path.name}")
    if "Nitrogen" not in balance_gas or "Nitrogen" not in sample_gas:
        raise ValueError(f"TGA气氛不符合来源记录: {path.name}")
    raw = source.iloc[45:, [1, 2, 3]].apply(pd.to_numeric, errors="coerce")
    raw.columns = ["temperature_degC", "mass_mg", "derivative_weight_pct_degC"]
    raw = raw.dropna(subset=["temperature_degC", "mass_mg"])
    curve = (
        raw.groupby("temperature_degC", as_index=False)
        .median(numeric_only=True)
        .sort_values("temperature_degC")
        .reset_index(drop=True)
    )
    return curve, {
        "raw_point_count": int(len(raw)),
        "method_text": method_text,
        "balance_gas": balance_gas,
        "sample_gas": sample_gas,
    }


def _build_tga() -> pd.DataFrame:
    rows = []
    for material in MATERIALS:
        path = SOURCE_DIR / material["file"]
        if _hash(path, "md5") != material["expected_md5"]:
            raise ValueError(f"原始TGA文件MD5不一致: {path.name}")
        curve, metadata = _read_tga(path)
        baseline = curve.loc[
            curve["temperature_degC"].between(100, 150), "mass_mg"
        ].median()
        if pd.isna(baseline) or baseline <= 0:
            raise ValueError(f"TGA基准质量无效: {path.name}")
        temperature = curve["temperature_degC"].to_numpy(dtype=float)
        remaining = curve["mass_mg"].to_numpy(dtype=float) / baseline * 100
        derivative = curve["derivative_weight_pct_degC"].to_numpy(dtype=float)
        dtg_window = (temperature >= 150) & (temperature <= 600)
        dtg_candidates = np.flatnonzero(dtg_window)
        dtg_index = int(
            dtg_candidates[np.nanargmax(derivative[dtg_candidates])]
        )
        terminal_remaining = float(remaining[-1])
        remaining_at_600 = float(np.interp(600, temperature, remaining))
        rows.append(
            {
                "source_id": "source_zenodo_18149651_v1",
                **{key: value for key, value in material.items() if key not in {"file", "expected_md5"}},
                "formulation_id": material["material_id"],
                "raw_curve_point_count": metadata["raw_point_count"],
                "unique_temperature_point_count": int(len(curve)),
                "baseline_mass_mg": float(baseline),
                "baseline_temperature_window_degC": "100-150",
                "T5_degC": _crossing_temperature(temperature, remaining, 95),
                "T10_degC": _crossing_temperature(temperature, remaining, 90),
                "T50_degC": _crossing_temperature(temperature, remaining, 50),
                "DTG_peak_temperature_degC": float(temperature[dtg_index]),
                "DTG_peak_rate_pct_degC": float(derivative[dtg_index]),
                "remaining_mass_at_600C_pct": remaining_at_600,
                "remaining_mass_at_600C_reliable": remaining_at_600 >= 0,
                "terminal_temperature_degC": float(temperature[-1]),
                "terminal_raw_remaining_mass_pct": terminal_remaining,
                "terminal_residue_reliable": terminal_remaining >= 0,
                "heating_rate_degC_min": 20,
                "program_end_temperature_degC": 800,
                "balance_gas": metadata["balance_gas"],
                "sample_gas": metadata["sample_gas"],
                "target_role": "direct_TGA_thermal_stability",
                "split_group": f"{DATASET_DOI}|{material['material_id']}",
                "source_locator": f"{path.relative_to(ROOT).as_posix()}#rows=46:4665",
                "source_file_sha256": _sha256(path),
                "license": "CC-BY-4.0",
                "citation_keys": "reference-201;reference-202",
            }
        )
    return pd.DataFrame(rows).sort_values("material_id").reset_index(drop=True)


def _build_wear() -> pd.DataFrame:
    material_map = {item["material_id"]: item for item in MATERIALS}
    rows = []
    for material_id, losses in WEAR_LOSS.items():
        material = material_map[material_id]
        for relative_density, mass_loss in zip(
            RELATIVE_DENSITIES, losses, strict=True
        ):
            rows.append(
                {
                    "source_id": "source_zenodo_18149651_v1",
                    "material_id": material_id,
                    "material_family": material["material_family"],
                    "supplier": material["supplier"],
                    "hardness_shore_A": material["hardness_shore_A"],
                    "relative_density_pct": relative_density,
                    "abrasion_mass_loss_pct": mass_loss,
                    "reported_replicate_count": 3,
                    "tribology_standard": "KS_M_ISO_12947",
                    "motion_path": "Lissajous",
                    "sliding_cycles_per_min": 30,
                    "total_cycles": 200,
                    "abrasive_surface": "200_grit_sandpaper",
                    "normal_load_g": 500,
                    "test_temperature_degC": 25,
                    "relative_humidity_pct": 55,
                    "source_numeric_level": "published_article_condition_summary",
                    "raw_abrasion_workbook_available": False,
                    "target_role": "wear_durability_environmental_application",
                    "model_admission_layer": material["model_admission_layer"],
                    "sample_weight_ceiling": 0.15,
                    "split_group": f"{DATASET_DOI}|{material_id}",
                    "license": "CC-BY-4.0",
                    "citation_keys": "reference-201;reference-202",
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["material_id", "relative_density_pct"]
    ).reset_index(drop=True)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    return _build_tga(), _build_wear()


def _manifest(
    tga: pd.DataFrame,
    wear: pd.DataFrame,
    tga_hash: str,
    wear_hash: str,
) -> dict[str, object]:
    return {
        "release_id": "zenodo_footwear_tpu_thermal_wear_v1",
        "source": {
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "dataset_license": "CC-BY-4.0",
            "article_license": "CC-BY-4.0",
            "source_manifest_sha256": _sha256(SOURCE_MANIFEST),
        },
        "counts": {
            "commercial_material_count": int(tga["material_id"].nunique()),
            "tga_curve_count": len(tga),
            "tga_curve_point_count": int(tga["raw_curve_point_count"].sum()),
            "wear_condition_count": len(wear),
            "published_compact_row_count": len(tga) + len(wear),
        },
        "policy": {
            "raw_tga_curves_republished": False,
            "DSC_used_as_TGA_decomposition_target": False,
            "PEBA_counted_as_TPU": False,
            "compression_curve_claimed_without_numeric_source": False,
            "abrasion_conditions_claimed_as_independent_chemistries": False,
            "negative_terminal_balance_drift_used_as_residue": False,
            "negative_600C_balance_drift_used_as_residue": False,
        },
        "outputs": {TGA_OUT.name: tga_hash, WEAR_OUT.name: wear_hash},
    }


def write_release(tga: pd.DataFrame, wear: pd.DataFrame) -> None:
    tga.to_csv(TGA_OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    wear.to_csv(WEAR_OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(
            _manifest(tga, wear, _sha256(TGA_OUT), _sha256(WEAR_OUT)),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(tga: pd.DataFrame, wear: pd.DataFrame) -> None:
    if not TGA_OUT.exists() or not WEAR_OUT.exists() or not MANIFEST.exists():
        raise SystemExit("TPU鞋材发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        tga_candidate = Path(directory) / TGA_OUT.name
        wear_candidate = Path(directory) / WEAR_OUT.name
        tga.to_csv(
            tga_candidate, index=False, encoding="utf-8-sig", lineterminator="\n"
        )
        wear.to_csv(
            wear_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        if _sha256(tga_candidate) != _sha256(TGA_OUT):
            raise SystemExit("TPU鞋材TGA端点与确定性重建不一致")
        if _sha256(wear_candidate) != _sha256(WEAR_OUT):
            raise SystemExit("TPU鞋材耐磨摘要与确定性重建不一致")
    expected = _manifest(tga, wear, _sha256(TGA_OUT), _sha256(WEAR_OUT))
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("TPU鞋材发布清单不一致")
    print("TPU鞋材检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    tga, wear = build_release()
    if args.检查:
        check_release(tga, wear)
    else:
        write_release(tga, wear)
        print(
            json.dumps(
                {"tga_curves": len(tga), "wear_conditions": len(wear)},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
