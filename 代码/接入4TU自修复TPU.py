"""从4TU原始压缩包提取SH-TPU刀切修复配对与TGA端点。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
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
    / "力学曲线"
    / "SelfHealingTPU_4TU"
)
ARCHIVE = SOURCE_DIR / "source_data.zip"
RECOVERY_OUT = ROOT / "结果" / "定向筛选" / "4TU自修复TPU恢复配对.csv"
TGA_OUT = ROOT / "结果" / "定向筛选" / "4TU自修复TPUTGA端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "4TU自修复TPU发布清单.json"

DATASET_DOI = "10.4121/13603775.v1"
ARTICLE_DOI = "10.3390/polym13020305"
EXPECTED_ARCHIVE_SHA256 = (
    "9d563b8389686530a1a73e62a0244c57a1c19b8a039b60ec63f0753b2ff034a8"
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_curve(payload: bytes) -> pd.DataFrame:
    frame = pd.read_csv(
        io.BytesIO(payload), sep=";", skiprows=[1], encoding="latin1"
    )
    frame = frame.iloc[:, :2].apply(pd.to_numeric, errors="coerce").dropna()
    frame.columns = ["displacement_mm", "load_N"]
    return (
        frame.groupby("displacement_mm", as_index=False)["load_N"]
        .median()
        .sort_values("displacement_mm")
        .reset_index(drop=True)
    )


def _integrate_to(frame: pd.DataFrame, terminal_mm: float) -> float:
    below = frame.loc[frame["displacement_mm"] < terminal_mm].copy()
    terminal_load = float(
        np.interp(
            terminal_mm,
            frame["displacement_mm"],
            frame["load_N"],
        )
    )
    terminal = pd.DataFrame(
        {"displacement_mm": [terminal_mm], "load_N": [terminal_load]}
    )
    integrated = (
        pd.concat([below, terminal], ignore_index=True)
        .drop_duplicates("displacement_mm", keep="last")
        .sort_values("displacement_mm")
    )
    return float(
        np.trapezoid(integrated["load_N"], integrated["displacement_mm"])
    )


def _curve_endpoints(
    frame: pd.DataFrame, terminal_mm: float
) -> dict[str, float | int]:
    common = frame.loc[frame["displacement_mm"] <= terminal_mm]
    peak_index = int(common["load_N"].idxmax())
    return {
        "point_count": int(len(frame)),
        "maximum_observed_displacement_mm": float(
            frame["displacement_mm"].max()
        ),
        "common_terminal_displacement_mm": terminal_mm,
        "peak_load_to_common_terminal_N": float(common.loc[peak_index, "load_N"]),
        "displacement_at_peak_load_mm": float(
            common.loc[peak_index, "displacement_mm"]
        ),
        "cut_work_to_common_terminal_mJ": _integrate_to(frame, terminal_mm),
    }


def _parse_pair_identity(member: str) -> dict[str, object]:
    stem = Path(member).stem.removesuffix("_healed")
    ninjaflex = re.fullmatch(
        r"Ninjaflex_(\d+)C_(\d+(?:\.\d+)?)_(XY|XZ)_(\d+)", stem
    )
    if ninjaflex:
        temperature, infill, orientation, replicate = ninjaflex.groups()
        return {
            "formulation_id": "Ninjaflex",
            "material_state": "FDM_printed",
            "printing_temperature_degC": int(temperature),
            "infill_distance_mm": float(infill),
            "cut_plane_orientation": orientation,
            "replicate_id": int(replicate),
            "chemistry_mapping_status": "commercial_grade_only",
            "component_1": "Ninjaflex commercial TPU",
            "component_2": "",
            "component_3": "",
            "component_molar_ratio": "",
            "sample_weight_ceiling": 0.35,
        }
    printed = re.fullmatch(r"SH-TPU_(\d+)C_(XY|XZ)_(\d+)", stem)
    if printed:
        temperature, orientation, replicate = printed.groups()
        return {
            "formulation_id": "SH-TPU",
            "material_state": "FDM_printed",
            "printing_temperature_degC": int(temperature),
            "infill_distance_mm": float("nan"),
            "cut_plane_orientation": orientation,
            "replicate_id": int(replicate),
            "chemistry_mapping_status": "monomer_set_molar_composition_mapped",
            "component_1": "CroHeal 2000",
            "component_2": "2-ethyl-1,3-hexanediol (EHD)",
            "component_3": "4,4'-methylenebis(phenyl isocyanate) (MDI)",
            "component_molar_ratio": "1:0.6:1.7",
            "sample_weight_ceiling": 0.55,
        }
    pristine = re.fullmatch(r"SH-TPU_pristine_sample_(\d+)", stem)
    if pristine:
        return {
            "formulation_id": "SH-TPU",
            "material_state": "pristine_bulk_polymer",
            "printing_temperature_degC": float("nan"),
            "infill_distance_mm": float("nan"),
            "cut_plane_orientation": "bulk",
            "replicate_id": int(pristine.group(1)),
            "chemistry_mapping_status": "monomer_set_molar_composition_mapped",
            "component_1": "CroHeal 2000",
            "component_2": "2-ethyl-1,3-hexanediol (EHD)",
            "component_3": "4,4'-methylenebis(phenyl isocyanate) (MDI)",
            "component_molar_ratio": "1:0.6:1.7",
            "sample_weight_ceiling": 0.55,
        }
    raise ValueError(f"无法解析修复配对身份: {member}")


def _build_recovery(archive: zipfile.ZipFile) -> pd.DataFrame:
    healed_members = sorted(
        member
        for member in archive.namelist()
        if member.startswith("Data/Mechanical testing/")
        and member.lower().endswith("_healed.csv")
    )
    rows: list[dict[str, object]] = []
    for healed_member in healed_members:
        original_member = healed_member[: -len("_healed.csv")] + ".csv"
        if original_member not in archive.namelist():
            raise ValueError(f"缺少修复前配对曲线: {original_member}")
        original_payload = archive.read(original_member)
        healed_payload = archive.read(healed_member)
        original = _read_curve(original_payload)
        healed = _read_curve(healed_payload)
        terminal = min(
            float(original["displacement_mm"].max()),
            float(healed["displacement_mm"].max()),
        )
        before = _curve_endpoints(original, terminal)
        after = _curve_endpoints(healed, terminal)
        identity = _parse_pair_identity(healed_member)
        pair_id = Path(original_member).stem
        rows.append(
            {
                "source_id": "source_4tu_13603775_v1",
                "pair_id": pair_id,
                **identity,
                "original_point_count": before["point_count"],
                "healed_point_count": after["point_count"],
                "common_terminal_displacement_mm": terminal,
                "original_peak_load_N": before[
                    "peak_load_to_common_terminal_N"
                ],
                "healed_peak_load_N": after["peak_load_to_common_terminal_N"],
                "peak_load_recovery_fraction": (
                    after["peak_load_to_common_terminal_N"]
                    / before["peak_load_to_common_terminal_N"]
                ),
                "original_cut_work_mJ": before[
                    "cut_work_to_common_terminal_mJ"
                ],
                "healed_cut_work_mJ": after["cut_work_to_common_terminal_mJ"],
                "cut_work_recovery_fraction": (
                    after["cut_work_to_common_terminal_mJ"]
                    / before["cut_work_to_common_terminal_mJ"]
                ),
                "healing_temperature_degC": 30,
                "healing_time_h": 24,
                "external_pressure_during_healing": False,
                "test_temperature_degC": 20,
                "blade_speed_mm_s": 10,
                "specimen_dimensions_mm": "4x4x4",
                "blade_tip_angle_deg": 18,
                "blade_tip_length_mm": 0.75,
                "blade_body_width_mm": 0.20,
                "target_role": "direct_compression_cut_healing_recovery_proxy",
                "complete_tensile_toughness_available": False,
                "model_admission_layer": "core_TPU_healing_experimental",
                "split_group": f"{DATASET_DOI}|{identity['formulation_id']}",
                "original_source_locator": f"source_data.zip!/{original_member}",
                "healed_source_locator": f"source_data.zip!/{healed_member}",
                "original_member_sha256": _sha256_bytes(original_payload),
                "healed_member_sha256": _sha256_bytes(healed_payload),
                "license": "CC-BY-4.0",
                "citation_keys": "reference-21",
            }
        )
    return pd.DataFrame(rows).sort_values(
        [
            "formulation_id",
            "material_state",
            "printing_temperature_degC",
            "cut_plane_orientation",
            "replicate_id",
        ],
        na_position="last",
    ).reset_index(drop=True)


def _crossing_temperature(
    temperature: np.ndarray, remaining_pct: np.ndarray, target: float
) -> float:
    indices = np.flatnonzero((temperature >= 120) & (remaining_pct <= target))
    if not len(indices):
        return float("nan")
    current = int(indices[0])
    if current == 0:
        return float(temperature[current])
    t0, t1 = temperature[current - 1], temperature[current]
    y0, y1 = remaining_pct[current - 1], remaining_pct[current]
    if y0 == y1:
        return float(t1)
    return float(t0 + (target - y0) * (t1 - t0) / (y1 - y0))


def _build_tga(archive: zipfile.ZipFile) -> pd.DataFrame:
    members = sorted(
        member
        for member in archive.namelist()
        if member.startswith("Data/TGA/") and member.lower().endswith(".csv")
    )
    rows: list[dict[str, object]] = []
    for member in members:
        payload = archive.read(member)
        raw = pd.read_csv(
            io.BytesIO(payload),
            sep=";",
            skiprows=[1],
            encoding="latin1",
        ).iloc[:, :5]
        raw = raw.apply(pd.to_numeric, errors="coerce").dropna(
            subset=[raw.columns[1], raw.columns[4]]
        )
        curve = pd.DataFrame(
            {
                "temperature_degC": raw.iloc[:, 4],
                "mass_g": raw.iloc[:, 1],
            }
        )
        curve = (
            curve.groupby("temperature_degC", as_index=False)["mass_g"]
            .median()
            .sort_values("temperature_degC")
        )
        baseline = curve.loc[
            curve["temperature_degC"].between(80, 120), "mass_g"
        ].median()
        if pd.isna(baseline) or baseline <= 0:
            raise ValueError(f"TGA基准质量无效: {member}")
        temperature = curve["temperature_degC"].to_numpy(dtype=float)
        remaining = curve["mass_g"].to_numpy(dtype=float) / baseline * 100
        state = (
            "FDM_filament" if "filament" in member.lower() else "pristine_polymer"
        )
        rows.append(
            {
                "source_id": "source_4tu_13603775_v1",
                "formulation_id": "SH-TPU",
                "material_state": state,
                "component_1": "CroHeal 2000",
                "component_2": "2-ethyl-1,3-hexanediol (EHD)",
                "component_3": "4,4'-methylenebis(phenyl isocyanate) (MDI)",
                "component_molar_ratio": "1:0.6:1.7",
                "chemistry_mapping_status": (
                    "monomer_set_molar_composition_mapped"
                ),
                "raw_curve_point_count": int(len(raw)),
                "curve_point_count": int(len(curve)),
                "baseline_mass_g": float(baseline),
                "baseline_temperature_window_degC": "80-120",
                "T5_degC": _crossing_temperature(temperature, remaining, 95),
                "T10_degC": _crossing_temperature(temperature, remaining, 90),
                "T50_degC": _crossing_temperature(temperature, remaining, 50),
                "terminal_temperature_degC": float(temperature[-1]),
                "terminal_remaining_mass_pct": float(remaining[-1]),
                "atmosphere": "nitrogen",
                "heating_rate_degC_min": 5,
                "program_temperature_range_degC": "30-600",
                "target_role": "direct_TGA_thermal_stability",
                "model_admission_layer": "core_TPU_thermal_experimental",
                "sample_weight_ceiling": 0.30,
                "split_group": f"{DATASET_DOI}|SH-TPU",
                "source_locator": f"source_data.zip!/{member}",
                "source_member_sha256": _sha256_bytes(payload),
                "license": "CC-BY-4.0",
                "citation_keys": "reference-21",
            }
        )
    return pd.DataFrame(rows).sort_values("material_state").reset_index(drop=True)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    if _sha256(ARCHIVE) != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("4TU原始压缩包SHA-256与冻结审计不一致")
    with zipfile.ZipFile(ARCHIVE) as archive:
        recovery = _build_recovery(archive)
        tga = _build_tga(archive)
    return recovery, tga


def _manifest(
    recovery: pd.DataFrame,
    tga: pd.DataFrame,
    recovery_hash: str,
    tga_hash: str,
) -> dict[str, object]:
    with zipfile.ZipFile(ARCHIVE) as archive:
        members = archive.namelist()
        mechanical = [
            member
            for member in members
            if member.startswith("Data/Mechanical testing/")
            and member.lower().endswith(".csv")
        ]
    return {
        "release_id": "4tu_self_healing_tpu_targeted_v1",
        "source": {
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "archive_member_count": len(members),
            "archive_file_member_count": sum(
                not member.endswith("/") for member in members
            ),
            "mechanical_curve_count": len(mechanical),
            "original_physical_specimen_key_count": sum(
                not member.lower().endswith("_healed.csv")
                for member in mechanical
            ),
            "healing_pair_count": len(recovery),
            "healing_pair_curve_count": len(recovery) * 2,
            "unpaired_original_mechanical_curve_hold_count": (
                len(mechanical) - len(recovery) * 2
            ),
            "tga_curve_count": len(tga),
            "tga_raw_point_count": int(tga["raw_curve_point_count"].sum()),
            "tga_unique_temperature_point_count": int(
                tga["curve_point_count"].sum()
            ),
            "published_compact_row_count": len(recovery) + len(tga),
        },
        "policy": {
            "raw_curves_republished": False,
            "unpaired_process_curves_used_as_recovery_labels": False,
            "compression_cut_work_claimed_as_tensile_toughness": False,
            "recovery_ratios_clipped_to_0_1": False,
            "tga_baseline": "median_mass_between_80_and_120_degC",
            "same_material_states_grouped_together": True,
        },
        "outputs": {
            RECOVERY_OUT.name: recovery_hash,
            TGA_OUT.name: tga_hash,
        },
    }


def write_release(recovery: pd.DataFrame, tga: pd.DataFrame) -> None:
    recovery.to_csv(
        RECOVERY_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    tga.to_csv(TGA_OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(
            _manifest(recovery, tga, _sha256(RECOVERY_OUT), _sha256(TGA_OUT)),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(recovery: pd.DataFrame, tga: pd.DataFrame) -> None:
    if not RECOVERY_OUT.exists() or not TGA_OUT.exists() or not MANIFEST.exists():
        raise SystemExit("4TU自修复TPU发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        recovery_candidate = Path(directory) / RECOVERY_OUT.name
        tga_candidate = Path(directory) / TGA_OUT.name
        recovery.to_csv(
            recovery_candidate,
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
        if _sha256(recovery_candidate) != _sha256(RECOVERY_OUT):
            raise SystemExit("4TU自修复TPU恢复配对与确定性重建不一致")
        if _sha256(tga_candidate) != _sha256(TGA_OUT):
            raise SystemExit("4TU自修复TPU TGA端点与确定性重建不一致")
    expected = _manifest(
        recovery,
        tga,
        _sha256(RECOVERY_OUT),
        _sha256(TGA_OUT),
    )
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("4TU自修复TPU发布清单不一致")
    print("4TU自修复TPU检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    recovery, tga = build_release()
    if args.检查:
        check_release(recovery, tga)
    else:
        write_release(recovery, tga)
        print(
            json.dumps(
                {"recovery_pairs": len(recovery), "tga_curves": len(tga)},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
