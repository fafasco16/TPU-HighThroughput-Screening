"""接入Zenodo木质素/Pearlthane TPU前驱纤维力学与TGA端点。"""

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
    / "Zenodo_木质素_TPU多模态数据"
)
WORKBOOK = SOURCE_DIR / "Biobased.xlsx"
MECHANICAL_OUT = ROOT / "结果" / "定向筛选" / "木质素TPU前驱纤维力学.csv"
TGA_OUT = ROOT / "结果" / "定向筛选" / "木质素TPU_TGA端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "木质素TPU发布清单.json"

DATASET_DOI = "10.5281/zenodo.3631551"
ARTICLE_DOI = "10.1021/acssuschemeng.8b01170"
EXPECTED_WORKBOOK_SHA256 = (
    "5dd712d854f56a50946e195039d875e1dc22fb755b309f99e9516789d120c6d4"
)

LIGNINS = {
    "TcA": "Alcell organosolv hardwood lignin",
    "TcC": "hydroxypropyl-modified Kraft hardwood lignin",
}

TGA_SPECS = [
    ("TcC", 70, 30, 0, 1, 2),
    ("TcC", 60, 40, 3, 4, 5),
    ("TcC", 50, 50, 6, 7, 8),
    ("TcA", 70, 30, 10, 11, 12),
    ("TcA", 60, 40, 13, 14, 15),
    ("TcA", 50, 50, 16, 17, 18),
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _formulation(lignin: str, lignin_pct: int, tpu_pct: int) -> dict[str, object]:
    return {
        "formulation_id": f"{lignin}-TPU-{lignin_pct}-{tpu_pct}",
        "lignin_code": lignin,
        "lignin_identity": LIGNINS[lignin],
        "lignin_wt_pct": lignin_pct,
        "commercial_tpu_grade": "Pearlthane ECO 12T95",
        "tpu_wt_pct": tpu_pct,
        "fraction_basis": "weight_percent",
        "chemistry_mapping_status": "lignin_identity_and_commercial_TPU_grade_mapped",
        "material_family": "lignin_commercial_TPU_carbon_fiber_precursor_blend",
        "thermoplastic_TPU_core": False,
        "model_admission_layer": "lignin_TPU_carbon_fiber_precursor_transfer",
        "split_group": f"{DATASET_DOI}|{lignin}-TPU-{lignin_pct}-{tpu_pct}",
        "dataset_license": "CC-BY-4.0",
        "citation_keys": "reference-122;reference-123",
    }


def _build_mechanical() -> pd.DataFrame:
    source = pd.read_excel(WORKBOOK, sheet_name="Sheet5", header=None)
    rows = []
    for row_index in range(2, 6):
        tpu_pct = int(source.iloc[row_index, 0])
        lignin_pct = 100 - tpu_pct
        for lignin, modulus_column, strength_column, strain_column in (
            ("TcC", 1, 3, 5),
            ("TcA", 2, 4, 6),
        ):
            raw_strain = source.iloc[row_index, strain_column]
            censored = isinstance(raw_strain, str) and raw_strain.strip().startswith(">")
            strain_value = (
                float("nan") if censored else float(pd.to_numeric(raw_strain))
            )
            lower_bound = 200.0 if censored else float("nan")
            formulation = _formulation(lignin, lignin_pct, tpu_pct)
            rows.append(
                {
                    "source_id": "source_zenodo_3631551_v1",
                    **formulation,
                    "material_state": "melt_spun_lignin_TPU_precursor_fiber",
                    "youngs_modulus_MPa": float(source.iloc[row_index, modulus_column]),
                    "tensile_strength_MPa": float(
                        source.iloc[row_index, strength_column]
                    ),
                    "elongation_at_break_pct": strain_value,
                    "elongation_right_censored": censored,
                    "elongation_lower_bound_pct": lower_bound,
                    "reported_replicate_count": float("nan"),
                    "complete_stress_strain_curve_available": False,
                    "complete_toughness_available": False,
                    "target_role": "precursor_fiber_strength_modulus_elongation_transfer",
                    "sample_weight_ceiling": 0.15,
                    "source_locator": f"{WORKBOOK.relative_to(ROOT).as_posix()}#sheet=Sheet5,row={row_index + 1},lignin={lignin}",
                    "source_file_sha256": _sha256(WORKBOOK),
                }
            )
    return pd.DataFrame(rows).sort_values("formulation_id").reset_index(drop=True)


def _crossing_temperature(
    temperature: np.ndarray, mass_pct: np.ndarray, target: float
) -> float:
    indices = np.flatnonzero(mass_pct <= target)
    if not len(indices):
        return float("nan")
    current = int(indices[0])
    if current == 0:
        return float(temperature[current])
    t0, t1 = temperature[current - 1], temperature[current]
    y0, y1 = mass_pct[current - 1], mass_pct[current]
    if y0 == y1:
        return float(t1)
    return float(t0 + (target - y0) * (t1 - t0) / (y1 - y0))


def _build_tga() -> pd.DataFrame:
    source = pd.read_excel(WORKBOOK, sheet_name="Sheet6", header=None)
    rows = []
    for lignin, lignin_pct, tpu_pct, temp_col, time_col, mass_col in TGA_SPECS:
        curve = pd.DataFrame(
            {
                "temperature": source.iloc[5:, temp_col],
                "source_time": source.iloc[5:, time_col],
                "mass_pct": source.iloc[5:, mass_col],
            }
        ).apply(pd.to_numeric, errors="coerce")
        curve = curve.dropna(subset=["temperature", "mass_pct"])
        raw_point_count = int(len(curve))
        curve = (
            curve.groupby("temperature", as_index=False)
            .median(numeric_only=True)
            .sort_values("temperature")
        )
        temperature = curve["temperature"].to_numpy(dtype=float)
        mass = curve["mass_pct"].to_numpy(dtype=float)
        formulation = _formulation(lignin, lignin_pct, tpu_pct)
        rows.append(
            {
                "source_id": "source_zenodo_3631551_v1",
                **formulation,
                "raw_curve_point_count": raw_point_count,
                "unique_temperature_point_count": int(len(curve)),
                "source_initial_mass_max_pct": float(mass.max()),
                "T5_degC": _crossing_temperature(temperature, mass, 95),
                "T10_degC": _crossing_temperature(temperature, mass, 90),
                "T50_degC": _crossing_temperature(temperature, mass, 50),
                "terminal_temperature_degC": float(temperature[-1]),
                "terminal_remaining_mass_pct": float(mass[-1]),
                "temperature_unit_status": "primary_article_supported_Celsius_workbook_header_missing",
                "source_time_unit_status": "workbook_header_missing_not_used",
                "atmosphere": "not_reported_in_local_workbook",
                "heating_rate_degC_min": float("nan"),
                "target_role": "direct_TGA_thermal_stability_transfer",
                "sample_weight_ceiling": 0.20,
                "source_locator": f"{WORKBOOK.relative_to(ROOT).as_posix()}#sheet=Sheet6,columns={temp_col + 1}:{mass_col + 1}",
                "source_file_sha256": _sha256(WORKBOOK),
            }
        )
    return pd.DataFrame(rows).sort_values("formulation_id").reset_index(drop=True)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    if _sha256(WORKBOOK) != EXPECTED_WORKBOOK_SHA256:
        raise ValueError("木质素TPU工作簿SHA-256与冻结值不一致")
    return _build_mechanical(), _build_tga()


def _manifest(
    mechanical: pd.DataFrame,
    tga: pd.DataFrame,
    mechanical_hash: str,
    tga_hash: str,
) -> dict[str, object]:
    return {
        "release_id": "zenodo_lignin_tpu_multimodal_v1",
        "source": {
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "license": "CC-BY-4.0",
            "workbook_sha256": _sha256(WORKBOOK),
        },
        "counts": {
            "mechanical_formulation_count": int(
                mechanical["formulation_id"].nunique()
            ),
            "mechanical_summary_row_count": len(mechanical),
            "right_censored_elongation_count": int(
                mechanical["elongation_right_censored"].sum()
            ),
            "tga_formulation_count": int(tga["formulation_id"].nunique()),
            "tga_curve_count": len(tga),
            "tga_curve_point_count": int(tga["raw_curve_point_count"].sum()),
            "published_compact_row_count": len(mechanical) + len(tga),
        },
        "policy": {
            "precursor_fibers_claimed_as_bulk_TPU": False,
            "right_censored_elongation_imputed_as_200": False,
            "mechanical_summary_claimed_as_complete_curve": False,
            "TGA_temperature_unit_silently_assumed_from_workbook": False,
            "lignin_content_conditions_counted_as_new_TPU_chemistries": False,
            "carbonized_fiber_properties_included": False,
        },
        "outputs": {
            MECHANICAL_OUT.name: mechanical_hash,
            TGA_OUT.name: tga_hash,
        },
    }


def write_release(mechanical: pd.DataFrame, tga: pd.DataFrame) -> None:
    mechanical.to_csv(
        MECHANICAL_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    tga.to_csv(TGA_OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(
            _manifest(
                mechanical,
                tga,
                _sha256(MECHANICAL_OUT),
                _sha256(TGA_OUT),
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(mechanical: pd.DataFrame, tga: pd.DataFrame) -> None:
    if not MECHANICAL_OUT.exists() or not TGA_OUT.exists() or not MANIFEST.exists():
        raise SystemExit("木质素TPU发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        mechanical_candidate = Path(directory) / MECHANICAL_OUT.name
        tga_candidate = Path(directory) / TGA_OUT.name
        mechanical.to_csv(
            mechanical_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        tga.to_csv(
            tga_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        if _sha256(mechanical_candidate) != _sha256(MECHANICAL_OUT):
            raise SystemExit("木质素TPU力学摘要与确定性重建不一致")
        if _sha256(tga_candidate) != _sha256(TGA_OUT):
            raise SystemExit("木质素TPU TGA端点与确定性重建不一致")
    expected = _manifest(
        mechanical,
        tga,
        _sha256(MECHANICAL_OUT),
        _sha256(TGA_OUT),
    )
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("木质素TPU发布清单不一致")
    print("木质素TPU检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    mechanical, tga = build_release()
    if args.检查:
        check_release(mechanical, tga)
    else:
        write_release(mechanical, tga)
        print(
            json.dumps(
                {"mechanical_rows": len(mechanical), "tga_curves": len(tga)},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
