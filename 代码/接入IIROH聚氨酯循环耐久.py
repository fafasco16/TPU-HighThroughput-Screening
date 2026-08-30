"""物化IIR-OH聚氨酯的100圈循环与水解前后耐久端点。"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十八批实验_IIR-OH聚氨酯"
)
ARCHIVE = SOURCE_DIR / "wg3znh66bv-1.zip"
SOURCE_METADATA = SOURCE_DIR / "来源元数据.json"
SOURCE_AUDIT = SOURCE_DIR / "内容审计摘要.json"
CURVE_AUDIT = SOURCE_DIR / "曲线审计清单.tsv"
DIRECTED = ROOT / "结果" / "定向筛选"
CYCLIC = DIRECTED / "IIR-OH聚氨酯循环端点.csv"
AGING = DIRECTED / "IIR-OH聚氨酯水解保持端点.csv"
MANIFEST = DIRECTED / "IIR-OH聚氨酯循环耐久发布清单.json"
RELEASE_ID = "iir-oh-pu-cyclic-durability-2026-v1"
DATASET_DOI = "10.17632/wg3znh66bv.1"
PREPRINT_DOI = "10.2139/ssrn.6767133"
LICENSE = "CC-BY-4.0"
CITATIONS = "reference-190;reference-191"


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


def _finite(value: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_cyclic(data: bytes) -> list[tuple[float, float]]:
    text = data.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    points = []
    for line in text.splitlines()[1:]:
        fields = line.strip().split("\t")
        if len(fields) < 2:
            continue
        strain = _finite(fields[0])
        stress = _finite(fields[-1])
        if strain is not None and stress is not None:
            points.append((strain, stress))
    return points


def _parse_aging(data: bytes) -> list[tuple[float, float]]:
    text = data.decode("utf-8-sig", errors="replace")
    points = []
    for line in text.splitlines()[2:]:
        fields = line.split()
        if len(fields) < 6:
            continue
        stress = _finite(fields[4])
        strain = _finite(fields[5])
        if strain is not None and stress is not None:
            points.append((strain, stress))
    return points


def _segment_cycles(
    points: list[tuple[float, float]], low: float = 0.5, high: float = 45.0
) -> list[list[tuple[float, float]]]:
    cycles = []
    state = "seek_low"
    current: list[tuple[float, float]] = []
    for point in points:
        strain, _ = point
        if state == "seek_low":
            if strain <= low:
                current = [point]
                state = "loading"
        elif state == "loading":
            current.append(point)
            if strain >= high:
                state = "unloading"
        else:
            current.append(point)
            if strain <= low:
                cycles.append(current)
                current = []
                state = "loading"
    return cycles


def _path_area(
    points: list[tuple[float, float]], direction: str
) -> float:
    area = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        delta = x1 - x0
        if (direction == "loading" and delta > 0) or (
            direction == "unloading" and delta < 0
        ):
            area += abs(delta) * (max(y0, 0.0) + max(y1, 0.0)) / 2
    return area / 100.0


def _curve_endpoints(points: list[tuple[float, float]]) -> dict[str, float]:
    return {
        "maximum_stress_MPa": max(stress for _, stress in points),
        "maximum_strain_percent": max(strain for strain, _ in points),
        "stress_strain_area_to_last_point_MJ_m3": _path_area(
            points, "loading"
        ),
    }


def _common(formulation: str, weight_ceiling: float) -> dict[str, object]:
    diisocyanate = formulation.split("-")[0]
    return {
        "release_id": RELEASE_ID,
        "source_id": f"doi:{DATASET_DOI}",
        "formulation_id": formulation,
        "polymer_family": "IIR_OH_crosslinked_polyurethane_network",
        "soft_segment_family": "hydroxylated_butyl_rubber_IIR_OH",
        "diisocyanate_family": diisocyanate,
        "formulation_code_value": 4,
        "formulation_semantics_status": "numeric_code_4_unresolved",
        "chemistry_mapping_status": "polymer_family_diisocyanate_code_mapped",
        "thermoplastic_tpu_core": False,
        "model_admission_layer": "polyurethane_adjacent_experimental",
        "usage_mode": "cyclic_and_durability_transfer_supervision",
        "future_weight_ceiling": weight_ceiling,
        "split_group": f"{DATASET_DOI}|{formulation}",
        "license": LICENSE,
        "citation_keys": CITATIONS,
    }


def _build_cyclic(
    archive: ZipFile, audit: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    selected = audit.loc[audit["category"].eq("cyclic_tensile_raw")]
    for record in selected.to_dict(orient="records"):
        member = str(record["member_path"])
        filename = PurePosixPath(member).name
        match = re.match(r"(HDI-4|HMDI-4)-\(C0-50\)-(\d)\.txt", filename)
        if not match:
            raise ValueError(f"无法解析循环文件名: {filename}")
        formulation, replicate = match.groups()
        points = _parse_cyclic(archive.read(member))
        if len(points) != int(record["point_count"]):
            raise ValueError(f"{filename}点数与审计不一致")
        cycles = _segment_cycles(points)
        if len(cycles) != 100:
            raise ValueError(f"{filename}分割得到{len(cycles)}圈，不是100圈")
        payloads = []
        for cycle_number, cycle in enumerate(cycles, start=1):
            peak_index = max(
                range(len(cycle)), key=lambda index: cycle[index][0]
            )
            loading = cycle[: peak_index + 1]
            unloading = cycle[peak_index:]
            loading_energy = _path_area(loading, "loading")
            unloading_energy = _path_area(unloading, "unloading")
            payloads.append(
                {
                    "cycle_number": cycle_number,
                    "maximum_strain_percent": max(x for x, _ in cycle),
                    "peak_stress_MPa": max(y for _, y in cycle),
                    "loading_energy_MJ_m3": loading_energy,
                    "unloading_energy_MJ_m3": unloading_energy,
                    "hysteresis_energy_MJ_m3": max(
                        loading_energy - unloading_energy, 0.0
                    ),
                    "cycle_point_count": len(cycle),
                }
            )
        first_peak = payloads[0]["peak_stress_MPa"]
        first_hysteresis = payloads[0]["hysteresis_energy_MJ_m3"]
        for payload in payloads:
            rows.append(
                {
                    **_common(formulation, 0.45),
                    "cyclic_run_id": f"{formulation}_rep{replicate}",
                    "replicate_id": int(replicate),
                    **payload,
                    "peak_stress_retention_percent": 100.0
                    * payload["peak_stress_MPa"]
                    / first_peak,
                    "hysteresis_retention_percent": (
                        100.0
                        * payload["hysteresis_energy_MJ_m3"]
                        / first_hysteresis
                        if first_hysteresis > 0
                        else math.nan
                    ),
                    "dissipation_fraction": (
                        payload["hysteresis_energy_MJ_m3"]
                        / payload["loading_energy_MJ_m3"]
                        if payload["loading_energy_MJ_m3"] > 0
                        else math.nan
                    ),
                    "cycle_strain_range_percent": "0-50",
                    "segmentation_low_threshold_percent": 0.5,
                    "segmentation_high_threshold_percent": 45.0,
                    "cyclic_target_role": (
                        "peak_stress_and_hysteresis_retention_transfer"
                    ),
                    "direct_shape_recovery_available": False,
                    "shape_recovery_ratio_percent": math.nan,
                    "source_member": member,
                    "source_curve_sha256": record["member_sha256"],
                    "source_run_point_count": len(points),
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["formulation_id", "replicate_id", "cycle_number"]
    ).reset_index(drop=True)


def _build_aging(archive: ZipFile, audit: pd.DataFrame) -> pd.DataFrame:
    selected = audit.loc[
        audit["category"].eq("hydrolytic_aging_tensile_raw")
    ]
    endpoints = {}
    point_total = 0
    for record in selected.to_dict(orient="records"):
        member = str(record["member_path"])
        filename = PurePosixPath(member).name
        match = re.match(r"(HDI-4|HMDI-4)-(Before|After)-(\d)\.txt", filename)
        if not match:
            raise ValueError(f"无法解析水解文件名: {filename}")
        formulation, state, replicate = match.groups()
        points = _parse_aging(archive.read(member))
        if len(points) != int(record["point_count"]):
            raise ValueError(f"{filename}点数与审计不一致")
        point_total += len(points)
        endpoints[(formulation, int(replicate), state.lower())] = {
            **_curve_endpoints(points),
            "source_member": member,
            "source_curve_sha256": record["member_sha256"],
            "source_point_count": len(points),
        }
    rows = []
    for formulation in ("HDI-4", "HMDI-4"):
        for replicate in (1, 2, 3):
            before = endpoints[(formulation, replicate, "before")]
            after = endpoints[(formulation, replicate, "after")]
            rows.append(
                {
                    **_common(formulation, 0.35),
                    "replicate_id": replicate,
                    "pairing_status": (
                        "matched_formulation_and_replicate_not_proven_same_specimen"
                    ),
                    "aging_condition_status": (
                        "hydrolytic_aging_source_protocol_unresolved"
                    ),
                    "before_maximum_stress_MPa": before[
                        "maximum_stress_MPa"
                    ],
                    "after_maximum_stress_MPa": after["maximum_stress_MPa"],
                    "peak_stress_retention_percent": 100.0
                    * after["maximum_stress_MPa"]
                    / before["maximum_stress_MPa"],
                    "before_maximum_strain_percent": before[
                        "maximum_strain_percent"
                    ],
                    "after_maximum_strain_percent": after[
                        "maximum_strain_percent"
                    ],
                    "maximum_strain_retention_percent": 100.0
                    * after["maximum_strain_percent"]
                    / before["maximum_strain_percent"],
                    "before_curve_area_MJ_m3": before[
                        "stress_strain_area_to_last_point_MJ_m3"
                    ],
                    "after_curve_area_MJ_m3": after[
                        "stress_strain_area_to_last_point_MJ_m3"
                    ],
                    "curve_area_retention_percent": 100.0
                    * after["stress_strain_area_to_last_point_MJ_m3"]
                    / before["stress_strain_area_to_last_point_MJ_m3"],
                    "before_source_member": before["source_member"],
                    "after_source_member": after["source_member"],
                    "before_source_sha256": before["source_curve_sha256"],
                    "after_source_sha256": after["source_curve_sha256"],
                    "before_point_count": before["source_point_count"],
                    "after_point_count": after["source_point_count"],
                    "durability_target_role": (
                        "hydrolytic_mechanical_retention_transfer"
                    ),
                }
            )
    frame = pd.DataFrame(rows).sort_values(
        ["formulation_id", "replicate_id"]
    ).reset_index(drop=True)
    frame.attrs["source_point_count"] = point_total
    return frame


@functools.lru_cache(maxsize=1)
def _build_cached() -> tuple[pd.DataFrame, pd.DataFrame]:
    audit = pd.read_csv(CURVE_AUDIT, sep="\t")
    with ZipFile(ARCHIVE) as archive:
        cyclic = _build_cyclic(archive, audit)
        aging = _build_aging(archive, audit)
    return cyclic, aging


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    return tuple(frame.copy() for frame in _build_cached())


def _write_frames(
    cyclic: pd.DataFrame, aging: pd.DataFrame, directory: Path
) -> dict[str, Path]:
    paths = {"cyclic": directory / CYCLIC.name, "aging": directory / AGING.name}
    cyclic.to_csv(
        paths["cyclic"], index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    aging.to_csv(
        paths["aging"], index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    return paths


def write_release(cyclic: pd.DataFrame, aging: pd.DataFrame) -> None:
    DIRECTED.mkdir(parents=True, exist_ok=True)
    paths = _write_frames(cyclic, aging, DIRECTED)
    source_audit = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    payload = {
        "release_id": RELEASE_ID,
        "counts": {
            "formulation_count": int(cyclic["formulation_id"].nunique()),
            "cyclic_run_count": int(cyclic["cyclic_run_id"].nunique()),
            "cycle_endpoint_count": len(cyclic),
            "cyclic_source_point_count": int(
                source_audit["curve_points_by_category"]["cyclic_tensile_raw"]
            ),
            "hydrolytic_aging_pair_count": len(aging),
            "hydrolytic_source_point_count": int(
                source_audit["curve_points_by_category"][
                    "hydrolytic_aging_tensile_raw"
                ]
            ),
            "published_compact_row_count": len(cyclic) + len(aging),
        },
        "source": {
            "dataset_doi": DATASET_DOI,
            "inferred_preprint_doi": PREPRINT_DOI,
            "license": LICENSE,
            "archive": _entry(ARCHIVE),
            "metadata": _entry(SOURCE_METADATA),
            "source_audit": _entry(SOURCE_AUDIT),
            "curve_audit": _entry(CURVE_AUDIT),
        },
        "policy": {
            "raw_curves_republished": False,
            "cyclic_filename_C0_50_means_strain_range": True,
            "cycle_count_verified_from_raw_curves": 100,
            "direct_shape_recovery_available": False,
            "hydrolytic_protocol_resolved": False,
            "preprint_relation": "inferred_not_repository_linked",
        },
        "outputs": {key: _entry(path) for key, path in paths.items()},
    }
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_release(cyclic: pd.DataFrame, aging: pd.DataFrame) -> None:
    with tempfile.TemporaryDirectory(prefix="iir-oh-durability-check-") as directory:
        temporary = _write_frames(cyclic, aging, Path(directory))
        published = {"cyclic": CYCLIC, "aging": AGING}
        mismatches = [
            key
            for key in published
            if _sha256(temporary[key]) != _sha256(published[key])
        ]
        if mismatches:
            raise SystemExit(f"IIR-OH循环耐久输出不一致: {','.join(mismatches)}")
    print("IIR-OH聚氨酯循环耐久数据检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    cyclic, aging = build_release()
    if args.检查:
        check_release(cyclic, aging)
    else:
        write_release(cyclic, aging)
        print(
            json.dumps(
                {
                    "cyclic_runs": int(cyclic["cyclic_run_id"].nunique()),
                    "cycle_endpoints": len(cyclic),
                    "hydrolytic_pairs": len(aging),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
