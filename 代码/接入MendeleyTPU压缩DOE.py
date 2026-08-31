"""接入Mendeley NinjaFlex/PolyFlex压缩DOE的方向化离散响应。"""

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
    / "Mendeley_TPU压缩打印DOE"
)
ARCHIVE = SOURCE_DIR / "7zcd9bmmg5-1.zip"
OUT = ROOT / "结果" / "定向筛选" / "TPU压缩打印DOE端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "TPU压缩打印DOE发布清单.json"

DATASET_DOI = "10.17632/7zcd9bmmg5.1"
EXPECTED_ARCHIVE_SHA256 = (
    "0b26707846f5cd23d2f843eb30d90ad24e548fce277a2cbffa5555348d226397"
)
STRAINS = np.array([0.05, 0.10, 0.15, 0.20])
LBF_TO_N = 4.4482216152605
IN2_TO_M2 = 0.00064516
PSI_TO_KPA = 6.894757293168


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _discrete_endpoints(
    stress_kpa: list[float], missing_count: int
) -> dict[str, object]:
    stress = np.asarray(stress_kpa, dtype=float)
    complete = missing_count == 0 and np.isfinite(stress).all()
    if complete:
        peak_index = int(np.argmax(stress))
        energy = float(np.trapezoid(np.r_[0, stress], np.r_[0, STRAINS]))
        peak = float(stress[peak_index])
        peak_strain = float(STRAINS[peak_index])
    else:
        peak = float("nan")
        peak_strain = float("nan")
        energy = float("nan")
    return {
        "stress_at_5pct_strain_kPa": stress[0],
        "stress_at_10pct_strain_kPa": stress[1],
        "stress_at_15pct_strain_kPa": stress[2],
        "stress_at_20pct_strain_kPa": stress[3],
        "peak_discrete_stress_kPa": peak,
        "strain_at_peak_discrete_stress": peak_strain,
        "discrete_compression_energy_to_20pct_kJ_m3": energy,
        "direct_stress_observation_count": int(np.isfinite(stress).sum()),
        "missing_stress_observation_count": missing_count,
        "complete_four_point_response": complete,
    }


def _family_id(material: str, geometry: str, order: object, pattern: object, infill: object, shells: object) -> str:
    return "|".join(
        str(value)
        for value in (material, geometry, order, pattern, infill, shells)
    )


def _base_row(
    *,
    material_key: str,
    material_grade: str,
    geometry: str,
    shape_order: object,
    pattern: object,
    infill_raw: object,
    infill_resolved: float | None,
    shells: object,
    sample: object,
    direction: str,
    stress_values: list[float],
    missing_count: int,
    source_file: str,
    source_member_sha256: str,
    source_locator: str,
    stress_formula: str,
    quality_status: str,
    notes: str,
) -> dict[str, object]:
    family = _family_id(
        material_key, geometry, shape_order, pattern, infill_raw, shells
    )
    return {
        "source_id": "source_mendeley_7zcd9bmmg5_v1",
        "material_key": material_key,
        "formulation_id": material_key,
        "material_grade": material_grade,
        "geometry": geometry,
        "shape_order": shape_order,
        "infill_pattern": pattern,
        "infill_value_raw": infill_raw,
        "infill_percent_resolved": infill_resolved,
        "infill_mapping_status": (
            "resolved_percent"
            if infill_resolved is not None
            else "raw_label_ambiguous_not_numeric_percent"
        ),
        "shell_count": shells,
        "physical_specimen_id": f"{material_key}|{geometry}|{shape_order}|{pattern}|{infill_raw}|{shells}|{sample}",
        "specimen_sample_label": sample,
        "loading_direction": direction,
        "strain_levels": "0.05;0.10;0.15;0.20",
        **_discrete_endpoints(stress_values, missing_count),
        "stress_unit": "kPa",
        "energy_unit": "kJ_m3",
        "stress_formula": stress_formula,
        "quality_status": quality_status,
        "reported_replicate_count": 4,
        "source_protocol": (
            "ASTM_D575_91_adapted;21C;12.65mm_min;20pct_peak_deflection;"
            "10Hz_acquisition"
        ),
        "target_role": "discrete_compression_energy_absorption_application_proxy",
        "complete_toughness_available": False,
        "model_admission_layer": "core_TPU_application_experimental",
        "sample_weight_ceiling": 0.15 if quality_status == "complete" else 0.0,
        "split_group": f"{DATASET_DOI}|{family}",
        "source_file": source_file,
        "source_locator": source_locator,
        "source_member_sha256": source_member_sha256,
        "license": "CC-BY-4.0",
        "citation_keys": "reference-103",
        "notes": notes,
    }


