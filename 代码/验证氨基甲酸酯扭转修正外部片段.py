"""在独立取代片段和六角度网格上验证家族特异松弛扭转修正。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from 汇总RESP敏感性 import sha256


EXPECTED_ANGLES = {-180, -120, -60, 0, 60, 120}
KEYS = ["fragment_name", "validation_family", "requested_angle_degrees"]


def verify_manifest_files(directory: Path, manifest: dict[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"外部扭转验证清单缺文件表: {directory}")
    root = directory.resolve()
    for name, record in files.items():
        path = (root / str(name)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"外部扭转验证文件越界: {name}") from exc
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"外部扭转验证文件哈希不闭合: {path}")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def circular_angle_difference(left: float, right: float) -> float:
    return abs(((float(left) - float(right) + 180.0) % 360.0) - 180.0)


def _correction(
    angles_degrees: np.ndarray, coefficients: pd.DataFrame
) -> np.ndarray:
    radians = np.deg2rad(np.asarray(angles_degrees, dtype=float))
    values = np.zeros(len(radians), dtype=float)
    for row in coefficients.to_dict(orient="records"):
        periodicity = int(row["periodicity"])
        coefficient = float(
            row["coefficient_for_cos_nphi_minus_one_kcal_mol"]
        )
        values += coefficient * (np.cos(periodicity * radians) - 1.0)
    return values


def validate_and_score(
    dft: pd.DataFrame,
    mm: pd.DataFrame,
    coefficients: pd.DataFrame,
    *,
    rmse_limit: float = 1.5,
    maximum_error_limit: float = 3.0,
    barrier_error_limit: float = 3.0,
    minimum_angle_limit: float = 30.0,
    dft_drift_limit: float = 0.5,
    mm_drift_limit: float = 2.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dft_required = {
        *KEYS,
        "point_status",
        "angle_drift_degrees",
        "relaxed_dft_relative_energy_kcal_mol",
    }
    mm_required = {
        *KEYS,
        "point_status",
        "angle_drift_degrees",
        "relaxed_gaff2_relative_energy_kcal_mol",
    }
    coefficient_required = {
        "validation_family",
        "periodicity",
        "coefficient_for_cos_nphi_minus_one_kcal_mol",
    }
    missing = {
        "dft": sorted(dft_required.difference(dft.columns)),
        "mm": sorted(mm_required.difference(mm.columns)),
        "coefficients": sorted(coefficient_required.difference(coefficients.columns)),
    }
    if any(missing.values()):
        raise ValueError(f"外部扭转验证输入缺字段: {missing}")
    if dft.duplicated(KEYS).any() or mm.duplicated(KEYS).any():
        raise ValueError("外部扭转验证点键重复")
    if not dft["point_status"].eq("completed").all() or not mm[
        "point_status"
    ].eq("completed").all():
        raise ValueError("外部扭转验证要求DFT和MM全部完成")
    joined = dft.merge(
        mm[
            [
                *KEYS,
                "angle_drift_degrees",
                "relaxed_gaff2_relative_energy_kcal_mol",
            ]
        ].rename(columns={"angle_drift_degrees": "mm_angle_drift_degrees"}),
        on=KEYS,
        how="inner",
        validate="one_to_one",
    ).rename(columns={"angle_drift_degrees": "dft_angle_drift_degrees"})
    if len(joined) != len(dft) or len(joined) != len(mm):
        raise ValueError("外部DFT/MM角度未完全连接")
    metric_rows = []
    evaluated = []
    for (fragment, family), subset in joined.groupby(
        ["fragment_name", "validation_family"], sort=True
    ):
        if set(subset["requested_angle_degrees"].astype(int)) != EXPECTED_ANGLES:
            raise ValueError(f"外部六角度网格不闭合: {fragment}")
        family_coefficients = coefficients.loc[
            coefficients["validation_family"].astype(str).eq(str(family))
        ].sort_values("periodicity", kind="stable")
        if family_coefficients.empty or len(family_coefficients) > 2:
            raise ValueError(f"外部验证缺少低阶家族系数: {family}")
        frame = subset.sort_values("requested_angle_degrees", kind="stable").copy()
        correction = _correction(
            frame["requested_angle_degrees"].to_numpy(float), family_coefficients
        )
        frame["torsion_correction_candidate_kcal_mol"] = correction
        unshifted = (
            frame["relaxed_gaff2_relative_energy_kcal_mol"].to_numpy(float)
            + correction
        )
        corrected = unshifted - unshifted.min()
        dft_energy = frame["relaxed_dft_relative_energy_kcal_mol"].to_numpy(float)
        raw_error = (
            frame["relaxed_gaff2_relative_energy_kcal_mol"].to_numpy(float)
            - dft_energy
        )
        corrected_error = corrected - dft_energy
        frame["corrected_gaff2_relaxed_relative_energy_kcal_mol"] = corrected
        frame["raw_gaff2_minus_dft_kcal_mol"] = raw_error
        frame["corrected_gaff2_minus_dft_kcal_mol"] = corrected_error
        dft_min_angles = frame.loc[
            np.isclose(dft_energy, dft_energy.min(), rtol=0.0, atol=1e-10),
            "requested_angle_degrees",
        ].to_numpy(float)
        corrected_min_angles = frame.loc[
            np.isclose(corrected, corrected.min(), rtol=0.0, atol=1e-10),
            "requested_angle_degrees",
        ].to_numpy(float)
        minimum_angle_error = min(
            circular_angle_difference(left, right)
            for left in dft_min_angles
            for right in corrected_min_angles
        )
        rmse = float(np.sqrt(np.mean(np.square(corrected_error))))
        maximum_error = float(np.max(np.abs(corrected_error)))
        barrier_error = float(abs(corrected.max() - dft_energy.max()))
        maximum_dft_drift = float(frame["dft_angle_drift_degrees"].max())
        maximum_mm_drift = float(frame["mm_angle_drift_degrees"].max())
        gates = {
            "rmse_gate": rmse <= rmse_limit,
            "maximum_error_gate": maximum_error <= maximum_error_limit,
            "barrier_error_gate": barrier_error <= barrier_error_limit,
            "minimum_angle_gate": minimum_angle_error <= minimum_angle_limit,
            "dft_angle_drift_gate": maximum_dft_drift <= dft_drift_limit,
            "mm_angle_drift_gate": maximum_mm_drift <= mm_drift_limit,
        }
        metric_rows.append(
            {
                "fragment_name": fragment,
                "validation_family": family,
                "external_rmse_kcal_mol": rmse,
                "external_maximum_absolute_error_kcal_mol": maximum_error,
                "external_barrier_error_kcal_mol": barrier_error,
                "external_minimum_angle_error_degrees": minimum_angle_error,
                "maximum_dft_angle_drift_degrees": maximum_dft_drift,
                "maximum_mm_angle_drift_degrees": maximum_mm_drift,
                **gates,
                "external_validation_pass": all(gates.values()),
            }
        )
        evaluated.append(frame)
    return (
        pd.concat(evaluated, ignore_index=True),
        pd.DataFrame(metric_rows).sort_values("validation_family", kind="stable"),
    )


def write_release(
    dft_paths: Sequence[Path],
    mm_paths: Sequence[Path],
    coefficient_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if len(dft_paths) not in {1, 2} or len(mm_paths) != len(dft_paths):
        raise ValueError("外部扭转验证必须包含一至两个、且DFT/MM数量相等的家族目录")
    for path in [*dft_paths, *mm_paths, coefficient_path]:
        if not path.exists():
            raise ValueError(f"外部扭转验证输入不存在: {path}")
    dft_tables = []
    mm_tables = []
    input_records: dict[str, dict[str, Any]] = {}
    for directory in dft_paths:
        manifest_path = directory / "受约束松弛清单.json"
        table_path = directory / "relaxed_scan.csv"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "completed_constrained_relaxed_dft_points":
            raise ValueError(f"外部DFT未完成: {directory}")
        verify_manifest_files(directory, manifest)
        dft_tables.append(pd.read_csv(table_path))
        input_records[str(directory)] = {
            "manifest_sha256": sha256(manifest_path),
            "table_sha256": sha256(table_path),
        }
    for directory in mm_paths:
        manifest_path = directory / "MM约束松弛清单.json"
        table_path = directory / "mm_relaxed_scan.csv"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "completed_mm_constrained_relaxed_points":
            raise ValueError(f"外部MM未完成: {directory}")
        verify_manifest_files(directory, manifest)
        mm_tables.append(pd.read_csv(table_path))
        input_records[str(directory)] = {
            "manifest_sha256": sha256(manifest_path),
            "table_sha256": sha256(table_path),
        }
    evaluated, metrics = validate_and_score(
        pd.concat(dft_tables, ignore_index=True),
        pd.concat(mm_tables, ignore_index=True),
        pd.read_csv(coefficient_path),
    )
    all_pass = bool(metrics["external_validation_pass"].all())
    evaluated_family_count = int(metrics["validation_family"].nunique())
    complete_family_coverage = evaluated_family_count == 2
    output_root.mkdir(parents=True, exist_ok=True)
    evaluated_out = output_root / "外部片段逐角评估.csv"
    metrics_out = output_root / "外部片段验证指标.csv"
    report_out = output_root / "外部片段验证说明.md"
    _atomic_text(
        evaluated_out, evaluated.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(metrics_out, metrics.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 氨基甲酸酯扭转修正外部片段验证",
                "",
                "验证片段与训练片段不同；每个家族使用−180、−120、−60、0、60、120°六点同角度DFT/MM松弛面。",
                "预声明门为RMSE≤1.5、最大绝对误差≤3.0、势垒误差≤3.0 kcal mol⁻¹、最低点角差≤30°，并同时约束DFT/MM角漂移。",
                "",
                (
                    "两个家族均通过外部片段门；下一步仍是完整低聚链映射和凝聚相商业对照验证。"
                    if all_pass and complete_family_coverage
                    else "当前已评估家族通过，但另一家族仍待完成；partial结果不放行生产力场。"
                    if all_pass
                    else "至少一个家族未通过外部片段门；不得把候选系数写入生产力场，也不得通过提高四点训练阶数规避失败。"
                ),
                "",
            ]
        ),
    )
    files = [evaluated_out, metrics_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": (
            "external_fragment_validation_passed_full_chain_validation_pending"
            if all_pass and complete_family_coverage
            else "external_fragment_validation_partial_family_passed_other_family_pending"
            if all_pass
            else "external_fragment_validation_failed"
        ),
        "counts": {
            "fragments": metrics["fragment_name"].nunique(),
            "points": len(evaluated),
            "families_evaluated": evaluated_family_count,
            "families_planned": 2,
            "families_passed": int(metrics["external_validation_pass"].sum()),
        },
        "thresholds": {
            "rmse_kcal_mol": 1.5,
            "maximum_absolute_error_kcal_mol": 3.0,
            "barrier_error_kcal_mol": 3.0,
            "minimum_angle_error_degrees": 30.0,
            "dft_angle_drift_degrees": 0.5,
            "mm_angle_drift_degrees": 2.0,
        },
        "inputs": {
            **input_records,
            str(coefficient_path): {"sha256": sha256(coefficient_path)},
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": (
            "blocked_full_chain_mapping_and_condensed_phase_control_validation"
            if all_pass and complete_family_coverage
            else "blocked_other_external_family_pending"
            if all_pass
            else "blocked_external_fragment_validation_failed"
        ),
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "外部片段验证发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--DFT目录", type=Path, action="append", required=True)
    parser.add_argument("--MM目录", type=Path, action="append", required=True)
    parser.add_argument("--候选系数", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.DFT目录,
        args.MM目录,
        args.候选系数,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if (
        manifest["status"].startswith("external_fragment_validation_passed")
        or manifest["status"].startswith("external_fragment_validation_partial")
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
