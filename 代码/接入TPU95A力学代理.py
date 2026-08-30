"""重新物化eSUN eTPU-95A历史镜像的载荷-伸长与松弛端点。"""

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Mendeley_TPU95A_TPMS应变率力学"
)
TENSILE_DIR = SOURCE_DIR / "实验文件" / "拉伸"
RELAXATION_DIR = SOURCE_DIR / "实验文件" / "松弛"
SOURCE_METADATA = SOURCE_DIR / "官方DataCite元数据.json"
SOURCE_AUDIT = SOURCE_DIR / "内容审计摘要.json"
CURVE_AUDIT = SOURCE_DIR / "曲线解析清单.tsv"
DIRECTED = ROOT / "结果" / "定向筛选"
TENSILE = DIRECTED / "TPU95A载荷伸长端点.csv"
RELAXATION = DIRECTED / "TPU95A应力松弛端点.csv"
MANIFEST = DIRECTED / "TPU95A力学代理发布清单.json"
RELEASE_ID = "esun-etpu95a-mechanical-proxy-2026-v1"
DATASET_DOI = "10.17632/mc6zh4cwhf.2"
LICENSE = "CC-BY-4.0"
CITATIONS = "reference-192"
MATERIAL_GRADE = "eSUN eTPU-95A"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _read_csv(path: Path) -> list[tuple[float, ...]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, fields in enumerate(csv.reader(handle)):
            if index < 8:
                continue
            try:
                values = tuple(float(value) for value in fields)
            except (TypeError, ValueError):
                continue
            if all(math.isfinite(value) for value in values):
                rows.append(values)
    return rows


def _trapz(points: list[tuple[float, float]]) -> float:
    return sum(
        (x1 - x0) * (max(y0, 0.0) + max(y1, 0.0)) / 2
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False)
        if x1 > x0
    )


def _interpolate(points: list[tuple[float, float]], target: float) -> float:
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x0 <= target <= x1 and x1 != x0:
            return y0 + (target - x0) * (y1 - y0) / (x1 - x0)
    if points and 0 <= target - points[-1][0] <= 0.1:
        return points[-1][1]
    raise ValueError(f"松弛窗口未覆盖{target}s")


def _time_to_retention(
    points: list[tuple[float, float]], target: float
) -> float | None:
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if y0 > target >= y1 and y1 != y0:
            return x0 + (target - y0) * (x1 - x0) / (y1 - y0)
    return None


def _common(weight_ceiling: float) -> dict[str, object]:
    return {
        "release_id": RELEASE_ID,
        "source_id": f"doi:{DATASET_DOI}",
        "material_grade": MATERIAL_GRADE,
        "polymer_family": "commercial_thermoplastic_polyurethane_95A",
        "chemistry_mapping_status": "commercial_grade_identity_only",
        "thermoplastic_tpu_core": True,
        "model_admission_layer": "core_tpu_application_experimental",
        "usage_mode": "mechanical_proxy_supervision",
        "future_weight_ceiling": weight_ceiling,
        "historical_mirror_rematerialized": True,
        "incremental_scientific_sample_contribution": 0,
        "current_release_materialization": True,
        "split_group": f"{DATASET_DOI}|{MATERIAL_GRADE}",
        "license": LICENSE,
        "citation_keys": CITATIONS,
    }


def _build_tensile() -> pd.DataFrame:
    rows = []
    for path in sorted(TENSILE_DIR.glob("sample*.csv")):
        match = re.search(r"sample(\d+)", path.stem, re.IGNORECASE)
        if not match:
            continue
        replicate = int(match.group(1))
        source = _read_csv(path)
        baseline_extension = source[0][3]
        strain_extension_load = [
            ((record[3] - baseline_extension) / 33.0, record[3] - baseline_extension, record[1])
            for record in source
        ]
        maximum_index = max(
            range(len(strain_extension_load)),
            key=lambda index: strain_extension_load[index][0],
        )
        loading = strain_extension_load[: maximum_index + 1]
        work = _trapz(
            [(extension / 1000.0, load) for _, extension, load in loading]
        )
        rows.append(
            {
                **_common(0.20),
                "test_run_id": f"TPU95A_tensile_rep{replicate}",
                "replicate_id": replicate,
                "gauge_length_mm": 33.0,
                "maximum_engineering_strain_percent": 100.0
                * max(strain for strain, _, _ in strain_extension_load),
                "maximum_load_N": max(load for _, _, load in strain_extension_load),
                "load_extension_work_to_max_extension_J": work,
                "source_point_count": len(source),
                "absolute_tensile_stress_available": False,
                "complete_toughness_available": False,
                "blocked_reason": (
                    "unambiguous_narrow_section_width_and_thickness_unavailable"
                ),
                "raw_template_material_label": "PLA",
                "resolved_material_evidence": (
                    "dataset_title_folder_and_audit_identify_eSUN_eTPU95A"
                ),
                "source_file": path.relative_to(ROOT).as_posix(),
                "source_sha256": _sha256(path),
                "prior_registered_mirror_status": "exact_file_sha256_match",
            }
        )
    return pd.DataFrame(rows)