def _ninjaflex_rows(
    payload: bytes, member: str, member_sha256: str
) -> list[dict[str, object]]:
    raw = pd.read_excel(io.BytesIO(payload), sheet_name="Raw Data", header=0)
    load_columns = {
        direction: [
            f"Load @ {int(strain * 100)}% Strain Dir {direction} [lbf]"
            for strain in STRAINS
        ]
        for direction in (1, 2)
    }
    rows = []
    for excel_row, record in raw.iterrows():
        if pd.isna(record.get("Order")):
            continue
        pattern = record["Infill Pattern"]
        infill = record["Infill %"]
        shells = record["Shells"]
        sample = record["Sample"]
        area = float(record["Cross Section Area [in^2]"])
        family = _family_id(
            "NinjaFlex_unknown_grade", "cube", record["Order"], pattern, infill, shells
        )
        for direction in (1, 2):
            loads = [float(record[column]) for column in load_columns[direction]]
            stress = [load * LBF_TO_N / (area * IN2_TO_M2) / 1000 for load in loads]
            cached_columns = [
                f"Stress @ {int(strain * 100)}% Strain Dir {direction} [kPa]"
                for strain in STRAINS
            ]
            cached = pd.to_numeric(record[cached_columns], errors="coerce")
            recompute_error = float(
                np.nanmax(np.abs(np.asarray(stress) - cached.to_numpy(dtype=float)))
            )
            rows.append(
                _base_row(
                    material_key="NinjaFlex_unknown_grade",
                    material_grade="NinjaFlex (NinjaTek; exact chemistry/hardness unavailable)",
                    geometry="cube",
                    shape_order=record["Order"],
                    pattern=pattern,
                    infill_raw=infill,
                    infill_resolved=float(infill),
                    shells=int(shells),
                    sample=sample,
                    direction=f"dir_{direction}",
                    stress_values=stress,
                    missing_count=0,
                    source_file=member,
                    source_member_sha256=member_sha256,
                    source_locator=f"{member}#Raw Data!{excel_row + 2}",
                    stress_formula="load_lbf_to_kPa_using_cross_section_area_in2",
                    quality_status="complete",
                    notes=f"recomputed_from_load_and_area;max_cached_difference_kPa={recompute_error:.9g};family={family}",
                )
            )
    return rows


def _ninjaflex_cylinder_rows(
    payload: bytes, member: str, member_sha256: str
) -> list[dict[str, object]]:
    raw = pd.read_excel(
        io.BytesIO(payload), sheet_name="NinjaFlex Cylinders - Do Not S.", header=0
    )
    columns = [
        f"Stress @ {int(strain * 100)}% Strain Dir 1 (kpa)" for strain in STRAINS
    ]
    rows = []
    for data_index, record in raw.iterrows():
        sheet_row = data_index + 2
        if not (2 <= sheet_row <= 13 or 18 <= sheet_row <= 25):
            continue
        values = pd.to_numeric(record[columns], errors="coerce").to_numpy(dtype=float)
        missing = int(np.isnan(values).sum())
        infill_raw = record["Infill %"]
        infill_resolved = (
            float(infill_raw)
            if float(infill_raw) in {10.0, 20.0, 30.0}
            else None
        )
        rows.append(
            _base_row(
                material_key="NinjaFlex_unknown_grade",
                material_grade="NinjaFlex (NinjaTek; exact chemistry/hardness unavailable)",
                geometry="cylinder",
                shape_order=record["Order"],
                pattern=record["Infill Pattern"],
                infill_raw=infill_raw,
                infill_resolved=infill_resolved,
                shells=int(record["Shells"]),
                sample=record["Sample"],
                direction="dir_1",
                stress_values=values.tolist(),
                missing_count=missing,
                source_file=member,
                source_member_sha256=member_sha256,
                source_locator=f"{member}#NinjaFlex Cylinders - Do Not S.!{sheet_row}",
                stress_formula="source_reported_kPa",
                quality_status="complete" if missing == 0 else "partial_missing_points",
                notes="cylinder_response;0.2_infill_label_kept_ambiguous"
                if infill_resolved is None
                else "cylinder_response",
            )
        )
    return rows


def _ninjaflex_solid_rows(
    payload: bytes, member: str, member_sha256: str
) -> list[dict[str, object]]:
    raw = pd.read_excel(
        io.BytesIO(payload), sheet_name="NinjaFlex Cylinders - Do Not S.", header=0
    )
    columns = [
        f"Stress @ {int(strain * 100)}% Strain Dir 1 (kpa)" for strain in STRAINS
    ]
    rows = []
    for data_index in range(12, 16):
        record = raw.iloc[data_index]
        sheet_row = data_index + 2
        values = pd.to_numeric(record[columns], errors="coerce").to_numpy(dtype=float)
        missing = int(np.isnan(values).sum())
        rows.append(
            _base_row(
                material_key="NinjaFlex_unknown_grade",
                material_grade="NinjaFlex (NinjaTek; exact chemistry/hardness unavailable)",
                geometry="solid_cube_control",
                shape_order="9999_bottom_layers",
                pattern="9999 bottom layers",
                infill_raw=1,
                infill_resolved=None,
                shells=2,
                sample=record["Sample"],
                direction="dir_1",
                stress_values=values.tolist(),
                missing_count=missing,
                source_file=member,
                source_member_sha256=member_sha256,
                source_locator=f"{member}#NinjaFlex Cylinders - Do Not S.!{sheet_row}",
                stress_formula="source_reported_kPa",
                quality_status="complete" if missing == 0 else "partial_missing_points",
                notes="source_note_relocated_solid_cube_control;raw_infill_label_not_percent",
            )
        )
    return rows


