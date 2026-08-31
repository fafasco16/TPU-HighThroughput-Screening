"""接入Mendeley TPU三重复实验曲线及同家族仿真参考。"""

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
    / "Mendeley_TPU实验仿真曲线"
)
ARCHIVE = SOURCE_DIR / "kysnxmy7xw-1.zip"
EXPERIMENT_OUT = ROOT / "结果" / "定向筛选" / "TPU实验100pct拉伸端点.csv"
SIMULATION_OUT = ROOT / "结果" / "定向筛选" / "TPU仿真应力应变参考.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "TPU实验仿真发布清单.json"

DATASET_DOI = "10.17632/kysnxmy7xw.1"
EXPECTED_ARCHIVE_SHA256 = (
    "3585c67dac25988b651999d4a9b25ca3fb55da1a25b05386fbbf8fa8a87cf55e"
)
EXPERIMENT_MEMBER = (
    "S-S Curve for TPU Experiment/Raw Data Experiment TPU.xlsx"
)
SIMULATION_MEMBER = (
    "S-S Curve for TPU Experiment/Comparison Excel Experiment and Simulation.xlsx"
)
SIMULATION_SHEETS = [
    "Sheet1",
    "Static_Increment_100000_1",
    "Sheet2",
    "Dynamic_Increment_1000_True_1",
    "Dynamic_Increment_1000",
    "Dynamic_Increment_1000_2",
    "Try_SG_M1_Centroid_SS_LE",
    "Try_SG_M2_Centroid_SS_LE",
    "Try_SG_M3_Centroid_SS_LE",
    "Try_SG_M4_Centroid_SS_LE",
    "Try_SG_M5_Integration_SS_LE",
    "Try_DI_M1-DI_Centroid_SS_LE",
    "SG_Lab_3",
]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_curve(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.apply(pd.to_numeric, errors="coerce")
        .dropna()
        .groupby("strain", as_index=False)["stress_MPa"]
        .median()
        .sort_values("strain")
        .reset_index(drop=True)
    )


def _curve_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return _sha256_bytes(payload)


def _modulus_0_to_5pct(frame: pd.DataFrame) -> float:
    region = frame.loc[frame["strain"].between(0, 0.05)]
    if len(region) < 10:
        return float("nan")
    return float(np.polyfit(region["strain"], region["stress_MPa"], 1)[0])


def _endpoints(frame: pd.DataFrame) -> dict[str, object]:
    peak_index = int(frame["stress_MPa"].idxmax())
    nonnegative = frame["stress_MPa"].clip(lower=0)
    return {
        "curve_point_count": int(len(frame)),
        "minimum_observed_strain": float(frame["strain"].min()),
        "maximum_observed_strain": float(frame["strain"].max()),
        "minimum_observed_stress_MPa": float(frame["stress_MPa"].min()),
        "peak_stress_MPa": float(frame.loc[peak_index, "stress_MPa"]),
        "strain_at_peak_stress": float(frame.loc[peak_index, "strain"]),
        "stress_at_100pct_strain_MPa": float(
            np.interp(1.0, frame["strain"], frame["stress_MPa"])
        )
        if frame["strain"].max() >= 1.0
        else float("nan"),
        "partial_tensile_energy_MJ_m3": float(
            np.trapezoid(nonnegative, frame["strain"])
        ),
        "youngs_modulus_0_5pct_MPa": _modulus_0_to_5pct(frame),
        "negative_stress_clipped_for_area": bool(frame["stress_MPa"].lt(0).any()),
        "curve_sha256": _curve_sha256(frame),
    }


def _read_experiments(payload: bytes) -> list[pd.DataFrame]:
    source = pd.read_excel(io.BytesIO(payload), sheet_name="Sheet1", header=None)
    curves = []
    for replicate in range(3):
        frame = pd.DataFrame(
            {
                "strain": source.iloc[3:, replicate],
                "stress_MPa": source.iloc[3:, replicate + 5],
            }
        )
        curves.append(_canonical_curve(frame))
    return curves


