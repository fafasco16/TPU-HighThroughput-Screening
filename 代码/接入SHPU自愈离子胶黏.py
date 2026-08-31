"""物化 Figshare SHPU 超分子聚氨酯的力学、自愈和界面韧性端点。

来源是动态超分子 PU/离子胶黏体系，不是已闭合单体身份的热塑性 TPU。
端点进入迁移层，所有配方代码、愈合时间和工作簿位置保持可追溯。
"""

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
    / "Figshare_自愈离子胶黏PU源数据"
)
WORKBOOK = SOURCE_DIR / "Source Data.xlsx"
AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUT_DIR = ROOT / "结果" / "定向筛选"
TENSILE_OUT = OUT_DIR / "SHPU自愈拉伸端点.csv"
CYCLIC_OUT = OUT_DIR / "SHPU恢复加载端点.csv"
INTERFACIAL_OUT = OUT_DIR / "SHPU界面韧性摘要.csv"
MANIFEST = OUT_DIR / "SHPU自愈离子胶黏发布清单.json"

DATASET_DOI = "10.6084/m9.figshare.21716516.v1"
ARTICLE_DOI = "10.1038/s41467-023-37535-4"
LICENSE = "CC-BY-4.0"
SOURCE_ID = "source_figshare_21716516_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _curve(sheet: pd.DataFrame, x_column: int, y_column: int) -> pd.DataFrame:
    raw = sheet.iloc[3:, [x_column, y_column]].copy()
    raw.columns = ["strain_percent", "stress_MPa"]
    raw = raw.apply(pd.to_numeric, errors="coerce").dropna()
    if raw.empty:
        raise ValueError(f"空应力—应变列: {x_column},{y_column}")
    return (
        raw.groupby("strain_percent", as_index=False)["stress_MPa"]
        .median()
        .sort_values("strain_percent")
        .reset_index(drop=True)
    )


def _curve_endpoints(curve: pd.DataFrame) -> dict[str, object]:
    strain = curve["strain_percent"].to_numpy(dtype=float)
    stress = curve["stress_MPa"].to_numpy(dtype=float)
    return {
        "curve_point_count": int(len(curve)),
        "maximum_observed_strain_percent": float(strain.max()),
        "peak_stress_MPa": float(stress.max()),
        "stress_strain_area_MJ_m3": float(
            np.trapezoid(np.maximum(stress, 0.0), strain / 100.0)
        ),
        "stress_at_100pct_MPa": float(np.interp(100.0, strain, stress)),
        "stress_at_500pct_MPa": float(np.interp(500.0, strain, stress)),
        "stress_at_1000pct_MPa": float(np.interp(1000.0, strain, stress)),
    }


def _common(**extra: object) -> dict[str, object]:
    return {
        "source_id": SOURCE_ID,
        "dataset_doi": DATASET_DOI,
        "article_doi": ARTICLE_DOI,
        "material_family": "supramolecular_hydrogen_bonded_polyurethane_SHPU",
        "license": LICENSE,
        "source_file": WORKBOOK.relative_to(ROOT).as_posix(),
        "source_file_sha256": _sha256(WORKBOOK),
        "model_admission_layer": "supramolecular_PU_transfer",
        "tpu_core_supervision": False,
        "chemistry_mapping_status": "source_material_code_only",
        "complete_toughness_available": False,
        **extra,
    }