def _polyflex_rows(
    payload: bytes, member: str, member_sha256: str
) -> list[dict[str, object]]:
    raw = pd.read_excel(
        io.BytesIO(payload), sheet_name="Raw data - Do not sort", header=1
    )
    directions = {
        "vertical": [
            f"{int(strain * 100)}% Vertical (psi)" for strain in STRAINS
        ],
        "horizontal": [
            f"{int(strain * 100)}% Horizontal (psi)" for strain in STRAINS
        ],
    }
    rows = []
    for excel_row, record in raw.iterrows():
        if pd.isna(record.get("Order")):
            continue
        for direction, columns in directions.items():
            psi = pd.to_numeric(record[columns], errors="coerce").to_numpy(dtype=float)
            missing = int(np.isnan(psi).sum())
            stress = (psi * PSI_TO_KPA).tolist()
            quality = "complete" if missing == 0 else "partial_missing_points"
            rows.append(
                _base_row(
                    material_key="PolyFlex_unknown_grade",
                    material_grade="PolyFlex (exact chemistry/hardness unavailable)",
                    geometry="cube",
                    shape_order=record["Order"],
                    pattern=record["Infill Pattern"],
                    infill_raw=record["Infill %"],
                    infill_resolved=float(record["Infill %"]),
                    shells=int(record["Shells"]),
                    sample=record["Sample"],
                    direction=direction,
                    stress_values=stress,
                    missing_count=missing,
                    source_file=member,
                    source_member_sha256=member_sha256,
                    source_locator=f"{member}#Raw data - Do not sort!{excel_row + 3}",
                    stress_formula="source_reported_psi_to_kPa",
                    quality_status=quality,
                    notes="polyflex_discrete_point_response;one_horizontal_direction_has_four_missing_source_values"
                    if missing
                    else "polyflex_discrete_point_response",
                )
            )
    return rows


def build_release() -> pd.DataFrame:
    if _sha256(ARCHIVE) != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("Mendeley TPU压缩DOE归档SHA-256与冻结值不一致")
    with zipfile.ZipFile(ARCHIVE) as archive:
        members = archive.namelist()
        ninja_member = next(
            member
            for member in members
            if member.endswith("NinjaTek Data.xlsx")
        )
        polyflex_member = next(
            member
            for member in members
            if member.endswith("PolyFlex Data.xlsx")
        )
        ninja_payload = archive.read(ninja_member)
        polyflex_payload = archive.read(polyflex_member)
    ninja_sha = _sha256_bytes(ninja_payload)
    polyflex_sha = _sha256_bytes(polyflex_payload)
    rows = _ninjaflex_rows(ninja_payload, ninja_member, ninja_sha)
    rows += _ninjaflex_cylinder_rows(ninja_payload, ninja_member, ninja_sha)
    rows += _ninjaflex_solid_rows(ninja_payload, ninja_member, ninja_sha)
    rows += _polyflex_rows(polyflex_payload, polyflex_member, polyflex_sha)
    return pd.DataFrame(rows).sort_values(
        ["material_key", "geometry", "shape_order", "physical_specimen_id", "loading_direction"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    return {
        "release_id": "mendeley_tpu_compression_doe_v1",
        "source": {
            "dataset_doi": DATASET_DOI,
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "published_directional_response_row_count": len(frame),
            "physical_specimen_count": int(
                frame["physical_specimen_id"].nunique()
            ),
            "configuration_family_count": int(frame["split_group"].nunique()),
            "direct_stress_observation_count": int(
                frame["direct_stress_observation_count"].sum()
            ),
            "complete_four_point_response_row_count": int(
                frame["complete_four_point_response"].sum()
            ),
            "partial_response_row_count": int(
                (~frame["complete_four_point_response"]).sum()
            ),
            "derived_discrete_energy_row_count": int(
                frame["discrete_compression_energy_to_20pct_kJ_m3"].notna().sum()
            ),
            "material_count": int(frame["material_key"].nunique()),
        },
        "policy": {
            "continuous_stress_strain_history_available": False,
            "discrete_energy_claimed_as_fracture_toughness": False,
            "directions_claimed_as_independent_physical_specimens": False,
            "solid_control_infill_9999_treated_as_1pct": False,
            "ambiguous_0p2_infill_silently_converted_to_20pct": False,
            "missing_polyflex_values_imputed": False,
            "proprietary_mpx_deserialized": False,
        },
        "outputs": {OUT.name: output_hash},
    }


def write_release(frame: pd.DataFrame) -> None:
    frame.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(_manifest(frame, _sha256(OUT)), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def check_release(frame: pd.DataFrame) -> None:
    if not OUT.exists() or not MANIFEST.exists():
        raise SystemExit("TPU压缩打印DOE发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("TPU压缩打印DOE端点与确定性重建不一致")
    expected = _manifest(frame, _sha256(OUT))
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("TPU压缩打印DOE发布清单不一致")
    print("TPU压缩打印DOE检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(json.dumps({"rows": len(frame)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
