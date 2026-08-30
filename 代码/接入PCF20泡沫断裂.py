"""物化Sawbones PCF20硬质PU泡沫的拉伸与SENB断裂端点。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
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
    / "MaterialsCloud_商用PU泡沫多轴断裂力学"
)
DATA_ROOT = SOURCE_DIR / "解压内容" / "PUF"
ARCHIVE = SOURCE_DIR / "PUF.zip"
README = SOURCE_DIR / "README.txt"
SOURCE_METADATA = SOURCE_DIR / "官方MaterialsCloud元数据.json"
DATACITE_METADATA = SOURCE_DIR / "官方DataCite元数据.json"
SOURCE_AUDIT = SOURCE_DIR / "内容审计摘要.json"
DIMENSION_IMAGE = DATA_ROOT / "Toughness" / "Nominal Dimensions.png"
DIRECTED = ROOT / "结果" / "定向筛选"
OUTPUT = DIRECTED / "PCF20泡沫拉伸断裂端点.csv"
MANIFEST = DIRECTED / "PCF20泡沫断裂发布清单.json"
RELEASE_ID = "sawbones-pcf20-foam-fracture-2026-v1"
DATASET_DOI = "10.24435/materialscloud:vf-ry"
PREPRINT_DOI = "10.2139/ssrn.6755055"
LICENSE = "CC-BY-4.0"
CITATIONS = "reference-193;reference-194"


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


def _number(value: str) -> float:
    return float(value.strip().strip('"').replace(",", "."))


def _parse_machine(path: Path) -> tuple[dict[str, float], list[tuple[float, float, float]]]:
    lines = path.read_text(encoding="cp1252").splitlines()
    metadata: dict[str, float] = {}
    start = None
    for index, line in enumerate(lines):
        if "Epaisseur" in line:
            metadata["thickness_mm"] = _number(line.split(";")[1])
        elif "Largeur" in line:
            metadata["width_mm"] = _number(line.split(";")[1])
        elif "Longueur" in line and "initiale" not in line:
            metadata["length_mm"] = _number(line.split(";")[1])
        if line.startswith("Temps;"):
            start = index + 2
            header = line.split(";")
            break
    if start is None:
        raise ValueError(f"未找到机器数据表头: {path}")
    rows = []
    for line in lines[start:]:
        fields = line.split(";")
        if len(fields) < 3:
            continue
        try:
            values = tuple(_number(value) for value in fields[:3])
        except ValueError:
            continue
        if header[1].startswith("Charge"):
            time, load, displacement = values
        else:
            time, displacement, load = values
        rows.append((time, displacement, load))
    return metadata, rows


def _parse_correlation(path: Path) -> tuple[list[str], list[tuple[float, ...]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = next(reader)
        rows = []
        for fields in reader:
            try:
                values = tuple(float(value) for value in fields)
            except (TypeError, ValueError):
                continue
            rows.append(values)
    return header, rows


def _positive_area(points: list[tuple[float, float]]) -> float:
    return sum(
        (x1 - x0) * (max(y0, 0.0) + max(y1, 0.0)) / 2
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False)
        if x1 > x0
    )


def _common(weight_ceiling: float) -> dict[str, object]:
    return {
        "release_id": RELEASE_ID,
        "source_id": f"doi:{DATASET_DOI}",
        "material_grade": "Sawbones PCF20",
        "material_description": "Fourth-generation rigid cellular polyurethane foam",
        "nominal_density_lb_ft3": 20.0,
        "nominal_density_kg_m3": 320.369,
        "polymer_family": "commercial_rigid_cellular_polyurethane_foam",
        "chemistry_mapping_status": "commercial_grade_density_only",
        "thermoplastic_tpu_core": False,
        "model_admission_layer": "polyurethane_foam_transfer",
        "usage_mode": "fracture_and_tensile_transfer_supervision",
        "future_weight_ceiling": weight_ceiling,
        "split_group": f"{DATASET_DOI}|Sawbones PCF20",
        "license": LICENSE,
        "citation_keys": CITATIONS,
    }


def _build_tension() -> list[dict[str, object]]:
    records = []
    for direction_dir in sorted((DATA_ROOT / "Tension").glob("Direction *")):
        for sample_dir in sorted(direction_dir.glob("Sample *")):
            sample = int(sample_dir.name.split()[-1])
            machine_path = sample_dir / f"Specimen_RawData_{sample}.csv"
            correlation_path = sample_dir / f"Correlation_{sample}.csv"
            dimensions, machine = _parse_machine(machine_path)
            correlation_header, correlation = _parse_correlation(correlation_path)
            machine_time = np.array([row[0] for row in machine])
            load = np.array([row[2] for row in machine])
            load -= statistics.median(load[:5])
            correlation_time = np.array([row[0] for row in correlation])
            strain_columns = [
                np.array([row[index] for row in correlation])
                for index in range(1, len(correlation_header))
            ]
            axial_index = max(
                range(len(strain_columns)),
                key=lambda index: float(
                    strain_columns[index].max() - strain_columns[index].min()
                ),
            )
            strain_percent = strain_columns[axial_index]
            overlap = (correlation_time >= machine_time.min()) & (
                correlation_time <= machine_time.max()
            )
            strain_fraction = strain_percent[overlap] / 100.0
            interpolated_load = np.interp(
                correlation_time[overlap], machine_time, load
            )
            stress = interpolated_load / (
                dimensions["thickness_mm"] * dimensions["width_mm"]
            )
            peak_strain_index = int(np.argmax(strain_fraction))
            curve = list(
                zip(
                    strain_fraction[: peak_strain_index + 1],
                    stress[: peak_strain_index + 1],
                    strict=True,
                )
            )
            records.append(
                {
                    **_common(0.30),
                    "specimen_key": (
                        f"PCF20_Tension_{direction_dir.name.replace(' ', '')}_S{sample}"
                    ),
                    "test_type": "tension",
                    "standard": "ASTM D638",
                    "direction": direction_dir.name,
                    "specimen_id": sample,
                    "thickness_mm": dimensions["thickness_mm"],
                    "width_mm": dimensions["width_mm"],
                    "length_mm": dimensions["length_mm"],
                    "DIC_axial_component": correlation_header[axial_index + 1],
                    "maximum_tensile_stress_MPa": float(stress.max()),
                    "maximum_DIC_strain_percent": float(strain_percent.max()),
                    "stress_strain_area_MJ_m3": _positive_area(curve),
                    "machine_point_count": len(machine),
                    "DIC_point_count": len(correlation),
                    "time_alignment_status": "direct_common_zero_no_calibration_file",
                    "toughness_evidence_level": "direct_tensile_curve_area_foam_transfer",
                    "machine_source": machine_path.relative_to(ROOT).as_posix(),
                    "DIC_source": correlation_path.relative_to(ROOT).as_posix(),
                    "machine_sha256": _sha256(machine_path),
                    "DIC_sha256": _sha256(correlation_path),
                }
            )
    return records


def _senb_shape_function(x: float) -> float:
    return (
        3
        * math.sqrt(x)
        * (1.99 - x * (1 - x) * (2.15 - 3.93 * x + 2.7 * x**2))
        / (2 * (1 + 2 * x) * (1 - x) ** 1.5)
    )


def _build_fracture() -> list[dict[str, object]]:
    records = []
    thickness_mm = 6.5
    width_mm = 14.0
    crack_length_mm = 6.5
    span_mm = 46.0
    geometry_factor = _senb_shape_function(crack_length_mm / width_mm)
    for sample_dir in sorted((DATA_ROOT / "Toughness").glob("Sample *")):
        sample = int(sample_dir.name.split()[-1])
        machine_path = sample_dir / f"Specimen_RawData_{sample}.csv"
        correlation_path = sample_dir / f"Correlation_{sample}.csv"
        _, machine = _parse_machine(machine_path)
        _, correlation = _parse_correlation(correlation_path)
        time = np.array([row[0] for row in machine])
        displacement = np.array([row[1] for row in machine])
        load = np.array([row[2] for row in machine])
        load -= statistics.median(load[:5])
        peak_index = int(np.argmax(load))
        work = _positive_area(
            list(
                zip(
                    displacement[: peak_index + 1] / 1000.0,
                    load[: peak_index + 1],
                    strict=True,
                )
            )
        )
        peak_load = float(load[peak_index])
        nominal_k = (
            peak_load
            * span_mm
            / (thickness_mm * width_mm**1.5)
            * geometry_factor
            / math.sqrt(1000.0)
        )
        cmod = float(
            np.interp(
                time[peak_index],
                np.array([row[0] for row in correlation]),
                np.array([row[1] for row in correlation]),
            )
        )
        records.append(
            {
                **_common(0.45),
                "specimen_key": f"PCF20_SENB_S{sample}",
                "test_type": "SENB_fracture",
                "standard": "ASTM E399",
                "direction": "not_reported_isotropic_foam",
                "specimen_id": sample,
                "nominal_thickness_B_mm": thickness_mm,
                "nominal_width_W_mm": width_mm,
                "nominal_crack_length_a_mm": crack_length_mm,
                "nominal_span_S_mm": span_mm,
                "peak_load_N": peak_load,
                "displacement_at_peak_load_mm": float(displacement[peak_index]),
                "load_displacement_work_to_peak_J": work,
                "CMOD_at_peak_load_mm": cmod,
                "nominal_peak_load_K_MPa_sqrt_m": nominal_k,
                "published_mean_K_MPa_sqrt_m": 0.24,
                "K_validity_status": (
                    "nominal_peak_load_geometry_not_full_ASTME399_validity"
                ),
                "toughness_evidence_level": "direct_SENB_nominal_K_foam_transfer",
                "machine_point_count": len(machine),
                "DIC_point_count": len(correlation),
                "machine_source": machine_path.relative_to(ROOT).as_posix(),
                "DIC_source": correlation_path.relative_to(ROOT).as_posix(),
                "machine_sha256": _sha256(machine_path),
                "DIC_sha256": _sha256(correlation_path),
            }
        )
    return records


def build_release() -> pd.DataFrame:
    return pd.DataFrame(_build_tension() + _build_fracture()).sort_values(
        ["test_type", "direction", "specimen_id"]
    ).reset_index(drop=True)


def write_release(frame: pd.DataFrame) -> None:
    DIRECTED.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUTPUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    payload = {
        "release_id": RELEASE_ID,
        "counts": {
            "material_grade_count": 1,
            "independent_physical_specimen_count": len(frame),
            "tension_specimen_count": int(frame["test_type"].eq("tension").sum()),
            "fracture_specimen_count": int(
                frame["test_type"].eq("SENB_fracture").sum()
            ),
            "machine_source_point_count": int(frame["machine_point_count"].sum()),
            "DIC_source_point_count": int(frame["DIC_point_count"].sum()),
            "published_compact_row_count": len(frame),
        },
        "source": {
            "dataset_doi": DATASET_DOI,
            "preprint_doi": PREPRINT_DOI,
            "license": LICENSE,
            "archive": _entry(ARCHIVE),
            "readme": _entry(README),
            "materialscloud_metadata": _entry(SOURCE_METADATA),
            "datacite_metadata": _entry(DATACITE_METADATA),
            "source_audit": _entry(SOURCE_AUDIT),
            "nominal_dimension_image": _entry(DIMENSION_IMAGE),
        },
        "policy": {
            "raw_curves_and_images_republished": False,
            "compression_and_shear_deferred": True,
            "deferred_reason": (
                "not_primary_toughness_target; preserve for future FEA auxiliary package"
            ),
            "DIC_missing_values_interpolated": False,
            "nominal_K_not_claimed_as_validated_KIc": True,
            "material_is_rigid_foam_not_TPU": True,
        },
        "output": _entry(OUTPUT),
    }
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_release(frame: pd.DataFrame) -> None:
    with tempfile.TemporaryDirectory(prefix="pcf20-fracture-check-") as directory:
        temporary = Path(directory) / OUTPUT.name
        frame.to_csv(
            temporary, index=False, encoding="utf-8-sig", lineterminator="\n"
        )
        if _sha256(temporary) != _sha256(OUTPUT):
            raise SystemExit("PCF20泡沫断裂输出不一致")
    print("PCF20泡沫断裂数据检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(
            json.dumps(
                {
                    "specimens": len(frame),
                    "tension": int(frame["test_type"].eq("tension").sum()),
                    "fracture": int(
                        frame["test_type"].eq("SENB_fracture").sum()
                    ),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