def _build_tensile(sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    sheet = sheets["Fig. 2a"]
    for pair, material in enumerate(("PE10/GY3", "PE10/GY5", "PE10/GY7")):
        curve = _curve(sheet, pair * 2, pair * 2 + 1)
        rows.append(
            _common(
                material_code=material,
                formulation_id=material,
                healing_state="pristine",
                healing_time_h=0.0,
                curve_source_sheet="Fig. 2a",
                curve_source_columns=f"{pair * 2 + 1}:{pair * 2 + 2}",
                identity_relation_status="Fig2a_source_code_only",
                target_role="direct_stress_strain_area_toughness_transfer",
                usage_mode="supramolecular_PU_toughness_transfer_not_TPU_core",
                sample_weight_ceiling=0.25,
                split_group=f"{DATASET_DOI}|{material}",
                citation_keys="reference-71;reference-72",
                source_locator=f"Source Data.xlsx#Fig. 2a;columns={pair * 2 + 1}:{pair * 2 + 2}",
                absolute_stress_available=True,
            )
            | _curve_endpoints(curve)
        )
    sheet = sheets["Fig. 2e"]
    states = ("pristine", "healed_6h", "healed_12h", "healed_24h")
    hours = (0.0, 6.0, 12.0, 24.0)
    for pair, (state, hour) in enumerate(zip(states, hours, strict=True)):
        curve = _curve(sheet, pair * 2, pair * 2 + 1)
        rows.append(
            _common(
                material_code="SHPU_self_healing_optimized",
                formulation_id="SHPU_self_healing_optimized",
                healing_state=state,
                healing_time_h=hour,
                curve_source_sheet="Fig. 2e",
                curve_source_columns=f"{pair * 2 + 1}:{pair * 2 + 2}",
                identity_relation_status=(
                    "Fig2e_optimized_sample_not_mapped_to_Fig2a_composition"
                ),
                target_role="self_healing_tensile_toughness_transfer",
                usage_mode="healing_time_response_not_TPU_core",
                sample_weight_ceiling=0.25,
                split_group=(
                    f"{DATASET_DOI}|SHPU_self_healing_optimized|healing_series"
                ),
                citation_keys="reference-71;reference-72",
                source_locator=f"Source Data.xlsx#Fig. 2e;columns={pair * 2 + 1}:{pair * 2 + 2}",
                absolute_stress_available=True,
            )
            | _curve_endpoints(curve)
        )
    return pd.DataFrame(rows)


def _build_cyclic(sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    sheet = sheets["Fig. 2f"]
    curves = [_curve(sheet, 0, 1), _curve(sheet, 2, 3)]
    endpoints = [_curve_endpoints(curve) for curve in curves]
    baseline_area = endpoints[0]["stress_strain_area_MJ_m3"]
    rows = []
    for index, (label, curve, values) in enumerate(
        zip(
            ("first_load", "second_load_after_1h"),
            curves,
            endpoints,
            strict=True,
        )
    ):
        rows.append(
            _common(
                material_code="SHPU_self_healing_optimized",
                formulation_id="SHPU_self_healing_optimized",
                load_sequence=label,
                recovery_wait_h=0.0 if index == 0 else 1.0,
                curve_source_sheet="Fig. 2f",
                curve_source_columns=f"{index * 2 + 1}:{index * 2 + 2}",
                identity_relation_status="successive_loading_same_source_figure",
                target_role="one_hour_stress_and_energy_recovery_transfer_proxy",
                usage_mode="successive_monotonic_loading_not_full_hysteresis",
                sample_weight_ceiling=0.20,
                split_group=(
                    f"{DATASET_DOI}|SHPU_self_healing_optimized|Fig2f_series"
                ),
                citation_keys="reference-71;reference-72",
                source_locator=f"Source Data.xlsx#Fig. 2f;columns={index * 2 + 1}:{index * 2 + 2}",
                absolute_stress_available=True,
                energy_retention_vs_first_load=(
                    1.0
                    if index == 0
                    else float(
                        values["stress_strain_area_MJ_m3"] / baseline_area
                    )
                ),
                peak_stress_retention_vs_first_load=(
                    1.0
                    if index == 0
                    else float(values["peak_stress_MPa"] / endpoints[0]["peak_stress_MPa"])
                ),
            )
            | values
        )
    return pd.DataFrame(rows)


def _build_interfacial(sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    sheet = sheets["Fig. 3e"]
    labels = ("SHPU", "VHB_4950", "Sylgard_184")
    rows = []
    for column, label in enumerate(labels):
        rows.append(
            _common(
                material_code=label,
                formulation_id=label,
                endpoint_type="interfacial_toughness_summary",
                interfacial_toughness_J_m2=float(sheet.iloc[6, column]),
                interfacial_toughness_sd_J_m2=float(sheet.iloc[7, column]),
                source_replicate_count=5,
                identity_relation_status="Fig3e_material_comparison_summary",
                target_role="interfacial_toughness_auxiliary_not_bulk_tpu_toughness",
                usage_mode="adhesive_interface_transfer_not_bulk_TPU",
                sample_weight_ceiling=0.15,
                split_group=f"{DATASET_DOI}|Fig3e|{label}",
                citation_keys="reference-71;reference-72",
                source_locator=f"Source Data.xlsx#Fig. 3e;column={column + 1}",
                complete_toughness_available=False,
            )
        )
    return pd.DataFrame(rows)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sheets = pd.read_excel(WORKBOOK, sheet_name=None, header=None)
    return _build_tensile(sheets), _build_cyclic(sheets), _build_interfacial(sheets)


def _manifest(
    tensile: pd.DataFrame,
    cyclic: pd.DataFrame,
    interfacial: pd.DataFrame,
    output_hashes: dict[str, str],
) -> dict[str, object]:
    return {
        "release_id": "shpu_self_healing_iontronic_transfer_v1",
        "source": {
            "source_id": SOURCE_ID,
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "license": LICENSE,
            "workbook_sha256": _sha256(WORKBOOK),
            "audit_summary_sha256": _sha256(AUDIT),
        },
        "counts": {
            "tensile_endpoint_count": len(tensile),
            "tensile_curve_point_count": int(tensile["curve_point_count"].sum()),
            "cyclic_endpoint_count": len(cyclic),
            "cyclic_curve_point_count": int(cyclic["curve_point_count"].sum()),
            "interfacial_summary_count": len(interfacial),
            "published_compact_row_count": len(tensile) + len(cyclic) + len(interfacial),
        },
        "policy": {
            "raw_curves_republished": False,
            "tpu_core_supervision": False,
            "fig2e_pristine_not_merged_with_fig2a_codes": True,
            "successive_loading_is_full_hysteresis": False,
            "interfacial_toughness_is_bulk_toughness": False,
            "chemistry_smiles_inferred": False,
        },
        "outputs": {
            key: {
                "path": path.relative_to(ROOT).as_posix(),
                "row_count": len(
                    {"tensile": tensile, "cyclic": cyclic, "interfacial": interfacial}[key]
                ),
                "sha256": output_hashes[key],
            }
            for key, path in {
                "tensile": TENSILE_OUT,
                "cyclic": CYCLIC_OUT,
                "interfacial": INTERFACIAL_OUT,
            }.items()
        },
    }


def _write_frames(
    tensile: pd.DataFrame, cyclic: pd.DataFrame, interfacial: pd.DataFrame, directory: Path
) -> dict[str, Path]:
    paths = {
        "tensile": directory / TENSILE_OUT.name,
        "cyclic": directory / CYCLIC_OUT.name,
        "interfacial": directory / INTERFACIAL_OUT.name,
    }
    for key, frame in {
        "tensile": tensile,
        "cyclic": cyclic,
        "interfacial": interfacial,
    }.items():
        frame.to_csv(paths[key], index=False, encoding="utf-8-sig", lineterminator="\n")
    return paths


def write_release(
    tensile: pd.DataFrame, cyclic: pd.DataFrame, interfacial: pd.DataFrame
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = _write_frames(tensile, cyclic, interfacial, OUT_DIR)
    hashes = {key: _sha256(path) for key, path in paths.items()}
    MANIFEST.write_text(
        json.dumps(_manifest(tensile, cyclic, interfacial, hashes), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def check_release(
    tensile: pd.DataFrame, cyclic: pd.DataFrame, interfacial: pd.DataFrame
) -> None:
    paths = {
        "tensile": TENSILE_OUT,
        "cyclic": CYCLIC_OUT,
        "interfacial": INTERFACIAL_OUT,
    }
    with tempfile.TemporaryDirectory() as directory:
        candidate = _write_frames(tensile, cyclic, interfacial, Path(directory))
        for key, path in paths.items():
            if not path.exists() or _sha256(candidate[key]) != _sha256(path):
                raise SystemExit(f"SHPU发布物无法确定性重建: {path.name}")
    hashes = {key: _sha256(path) for key, path in paths.items()}
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        tensile, cyclic, interfacial, hashes
    ):
        raise SystemExit("SHPU发布清单不一致")
    print("SHPU自愈离子胶黏检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    tensile, cyclic, interfacial = build_release()
    if args.检查:
        check_release(tensile, cyclic, interfacial)
    else:
        write_release(tensile, cyclic, interfacial)
        print(
            json.dumps(
                {
                    "tensile": len(tensile),
                    "cyclic": len(cyclic),
                    "interfacial": len(interfacial),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
