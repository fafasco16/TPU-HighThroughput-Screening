"""接入Zenodo导电、自修复、可回收交联PU复合膜力学数据。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
import zipfile
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
    / "Zenodo_导电自修复可回收PU复合材料"
)
ARCHIVE = SOURCE_DIR / "Dataset Cicoira Materials Horizons 2026.zip"
SOURCE_MANIFEST = SOURCE_DIR / "来源清单.json"
MECHANICAL_OUT = ROOT / "结果" / "定向筛选" / "导电自修复PU拉伸与回收端点.csv"
RECOVERY_OUT = ROOT / "结果" / "定向筛选" / "导电自修复PU恢复文献指标.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "导电自修复PU发布清单.json"

DATASET_DOI = "10.5281/zenodo.19609901"
ARTICLE_DOI = "10.1039/d6mh00177g"
EXPECTED_ARCHIVE_SHA256 = (
    "f7a0705d67e80f91bff482f8af6f768f2e7bb2357b483e2f999a7fd6da650e29"
)
FIG2_MEMBER = (
    "Dataset Cicoira Materials Horizons 2026/"
    "Figure 2/Fig 2a (strain-stress).txt"
)
FIG6_MEMBER = (
    "Dataset Cicoira Materials Horizons 2026/Figure 6/Fig 6c.txt"
)

BASE_CHEMISTRY = {
    "base_pu_soft_segment_1": "polycaprolactone diol Mn 2000",
    "base_pu_soft_segment_2": "polyethylene glycol Mn 2000",
    "base_pu_diisocyanate": "isophorone diisocyanate (IPDI)",
    "base_pu_chain_extender": "aminophenyl disulfide (APDS)",
    "base_pu_crosslinker": "trimethylolpropane (TMP)",
    "base_pu_main_component_molar_ratio": "1.5:4:11:5.5",
    "base_pu_TMP_amount_mmol": 0.5,
    "base_pu_DBTDL_amount_g": 0.09,
    "thermoplastic_TPU_core": False,
}

FORMULATIONS = [
    {
        "formulation_id": "PEDOT:PSS/PU-13",
        "pedot_pss_solution_wt_pct": 87.0,
        "pu_solution_wt_pct": 13.0,
        "glycerol_wt_pct": 0.0,
        "table_elongation_mean_pct": 202.0,
        "table_elongation_std_pct": 10.0,
        "table_youngs_modulus_mean_MPa": 0.14,
        "table_youngs_modulus_std_MPa": 0.04,
    },
    {
        "formulation_id": "PEDOT:PSS/PU-15",
        "pedot_pss_solution_wt_pct": 85.0,
        "pu_solution_wt_pct": 15.0,
        "glycerol_wt_pct": 0.0,
        "table_elongation_mean_pct": 432.0,
        "table_elongation_std_pct": 12.0,
        "table_youngs_modulus_mean_MPa": 0.13,
        "table_youngs_modulus_std_MPa": 0.02,
    },
    {
        "formulation_id": "PEDOT:PSS/PU-18",
        "pedot_pss_solution_wt_pct": 82.0,
        "pu_solution_wt_pct": 18.0,
        "glycerol_wt_pct": 0.0,
        "table_elongation_mean_pct": 500.0,
        "table_elongation_std_pct": 15.0,
        "table_youngs_modulus_mean_MPa": 0.09,
        "table_youngs_modulus_std_MPa": 0.03,
    },
    {
        "formulation_id": "PEDOT:PSS/PU-15/Gly-2.4",
        "pedot_pss_solution_wt_pct": 83.0,
        "pu_solution_wt_pct": 14.6,
        "glycerol_wt_pct": 2.4,
        "table_elongation_mean_pct": 545.0,
        "table_elongation_std_pct": 12.0,
        "table_youngs_modulus_mean_MPa": 0.040,
        "table_youngs_modulus_std_MPa": 0.01,
    },
    {
        "formulation_id": "PEDOT:PSS/PU-18/Gly-2.2",
        "pedot_pss_solution_wt_pct": 80.3,
        "pu_solution_wt_pct": 17.5,
        "glycerol_wt_pct": 2.2,
        "table_elongation_mean_pct": 630.0,
        "table_elongation_std_pct": 19.0,
        "table_youngs_modulus_mean_MPa": 0.020,
        "table_youngs_modulus_std_MPa": 0.002,
    },
]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_curves(payload: bytes) -> list[pd.DataFrame]:
    source = pd.read_csv(io.BytesIO(payload), sep="\t")
    if len(source.columns) % 2:
        raise ValueError("应力—应变表不是成对列")
    curves = []
    for column in range(0, len(source.columns), 2):
        curve = source.iloc[:, column : column + 2]
        curve = curve.apply(pd.to_numeric, errors="coerce").dropna()
        curve.columns = ["strain_pct", "stress_MPa"]
        curve = (
            curve.groupby("strain_pct", as_index=False)["stress_MPa"]
            .median()
            .sort_values("strain_pct")
            .reset_index(drop=True)
        )
        curves.append(curve)
    return curves


def _curve_endpoints(curve: pd.DataFrame) -> dict[str, object]:
    peak_index = int(curve["stress_MPa"].idxmax())
    nonnegative_stress = curve["stress_MPa"].clip(lower=0)
    return {
        "curve_point_count": int(len(curve)),
        "minimum_observed_stress_MPa": float(curve["stress_MPa"].min()),
        "maximum_observed_strain_pct": float(curve["strain_pct"].max()),
        "peak_stress_MPa": float(curve.loc[peak_index, "stress_MPa"]),
        "strain_at_peak_stress_pct": float(curve.loc[peak_index, "strain_pct"]),
        "tensile_curve_area_MJ_m3": float(
            np.trapezoid(nonnegative_stress, curve["strain_pct"] / 100)
        ),
        "negative_stress_clipped_for_area": bool(
            curve["stress_MPa"].lt(0).any()
        ),
    }


def _common_fields() -> dict[str, object]:
    return {
        **BASE_CHEMISTRY,
        "blend_fraction_basis": "solution_mass_fraction_not_dry_solid_fraction",
        "chemistry_mapping_status": "base_PU_exact_composition_blend_solution_fraction_mapped",
        "model_admission_layer": "conductive_crosslinked_PU_composite_transfer",
        "split_group": f"{DATASET_DOI}|shared_crosslinked_PU_family",
        "sample_weight_ceiling": 0.20,
        "dataset_license": "CC-BY-4.0",
        "article_license": "CC-BY-NC-3.0",
        "citation_keys": "reference-199;reference-200",
    }


def _build_mechanical(archive: zipfile.ZipFile) -> pd.DataFrame:
    fig2_payload = archive.read(FIG2_MEMBER)
    fig2_curves = _read_curves(fig2_payload)
    if len(fig2_curves) != len(FORMULATIONS):
        raise ValueError("Figure 2a曲线数与论文配方表不一致")
    rows: list[dict[str, object]] = []
    for index, (formulation, curve) in enumerate(
        zip(FORMULATIONS, fig2_curves, strict=True)
    ):
        endpoints = _curve_endpoints(curve)
        relative_difference = abs(
            endpoints["maximum_observed_strain_pct"]
            - formulation["table_elongation_mean_pct"]
        ) / formulation["table_elongation_mean_pct"]
        if relative_difference > 0.05:
            raise ValueError("Figure 2a列序与论文Table 1伸长率不闭合")
        rows.append(
            {
                "source_id": "source_zenodo_19609901_v1",
                "record_id": f"fig2a_curve_{index + 1}",
                **formulation,
                "material_state": "original_drop_cast_film",
                "record_role": "formulation_screening_tensile",
                **endpoints,
                "curve_max_vs_table_elongation_relative_difference": (
                    relative_difference
                ),
                "recycling_state_peak_stress_retention_fraction": float("nan"),
                "recycling_state_curve_area_retention_fraction": float("nan"),
                "recycling_state_max_strain_retention_fraction": float("nan"),
                "source_mechanical_reuse_cycle_count": float("nan"),
                "source_elongation_retention_mean_pct": float("nan"),
                "source_elongation_retention_std_pct": float("nan"),
                "target_role": "direct_tensile_curve_area_transfer",
                "environmental_evidence": "not_assigned_for_screening_curve",
                "cost_data_available": False,
                "source_locator": f"{ARCHIVE.name}!/{FIG2_MEMBER}#pair={index + 1}",
                "source_member_sha256": _sha256_bytes(fig2_payload),
                "column_mapping_status": (
                    "figure_order_crosschecked_against_article_table_elongation"
                ),
                **_common_fields(),
            }
        )

    fig6_payload = archive.read(FIG6_MEMBER)
    fig6_curves = _read_curves(fig6_payload)
    states = [
        "original_composite",
        "chemically_recycled_composite",
        "mechanically_reused_composite",
    ]
    if len(fig6_curves) != len(states):
        raise ValueError("Figure 6c回收状态曲线数不一致")
    state_endpoints = [_curve_endpoints(curve) for curve in fig6_curves]
    reference = state_endpoints[0]
    optimized = FORMULATIONS[-1]
    for index, (state, endpoints) in enumerate(
        zip(states, state_endpoints, strict=True)
    ):
        mechanical_reuse = state == "mechanically_reused_composite"
        rows.append(
            {
                "source_id": "source_zenodo_19609901_v1",
                "record_id": f"fig6c_{state}",
                **optimized,
                "material_state": state,
                "record_role": "recycling_state_tensile",
                **endpoints,
                "curve_max_vs_table_elongation_relative_difference": float("nan"),
                "recycling_state_peak_stress_retention_fraction": (
                    endpoints["peak_stress_MPa"] / reference["peak_stress_MPa"]
                ),
                "recycling_state_curve_area_retention_fraction": (
                    endpoints["tensile_curve_area_MJ_m3"]
                    / reference["tensile_curve_area_MJ_m3"]
                ),
                "recycling_state_max_strain_retention_fraction": (
                    endpoints["maximum_observed_strain_pct"]
                    / reference["maximum_observed_strain_pct"]
                ),
                "source_mechanical_reuse_cycle_count": (
                    15 if mechanical_reuse else float("nan")
                ),
                "source_elongation_retention_mean_pct": (
                    95.0 if mechanical_reuse else float("nan")
                ),
                "source_elongation_retention_std_pct": (
                    4.0 if mechanical_reuse else float("nan")
                ),
                "target_role": "tensile_and_recycling_retention_transfer",
                "environmental_evidence": (
                    "ethanol_component_recovery"
                    if state == "chemically_recycled_composite"
                    else "55C_30min_mechanical_remolding"
                    if mechanical_reuse
                    else "original_recycling_reference"
                ),
                "cost_data_available": False,
                "source_locator": f"{ARCHIVE.name}!/{FIG6_MEMBER}#pair={index + 1}",
                "source_member_sha256": _sha256_bytes(fig6_payload),
                "column_mapping_status": "figure_readme_list_order",
                **_common_fields(),
            }
        )
    return pd.DataFrame(rows).reset_index(drop=True)


def _build_recovery() -> pd.DataFrame:
    optimized = FORMULATIONS[-1]
    rows = [
        {
            "formulation_id": "PEDOT:PSS/PU-13",
            "metric_name": "cut_stick_elongation_recovery",
            "metric_mean_pct": 75.0,
            "metric_std_pct": 2.2,
            "cycle_count": float("nan"),
            "strain_range_pct": "to_break",
            "energy_dissipation_initial_kJ_m3": float("nan"),
            "energy_dissipation_stable_kJ_m3": float("nan"),
            "stabilization_cycle": float("nan"),
            "test_protocol": "25C_20min_cut_stick_then_tensile_10mm_min",
            "target_role": "direct_cut_stick_mechanical_recovery_summary",
        },
        {
            "formulation_id": optimized["formulation_id"],
            "metric_name": "cut_stick_elongation_recovery",
            "metric_mean_pct": 98.0,
            "metric_std_pct": 0.2,
            "cycle_count": float("nan"),
            "strain_range_pct": "to_break",
            "energy_dissipation_initial_kJ_m3": float("nan"),
            "energy_dissipation_stable_kJ_m3": float("nan"),
            "stabilization_cycle": float("nan"),
            "test_protocol": "25C_20min_cut_stick_then_tensile_10mm_min",
            "target_role": "direct_cut_stick_mechanical_recovery_summary",
        },
        {
            "formulation_id": optimized["formulation_id"],
            "metric_name": "cyclic_energy_recovery",
            "metric_mean_pct": 75.0,
            "metric_std_pct": float("nan"),
            "cycle_count": 500,
            "strain_range_pct": "0-50",
            "energy_dissipation_initial_kJ_m3": 145.0,
            "energy_dissipation_stable_kJ_m3": 110.0,
            "stabilization_cycle": 100,
            "test_protocol": "500_loading_unloading_cycles_0_to_50pct_strain",
            "target_role": "published_cyclic_energy_recovery_summary_no_raw_curve",
        },
    ]
    formulation_map = {item["formulation_id"]: item for item in FORMULATIONS}
    output = []
    for index, row in enumerate(rows, start=1):
        formulation = formulation_map[row["formulation_id"]]
        output.append(
            {
                "source_id": "source_zenodo_19609901_v1",
                "record_id": f"article_recovery_summary_{index}",
                **formulation,
                **row,
                "source_numeric_level": "published_article_summary",
                "raw_mechanical_cycle_curve_available_in_local_zip": False,
                "model_admission_layer": (
                    "conductive_crosslinked_PU_composite_transfer"
                ),
                "sample_weight_ceiling": 0.25,
                "split_group": f"{DATASET_DOI}|shared_crosslinked_PU_family",
                "dataset_license": "CC-BY-4.0",
                "article_license": "CC-BY-NC-3.0",
                "citation_keys": "reference-199;reference-200",
            }
        )
    return pd.DataFrame(output)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    if _sha256(ARCHIVE) != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("Zenodo导电自修复PU压缩包SHA-256与冻结值不一致")
    with zipfile.ZipFile(ARCHIVE) as archive:
        mechanical = _build_mechanical(archive)
    return mechanical, _build_recovery()


def _manifest(
    mechanical: pd.DataFrame,
    recovery: pd.DataFrame,
    mechanical_hash: str,
    recovery_hash: str,
) -> dict[str, object]:
    with zipfile.ZipFile(ARCHIVE) as archive:
        members = archive.infolist()
        substantive = [
            member
            for member in members
            if not member.is_dir()
            and not member.filename.startswith("__MACOSX/")
            and "/._" not in member.filename
            and not member.filename.endswith(".DS_Store")
        ]
    return {
        "release_id": "zenodo_conductive_self_healing_pu_v1",
        "source": {
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "dataset_license": "CC-BY-4.0",
            "article_license": "CC-BY-NC-3.0",
            "archive_sha256": _sha256(ARCHIVE),
            "source_manifest_sha256": _sha256(SOURCE_MANIFEST),
        },
        "counts": {
            "archive_member_count": len(members),
            "substantive_file_count": len(substantive),
            "formulation_screening_curve_count": int(
                mechanical["record_role"].eq("formulation_screening_tensile").sum()
            ),
            "recycling_state_curve_count": int(
                mechanical["record_role"].eq("recycling_state_tensile").sum()
            ),
            "tensile_curve_point_count": int(mechanical["curve_point_count"].sum()),
            "published_recovery_summary_count": len(recovery),
            "published_compact_row_count": len(mechanical) + len(recovery),
        },
        "policy": {
            "raw_curves_republished": False,
            "figure_pair_order_crosschecked_with_article_table": True,
            "negative_stress_clipped_only_for_area": True,
            "solution_fraction_claimed_as_dry_solid_fraction": False,
            "crosslinked_PU_claimed_as_thermoplastic_TPU_core": False,
            "electrical_cycles_claimed_as_mechanical_cycles": False,
            "raw_mechanical_cycle_curve_present_in_local_zip": False,
            "published_summary_used_when_raw_cycle_curve_absent": True,
        },
        "outputs": {
            MECHANICAL_OUT.name: mechanical_hash,
            RECOVERY_OUT.name: recovery_hash,
        },
    }


def write_release(mechanical: pd.DataFrame, recovery: pd.DataFrame) -> None:
    mechanical.to_csv(
        MECHANICAL_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    recovery.to_csv(
        RECOVERY_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    MANIFEST.write_text(
        json.dumps(
            _manifest(
                mechanical,
                recovery,
                _sha256(MECHANICAL_OUT),
                _sha256(RECOVERY_OUT),
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(mechanical: pd.DataFrame, recovery: pd.DataFrame) -> None:
    if not MECHANICAL_OUT.exists() or not RECOVERY_OUT.exists() or not MANIFEST.exists():
        raise SystemExit("导电自修复PU发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        mechanical_candidate = Path(directory) / MECHANICAL_OUT.name
        recovery_candidate = Path(directory) / RECOVERY_OUT.name
        mechanical.to_csv(
            mechanical_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        recovery.to_csv(
            recovery_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        if _sha256(mechanical_candidate) != _sha256(MECHANICAL_OUT):
            raise SystemExit("导电自修复PU拉伸端点与确定性重建不一致")
        if _sha256(recovery_candidate) != _sha256(RECOVERY_OUT):
            raise SystemExit("导电自修复PU恢复指标与确定性重建不一致")
    expected = _manifest(
        mechanical,
        recovery,
        _sha256(MECHANICAL_OUT),
        _sha256(RECOVERY_OUT),
    )
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("导电自修复PU发布清单不一致")
    print("导电自修复PU检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    mechanical, recovery = build_release()
    if args.检查:
        check_release(mechanical, recovery)
    else:
        write_release(mechanical, recovery)
        print(
            json.dumps(
                {
                    "mechanical_rows": len(mechanical),
                    "recovery_summary_rows": len(recovery),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