def _detect_simulation_curve(
    source: pd.DataFrame, sheet: str
) -> tuple[pd.DataFrame, int]:
    header_row = None
    stress_column = None
    strain_column = None
    for row in range(min(3, len(source))):
        for column, value in enumerate(source.iloc[row].tolist()):
            normalized = str(value).strip().lower()
            if normalized == "stress":
                stress_column = column
                header_row = row
            elif normalized == "strain":
                strain_column = column
                header_row = row
        if stress_column is not None and strain_column is not None:
            break
    if stress_column is None or strain_column is None or header_row is None:
        raise ValueError(f"无法识别仿真曲线列: {sheet}")
    frame = pd.DataFrame(
        {
            "strain": source.iloc[header_row + 1 :, strain_column],
            "stress_MPa": source.iloc[header_row + 1 :, stress_column],
        }
    )
    numeric = frame.apply(pd.to_numeric, errors="coerce").dropna()
    return _canonical_curve(frame), int(len(numeric))


def _experimental_reference(curves: list[pd.DataFrame]) -> pd.DataFrame:
    lower = max(float(curve["strain"].min()) for curve in curves)
    upper = min(float(curve["strain"].max()) for curve in curves)
    grid = np.linspace(lower, upper, 1001)
    stresses = np.vstack(
        [
            np.interp(grid, curve["strain"], curve["stress_MPa"])
            for curve in curves
        ]
    )
    return pd.DataFrame(
        {"strain": grid, "stress_MPa": np.median(stresses, axis=0)}
    )


def _comparison_metrics(
    simulation: pd.DataFrame, reference: pd.DataFrame
) -> dict[str, object]:
    lower = max(float(simulation["strain"].min()), float(reference["strain"].min()))
    upper = min(float(simulation["strain"].max()), float(reference["strain"].max()))
    if upper <= lower:
        return {
            "experimental_overlap_fraction": 0.0,
            "experimental_comparison_point_count": 0,
            "experimental_RMSE_MPa": float("nan"),
            "experimental_MAE_MPa": float("nan"),
        }
    grid = np.linspace(lower, upper, 501)
    simulated = np.interp(
        grid, simulation["strain"], simulation["stress_MPa"]
    )
    observed = np.interp(grid, reference["strain"], reference["stress_MPa"])
    residual = simulated - observed
    reference_range = float(reference["strain"].max() - reference["strain"].min())
    return {
        "experimental_overlap_fraction": float((upper - lower) / reference_range),
        "experimental_comparison_point_count": int(len(grid)),
        "experimental_RMSE_MPa": float(np.sqrt(np.mean(residual**2))),
        "experimental_MAE_MPa": float(np.mean(np.abs(residual))),
    }


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    if _sha256(ARCHIVE) != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("Mendeley TPU实验仿真归档SHA-256与冻结值不一致")
    with zipfile.ZipFile(ARCHIVE) as archive:
        experiment_payload = archive.read(EXPERIMENT_MEMBER)
        simulation_payload = archive.read(SIMULATION_MEMBER)
    experiment_curves = _read_experiments(experiment_payload)
    experiment_rows = []
    for replicate, curve in enumerate(experiment_curves, start=1):
        experiment_rows.append(
            {
                "source_id": "source_mendeley_kysnxmy7xw_v1",
                "formulation_id": "TPU_unknown_grade_kysnxmy7xw",
                "replicate_id": replicate,
                **_endpoints(curve),
                "complete_fracture_observed": False,
                "complete_toughness_available": False,
                "target_role": "direct_partial_tensile_energy_to_100pct",
                "chemistry_mapping_status": "TPU_grade_unreported",
                "model_admission_layer": "core_TPU_application_experimental",
                "sample_weight_ceiling": 0.25,
                "split_group": f"{DATASET_DOI}|TPU_unknown_grade",
                "source_locator": f"{ARCHIVE.name}!/{EXPERIMENT_MEMBER}#replicate={replicate}",
                "source_member_sha256": _sha256_bytes(experiment_payload),
                "license": "CC-BY-4.0",
                "citation_keys": "reference-94",
            }
        )
    experiments = pd.DataFrame(experiment_rows)

    reference = _experimental_reference(experiment_curves)
    simulation_rows = []
    workbook = pd.ExcelFile(io.BytesIO(simulation_payload))
    for sheet in SIMULATION_SHEETS:
        source = pd.read_excel(
            io.BytesIO(simulation_payload), sheet_name=sheet, header=None
        )
        curve, source_point_count = _detect_simulation_curve(source, sheet)
        simulation_rows.append(
            {
                "source_id": "source_mendeley_kysnxmy7xw_v1",
                "formulation_id": "TPU_unknown_grade_kysnxmy7xw",
                "simulation_condition_id": sheet,
                "source_curve_point_count": source_point_count,
                **_endpoints(curve),
                **_comparison_metrics(curve, reference),
                "simulation_family_id": "simulation_family_comparison_workbook_1",
                "solver_available": False,
                "mesh_available": False,
                "material_parameters_available": False,
                "simulation_protocol_complete": False,
                "model_ready": False,
                "potential_weight_ceiling": 0.10,
                "actual_training_weight": 0.0,
                "target_role": "same_experiment_calibration_family_reference",
                "model_admission_layer": "simulation_calibration_reference",
                "split_group": f"{DATASET_DOI}|TPU_unknown_grade|simulation_family",
                "source_locator": f"{ARCHIVE.name}!/{SIMULATION_MEMBER}#sheet={sheet}",
                "source_member_sha256": _sha256_bytes(simulation_payload),
                "license": "CC-BY-4.0",
                "citation_keys": "reference-94",
            }
        )
    if set(SIMULATION_SHEETS) - set(workbook.sheet_names):
        raise ValueError("仿真工作表清单不完整")
    simulations = pd.DataFrame(simulation_rows).sort_values(
        "simulation_condition_id"
    ).reset_index(drop=True)
    return experiments, simulations


