"""物化QUB开放的生物基自修复TPU拉伸、循环与TGA数据。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
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
    / "QUB_生物基三重自修复TPU"
)
RAW_DIR = SOURCE_DIR / "解压数据_只读" / "MA d4ma00289j dataset"
AUDIT = SOURCE_DIR / "内容审计摘要.json"
METADATA = SOURCE_DIR / "官方DataCite元数据.json"
DIRECTED = ROOT / "结果" / "定向筛选"
TENSILE = DIRECTED / "QUB生物基自修复TPU拉伸端点.csv"
CYCLIC = DIRECTED / "QUB生物基自修复TPU循环端点.csv"
THERMAL = DIRECTED / "QUB生物基自修复TPUTGA端点.csv"
CURVES = DIRECTED / "QUB生物基自修复TPU曲线.csv.gz"
MANIFEST = DIRECTED / "QUB生物基自修复TPU发布清单.json"
RELEASE_ID = "tpu-qub-biobased-self-healing-2024-v1"
DATASET_DOI = "10.17034/83fdb865-0ead-4c8b-81d2-59265a8810f3"
ARTICLE_DOI = "10.1039/D4MA00289J"
LICENSE = "CC-BY-4.0"
CITATIONS = "reference-182;reference-183"


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


def _read_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.reader(handle))


def _number(value: object) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normalise_header(value: str) -> str:
    return "".join(value.lower().split()).replace("−", "-")


def _pair_columns(
    rows: list[list[str]], x_token: str, y_token: str
) -> list[tuple[int, int]]:
    header = rows[1]
    x_key = _normalise_header(x_token)
    y_key = _normalise_header(y_token)
    pairs = []
    for index, value in enumerate(header[:-1]):
        if x_key in _normalise_header(value) and y_key in _normalise_header(
            header[index + 1]
        ):
            pairs.append((index, index + 1))
    return pairs


def _extract_pair(
    rows: list[list[str]], x_column: int, y_column: int
) -> list[tuple[float, float]]:
    points = []
    for row in rows[2:]:
        if max(x_column, y_column) >= len(row):
            continue
        x_value = _number(row[x_column])
        y_value = _number(row[y_column])
        if x_value is not None and y_value is not None:
            points.append((x_value, y_value))
    return points


def _positive_path_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        dx = x1 - x0
        if dx > 0:
            area += dx * (max(y0, 0.0) + max(y1, 0.0)) / 2
    return area / 100.0


def _composition(formulation: str) -> dict[str, object]:
    hard_segment = 40.0 if formulation == "P40-HDO" else float(formulation[1:])
    return {
        "polymer_family": "bio_based_thermoplastic_polyurethane",
        "polyol_name": "Pripol 2033",
        "polyol_role": "bio_based_dimer_fatty_acid_diol",
        "diisocyanate_name": "IPDI",
        "diisocyanate_smiles": "CC1(C)CC(CC(C)(CN=C=O)C1)N=C=O",
        "chain_extender_name": "HDO" if formulation == "P40-HDO" else "HEDS",
        "chain_extender_role": (
            "1,6-hexanediol_control"
            if formulation == "P40-HDO"
            else "bis(2-hydroxyethyl)disulfide_dynamic_chain_extender"
        ),
        "hard_segment_wt_percent": hard_segment,
        "NCO_OH_molar_ratio": 1.0,
        "chemistry_mapping_status": "monomer_set_hard_segment_mapped",
        "mapping_evidence": (
            "RSC article synthesis section: Pripol 2033/IPDI/HEDS, HDO control; "
            "P35/P40/P45 denote 35/40/45 wt% IPDI; NCO:OH=1:1"
        ),
    }


def _common(formulation: str, source_file: Path) -> dict[str, object]:
    return {
        "release_id": RELEASE_ID,
        "source_id": f"doi:{DATASET_DOI}",
        "formulation_id": formulation,
        **_composition(formulation),
        "split_group": f"{DATASET_DOI}|{formulation}",
        "model_admission_layer": "核心实验层",
        "usage_mode": "direct_target_supervision",
        "source_locator": source_file.relative_to(ROOT).as_posix(),
        "license": LICENSE,
        "citation_keys": CITATIONS,
    }


def _curve_rows(
    *,
    curve_id: str,
    formulation: str,
    modality: str,
    condition: str,
    points: list[tuple[float, float]],
    x_unit: str,
    y_unit: str,
    source_file: Path,
) -> list[dict[str, object]]:
    return [
        {
            "release_id": RELEASE_ID,
            "curve_id": curve_id,
            "formulation_id": formulation,
            "modality": modality,
            "condition": condition,
            "point_index": index,
            "x_value": x_value,
            "y_value": y_value,
            "x_unit": x_unit,
            "y_unit": y_unit,
            "split_group": f"{DATASET_DOI}|{formulation}",
            "source_locator": source_file.relative_to(ROOT).as_posix(),
            "license": LICENSE,
            "citation_keys": CITATIONS,
        }
        for index, (x_value, y_value) in enumerate(points, start=1)
    ]


def _build_tensile(
    audit: dict[str, object], curve_output: list[dict[str, object]]
) -> pd.DataFrame:
    records = []
    filenames = [
        "MA d4ma00289j dataset_Figure 4A.csv",
        "MA d4ma00289j dataset_Figure 6A.csv",
        "MA d4ma00289j dataset_Figure 6B.csv",
        "MA d4ma00289j dataset_Figure 6C.csv",
        "MA d4ma00289j dataset_Figure 7.csv",
    ]
    for filename in filenames:
        source_file = RAW_DIR / filename
        rows = _read_rows(source_file)
        pairs = _pair_columns(rows, "Strain", "Stress")
        audit_curves = [
            record
            for record in audit["curve_records"]
            if record["file_name"] == filename and record["modality"] == "bulk_tensile"
        ]
        if len(pairs) != len(audit_curves):
            raise ValueError(
                f"{filename}列对数{len(pairs)}与审计曲线数{len(audit_curves)}不一致"
            )
        for (x_column, y_column), audited in zip(pairs, audit_curves, strict=True):
            if audited["effective_grade"] == "重复排除" or audited.get(
                "cross_file_duplicate_of"
            ):
                continue
            points = _extract_pair(rows, x_column, y_column)
            positive = [(x, y) for x, y in points if x >= 0]
            if len(positive) < 4:
                raise ValueError(f"{audited['label']}有效拉伸点不足")
            formulation = audited["formulation"]
            curve_id = f"qub_{audited['label']}"
            records.append(
                {
                    **_common(formulation, source_file),
                    "curve_id": curve_id,
                    "specimen_id": audited["specimen_id"],
                    "condition": audited["condition"],
                    "is_healed": str(audited["condition"]).startswith("heal_"),
                    "tensile_strength_MPa": max(y for _, y in positive),
                    "elongation_at_break_percent": max(x for x, _ in positive),
                    "toughness_MJ_m3": _positive_path_area(positive),
                    "curve_point_count": len(points),
                    "curve_content_sha256": audited["curve_content_sha256"].lower(),
                }
            )
            curve_output.extend(
                _curve_rows(
                    curve_id=curve_id,
                    formulation=formulation,
                    modality="bulk_tensile",
                    condition=audited["condition"],
                    points=points,
                    x_unit="percent",
                    y_unit="MPa",
                    source_file=source_file,
                )
            )
    return pd.DataFrame(records).sort_values(
        ["formulation_id", "condition", "specimen_id"]
    ).reset_index(drop=True)


def _path_area(points: list[tuple[float, float]], direction: str) -> float:
    area = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        dx = x1 - x0
        if (direction == "loading" and dx > 0) or (
            direction == "unloading" and dx < 0
        ):
            area += abs(dx) * (max(y0, 0.0) + max(y1, 0.0)) / 2
    return area / 100.0


def _build_cyclic(
    audit: dict[str, object], curve_output: list[dict[str, object]]
) -> pd.DataFrame:
    filename = "MA d4ma00289j dataset_Figure 4B.csv"
    source_file = RAW_DIR / filename
    rows = _read_rows(source_file)
    pairs = _pair_columns(rows, "Strain", "Stress")
    audit_curves = [
        record
        for record in audit["curve_records"]
        if record["file_name"] == filename and record["modality"] == "cyclic_tensile"
    ]
    if len(pairs) != len(audit_curves):
        raise ValueError("QUB循环列对与审计曲线数不一致")
    records = []
    for cycle, ((x_column, y_column), audited) in enumerate(
        zip(pairs, audit_curves, strict=True), start=1
    ):
        points = _extract_pair(rows, x_column, y_column)
        maximum_strain = max(x for x, _ in points)
        peak_index = max(range(len(points)), key=lambda index: points[index][0])
        loading = points[: peak_index + 1]
        unloading = points[peak_index:]
        loading_energy = _path_area(loading, "loading")
        unloading_energy = _path_area(unloading, "unloading")
        curve_id = f"qub_P40_cycle_{cycle}"
        records.append(
            {
                **_common("P40", source_file),
                "model_admission_layer": "辅助实验层",
                "usage_mode": "cyclic_hysteresis_proxy",
                "cycle_sequence_id": "qub_P40_100pct_six_cycles",
                "curve_id": curve_id,
                "cycle_number": cycle,
                "dependent_observation": True,
                "maximum_strain_percent": maximum_strain,
                "peak_stress_MPa": max(y for _, y in points),
                "commanded_terminal_strain_percent": points[-1][0],
                "residual_strain_percent": math.nan,
                "strain_recovery_percent": math.nan,
                "recovery_metric_status": "not_identifiable_from_imposed_strain_cycle",
                "cyclic_target_role": "hysteresis_and_energy_dissipation_proxy",
                "loading_energy_MJ_m3": loading_energy,
                "unloading_energy_MJ_m3": unloading_energy,
                "hysteresis_energy_MJ_m3": max(loading_energy - unloading_energy, 0.0),
                "dissipation_fraction": (
                    max(loading_energy - unloading_energy, 0.0) / loading_energy
                    if loading_energy > 0
                    else math.nan
                ),
                "rest_time_min": 1.0,
                "crosshead_speed_mm_min": 20.0,
                "curve_point_count": len(points),
                "curve_content_sha256": audited["curve_content_sha256"].lower(),
            }
        )
        curve_output.extend(
            _curve_rows(
                curve_id=curve_id,
                formulation="P40",
                modality="cyclic_tensile",
                condition=f"cycle_{cycle}_100pct_one_minute_rest",
                points=points,
                x_unit="percent",
                y_unit="MPa",
                source_file=source_file,
            )
        )
    return pd.DataFrame(records)


def _crossing_temperature(
    points: list[tuple[float, float]], threshold: float
) -> float:
    for (t0, w0), (t1, w1) in zip(points, points[1:], strict=False):
        if w0 > threshold >= w1 and w0 != w1:
            return t0 + (threshold - w0) * (t1 - t0) / (w1 - w0)
    raise ValueError(f"TGA曲线未跨越{threshold}%")


def _build_thermal(
    audit: dict[str, object], curve_output: list[dict[str, object]]
) -> pd.DataFrame:
    tga_file = RAW_DIR / "MA d4ma00289j dataset_Figure 3B.csv"
    dtg_file = RAW_DIR / "MA d4ma00289j dataset_Figure 3C.csv"
    tga_rows = _read_rows(tga_file)
    dtg_rows = _read_rows(dtg_file)
    tga_pairs = _pair_columns(tga_rows, "Temperature", "Weight")
    dtg_pairs = _pair_columns(dtg_rows, "Temperature", "DerivativeWeight")
    audit_curves = [
        record
        for record in audit["curve_records"]
        if record["file_name"] == tga_file.name and record["modality"] == "TGA"
    ]
    if len(tga_pairs) != 3 or len(dtg_pairs) != 3 or len(audit_curves) != 3:
        raise ValueError("QUB热分析列数与三种配方不一致")
    records = []
    for (tx, wy), (dx, dy), audited in zip(
        tga_pairs, dtg_pairs, audit_curves, strict=True
    ):
        raw_tga = _extract_pair(tga_rows, tx, wy)
        baseline_values = [weight for _, weight in raw_tga[:30]]
        baseline = statistics.median(baseline_values)
        normalised = [(temperature, 100.0 * weight / baseline) for temperature, weight in raw_tga]
        crossing_points = [(t, w) for t, w in normalised if t >= 100]
        raw_dtg = _extract_pair(dtg_rows, dx, dy)
        degradation_dtg = [(t, value) for t, value in raw_dtg if t >= 150]
        td_peak = min(degradation_dtg, key=lambda point: point[1])[0]
        formulation = audited["formulation"]
        curve_id = f"qub_{formulation}_TGA"
        records.append(
            {
                **_common(formulation, tga_file),
                "curve_id": curve_id,
                "T5_C": _crossing_temperature(crossing_points, 95.0),
                "T10_C": _crossing_temperature(crossing_points, 90.0),
                "T50_C": _crossing_temperature(crossing_points, 50.0),
                "Td_peak_C": td_peak,
                "atmosphere": "nitrogen",
                "heating_rate_C_min": 10.0,
                "temperature_range_C": "25-600",
                "normalisation_baseline_weight_percent": baseline,
                "curve_point_count": len(raw_tga),
                "curve_content_sha256": audited["curve_content_sha256"].lower(),
            }
        )
        curve_output.extend(
            _curve_rows(
                curve_id=curve_id,
                formulation=formulation,
                modality="TGA",
                condition="nitrogen_10C_min",
                points=normalised,
                x_unit="degree_C",
                y_unit="normalised_weight_percent",
                source_file=tga_file,
            )
        )
    return pd.DataFrame(records).sort_values("formulation_id").reset_index(drop=True)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    curve_output: list[dict[str, object]] = []
    tensile = _build_tensile(audit, curve_output)
    cyclic = _build_cyclic(audit, curve_output)
    thermal = _build_thermal(audit, curve_output)
    curves = pd.DataFrame(curve_output)
    return tensile, cyclic, thermal, curves


def _write_frames(
    tensile: pd.DataFrame,
    cyclic: pd.DataFrame,
    thermal: pd.DataFrame,
    curves: pd.DataFrame,
    directory: Path,
) -> dict[str, Path]:
    paths = {
        "tensile": directory / TENSILE.name,
        "cyclic": directory / CYCLIC.name,
        "thermal": directory / THERMAL.name,
        "curves": directory / CURVES.name,
    }
    tensile.to_csv(paths["tensile"], index=False, encoding="utf-8-sig", lineterminator="\n")
    cyclic.to_csv(paths["cyclic"], index=False, encoding="utf-8-sig", lineterminator="\n")
    thermal.to_csv(paths["thermal"], index=False, encoding="utf-8-sig", lineterminator="\n")
    curves.to_csv(
        paths["curves"],
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        compression={"method": "gzip", "mtime": 0},
    )
    return paths


def write_release(
    tensile: pd.DataFrame,
    cyclic: pd.DataFrame,
    thermal: pd.DataFrame,
    curves: pd.DataFrame,
) -> None:
    DIRECTED.mkdir(parents=True, exist_ok=True)
    paths = _write_frames(tensile, cyclic, thermal, curves, DIRECTED)
    source_files = [
        AUDIT,
        METADATA,
        RAW_DIR / "MA d4ma00289j dataset_Figure 3B.csv",
        RAW_DIR / "MA d4ma00289j dataset_Figure 3C.csv",
        RAW_DIR / "MA d4ma00289j dataset_Figure 4A.csv",
        RAW_DIR / "MA d4ma00289j dataset_Figure 4B.csv",
        RAW_DIR / "MA d4ma00289j dataset_Figure 6A.csv",
        RAW_DIR / "MA d4ma00289j dataset_Figure 6B.csv",
        RAW_DIR / "MA d4ma00289j dataset_Figure 6C.csv",
        RAW_DIR / "MA d4ma00289j dataset_Figure 7.csv",
    ]
    payload = {
        "release_id": RELEASE_ID,
        "counts": {
            "formulation_count": int(tensile["formulation_id"].nunique()),
            "tensile_curve_count": int(tensile["curve_id"].nunique()),
            "cyclic_sequence_count": int(cyclic["cycle_sequence_id"].nunique()),
            "dependent_cycle_count": len(cyclic),
            "tga_curve_count": len(thermal),
            "curve_point_rows": len(curves),
        },
        "counting_note": (
            "41条拉伸曲线是独立试样；6个循环来自同一P40试样，只计1个循环序列；"
            "曲线点不是独立材料或独立实验。"
        ),
        "source": {
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "license": LICENSE,
            "inputs": [_entry(path) for path in source_files],
        },
        "outputs": {key: _entry(path) for key, path in paths.items()},
    }
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_release(
    tensile: pd.DataFrame,
    cyclic: pd.DataFrame,
    thermal: pd.DataFrame,
    curves: pd.DataFrame,
) -> None:
    with tempfile.TemporaryDirectory(prefix="qub-tpu-check-") as directory:
        temporary = _write_frames(tensile, cyclic, thermal, curves, Path(directory))
        published = {
            "tensile": TENSILE,
            "cyclic": CYCLIC,
            "thermal": THERMAL,
            "curves": CURVES,
        }
        mismatches = [
            key
            for key in published
            if _sha256(temporary[key]) != _sha256(published[key])
        ]
        if mismatches:
            raise SystemExit(f"QUB发布输出不一致: {','.join(mismatches)}")
    print("QUB生物基自修复TPU数据检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    tensile, cyclic, thermal, curves = build_release()
    if args.检查:
        check_release(tensile, cyclic, thermal, curves)
    else:
        write_release(tensile, cyclic, thermal, curves)
        print(
            json.dumps(
                {
                    "tensile_curves": len(tensile),
                    "dependent_cycles": len(cyclic),
                    "tga_curves": len(thermal),
                    "curve_points": len(curves),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