def _build_relaxation() -> pd.DataFrame:
    rows = []
    for path in sorted(RELAXATION_DIR.glob("Strain*.csv")):
        match = re.match(r"Strain0_(\d)_sample(\d+)", path.stem)
        if not match:
            continue
        strain_digit, replicate_text = match.groups()
        nominal_strain = int(strain_digit) / 10.0
        replicate = int(replicate_text)
        source = _read_csv(path)
        peak_index = max(range(len(source)), key=lambda index: source[index][2])
        peak_time = source[peak_index][0]
        peak_load = source[peak_index][2]
        window = [
            (record[0] - peak_time, record[2] / peak_load)
            for record in source[peak_index:]
            if 0 <= record[0] - peak_time <= 100.0
        ]
        time_to_half = _time_to_retention(window, 0.5)
        rows.append(
            {
                **_common(0.35),
                "relaxation_run_id": (
                    f"TPU95A_relax_strain{nominal_strain:.1f}_rep{replicate}"
                ),
                "replicate_id": replicate,
                "nominal_strain_fraction": nominal_strain,
                "source_loading_rate_s_1": 0.1,
                "standardized_post_peak_window_s": 100.0,
                "peak_load_N": peak_load,
                "peak_time_s": peak_time,
                "retention_at_1s": _interpolate(window, 1.0),
                "retention_at_10s": _interpolate(window, 10.0),
                "retention_at_50s": _interpolate(window, 50.0),
                "retention_at_100s_nearest": _interpolate(window, 100.0),
                "time_to_50pct_retention_s": time_to_half,
                "time_to_50pct_status": (
                    "observed_within_100s"
                    if time_to_half is not None
                    else "right_censored_above_100s"
                ),
                "normalized_retention_integral_0_100s": _trapz(window),
                "source_point_count": len(source),
                "absolute_stress_available": False,
                "actual_strain_geometry_status": (
                    "article_height_conflicts_with_raw_displacement_and_filename_strain"
                ),
                "cyclic_target_role": "stress_relaxation_transfer_proxy",
                "source_file": path.relative_to(ROOT).as_posix(),
                "source_sha256": _sha256(path),
                "prior_registered_mirror_status": "exact_file_sha256_match",
            }
        )
    return pd.DataFrame(rows)


@functools.lru_cache(maxsize=1)
def _build_cached() -> tuple[pd.DataFrame, pd.DataFrame]:
    return _build_tensile(), _build_relaxation()


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    return tuple(frame.copy() for frame in _build_cached())


def _write_frames(
    tensile: pd.DataFrame, relaxation: pd.DataFrame, directory: Path
) -> dict[str, Path]:
    paths = {
        "tensile": directory / TENSILE.name,
        "relaxation": directory / RELAXATION.name,
    }
    tensile.to_csv(
        paths["tensile"], index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    relaxation.to_csv(
        paths["relaxation"],
        index=False,
        encoding="utf-8-sig",
        lineterminator="\n",
    )
    return paths


def write_release(tensile: pd.DataFrame, relaxation: pd.DataFrame) -> None:
    DIRECTED.mkdir(parents=True, exist_ok=True)
    paths = _write_frames(tensile, relaxation, DIRECTED)
    payload = {
        "release_id": RELEASE_ID,
        "counts": {
            "material_grade_count": 1,
            "tensile_run_count": len(tensile),
            "tensile_source_point_count": int(tensile["source_point_count"].sum()),
            "relaxation_run_count": len(relaxation),
            "relaxation_source_point_count": int(
                relaxation["source_point_count"].sum()
            ),
            "published_compact_row_count": len(tensile) + len(relaxation),
            "incremental_scientific_sample_contribution": 0,
        },
        "source": {
            "dataset_doi": DATASET_DOI,
            "license": LICENSE,
            "metadata": _entry(SOURCE_METADATA),
            "source_audit": _entry(SOURCE_AUDIT),
            "curve_audit": _entry(CURVE_AUDIT),
            "experimental_files": [
                _entry(path)
                for path in sorted(TENSILE_DIR.glob("sample*.csv"))
                + sorted(RELAXATION_DIR.glob("Strain*.csv"))
            ],
        },
        "policy": {
            "historical_mirror_rematerialized": True,
            "raw_curves_republished": False,
            "independent_new_source_claimed": False,
            "absolute_tensile_toughness_available": False,
            "stress_relaxation_is_recovery_proxy_not_direct_cycle": True,
            "compression_curves_deferred_as_non_target_duplicate": True,
        },
        "outputs": {key: _entry(path) for key, path in paths.items()},
    }
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_release(tensile: pd.DataFrame, relaxation: pd.DataFrame) -> None:
    with tempfile.TemporaryDirectory(prefix="tpu95a-proxy-check-") as directory:
        temporary = _write_frames(tensile, relaxation, Path(directory))
        published = {"tensile": TENSILE, "relaxation": RELAXATION}
        mismatches = [
            key
            for key in published
            if _sha256(temporary[key]) != _sha256(published[key])
        ]
        if mismatches:
            raise SystemExit(f"TPU95A力学代理输出不一致: {','.join(mismatches)}")
    print("TPU95A力学代理数据检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    tensile, relaxation = build_release()
    if args.检查:
        check_release(tensile, relaxation)
    else:
        write_release(tensile, relaxation)
        print(
            json.dumps(
                {
                    "tensile_runs": len(tensile),
                    "relaxation_runs": len(relaxation),
                    "historical_mirror": True,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