def _manifest(
    experiments: pd.DataFrame,
    simulations: pd.DataFrame,
    experiment_hash: str,
    simulation_hash: str,
) -> dict[str, object]:
    return {
        "release_id": "mendeley_tpu_experiment_simulation_v1",
        "source": {
            "dataset_doi": DATASET_DOI,
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "experimental_material_count": 1,
            "experimental_specimen_count": len(experiments),
            "experimental_curve_point_count": int(
                experiments["curve_point_count"].sum()
            ),
            "simulation_family_count": int(
                simulations["simulation_family_id"].nunique()
            ),
            "simulation_run_count": len(simulations),
            "simulation_source_curve_point_count": int(
                simulations["source_curve_point_count"].sum()
            ),
            "simulation_unique_strain_point_count": int(
                simulations["curve_point_count"].sum()
            ),
            "published_compact_row_count": len(experiments) + len(simulations),
        },
        "policy": {
            "average_columns_counted_as_independent_specimens": False,
            "partial_100pct_energy_claimed_as_fracture_toughness": False,
            "simulation_runs_counted_as_independent_materials": False,
            "simulation_calibration_counted_as_external_validation": False,
            "simulation_weight_positive_without_protocol": False,
            "negative_stress_clipped_only_for_area": True,
        },
        "outputs": {
            EXPERIMENT_OUT.name: experiment_hash,
            SIMULATION_OUT.name: simulation_hash,
        },
    }


def write_release(experiments: pd.DataFrame, simulations: pd.DataFrame) -> None:
    experiments.to_csv(
        EXPERIMENT_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    simulations.to_csv(
        SIMULATION_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    MANIFEST.write_text(
        json.dumps(
            _manifest(
                experiments,
                simulations,
                _sha256(EXPERIMENT_OUT),
                _sha256(SIMULATION_OUT),
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(experiments: pd.DataFrame, simulations: pd.DataFrame) -> None:
    if not EXPERIMENT_OUT.exists() or not SIMULATION_OUT.exists() or not MANIFEST.exists():
        raise SystemExit("TPU实验仿真发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        experiment_candidate = Path(directory) / EXPERIMENT_OUT.name
        simulation_candidate = Path(directory) / SIMULATION_OUT.name
        experiments.to_csv(
            experiment_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        simulations.to_csv(
            simulation_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        if _sha256(experiment_candidate) != _sha256(EXPERIMENT_OUT):
            raise SystemExit("TPU实验端点与确定性重建不一致")
        if _sha256(simulation_candidate) != _sha256(SIMULATION_OUT):
            raise SystemExit("TPU仿真参考与确定性重建不一致")
    expected = _manifest(
        experiments,
        simulations,
        _sha256(EXPERIMENT_OUT),
        _sha256(SIMULATION_OUT),
    )
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("TPU实验仿真发布清单不一致")
    print("TPU实验仿真检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    experiments, simulations = build_release()
    if args.检查:
        check_release(experiments, simulations)
    else:
        write_release(experiments, simulations)
        print(
            json.dumps(
                {
                    "experimental_specimens": len(experiments),
                    "simulation_runs": len(simulations),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
