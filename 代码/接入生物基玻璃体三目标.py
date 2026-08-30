"""提取生物基动态网络玻璃体的拉伸、松弛与TGA迁移端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from 提取TGA热稳定端点 import extract_tga_endpoints


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Zenodo_生物基共轭氨基甲酸酯玻璃体"
)
RAW_DIR = SOURCE_DIR / "解压内容" / "001_Data_Raw"
ARCHIVE = SOURCE_DIR / "001_Data_Raw.zip"
OUT_DIR = ROOT / "结果" / "定向筛选"
TENSILE_OUT = OUT_DIR / "生物基玻璃体拉伸端点.csv"
RELAXATION_OUT = OUT_DIR / "生物基玻璃体松弛端点.csv"
TGA_OUT = OUT_DIR / "生物基玻璃体TGA端点.csv"
MANIFEST = OUT_DIR / "生物基玻璃体发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _state_from_stem(stem: str) -> tuple[str, str]:
    match = re.search(r"-(P1T|X1T)(-recycled)?$", stem)
    if not match:
        raise ValueError(f"无法解析玻璃体配方/状态：{stem}")
    return match.group(1), "recycled" if match.group(2) else "original"


def _base_row(
    path: Path, formulation: str, state: str, target_role: str
) -> dict[str, object]:
    return {
        "source_id": "source_zenodo_21096098_v1",
        "formulation_id": formulation,
        "material_state": state,
        "material_class": "crosslinked_vinylogous_urethane_vitrimer",
        "target_role": target_role,
        "chemistry_mapping_status": "formulation_code_synthesis_family_mapped",
        "model_admission_layer": "dynamic_network_vitrimer_transfer",
        "thermoplastic_tpu_core": False,
        "usage_mode": "transfer_only_not_tpu_core",
        "sample_weight_ceiling": 0.20,
        "split_group": f"10.5281/zenodo.21096098|{formulation}",
        "source_locator": str(path.relative_to(ROOT)).replace("\\", "/"),
        "source_sha256": _sha256(path),
        "license": "CC-BY-4.0",
        "citation_keys": "reference-195;reference-196",
    }


def _build_tensile() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for path in sorted(RAW_DIR.glob("Tensile-test_Raw_original-*.csv")):
        formulation, state = _state_from_stem(path.stem)
        frame = pd.read_csv(path, skiprows=[1])
        frame.columns = [
            "source_specimen_id",
            "reported_young_modulus",
            "tensile_stress_at_break_MPa",
            "elongation_at_break_percent",
            "reported_toughness",
        ]
        frame = frame.apply(pd.to_numeric, errors="coerce")
        for row_index, row in frame.iterrows():
            source_specimen = row["source_specimen_id"]
            rows.append(
                {
                    **_base_row(
                        path,
                        formulation,
                        state,
                        "break_strength_elongation_transfer",
                    ),
                    "sample_id": (
                        f"{formulation}_{state}_row{row_index + 1}"
                    ),
                    "source_specimen_id": source_specimen,
                    "source_specimen_id_missing": bool(pd.isna(source_specimen)),
                    "tensile_stress_at_break_MPa": row[
                        "tensile_stress_at_break_MPa"
                    ],
                    "elongation_at_break_percent": row[
                        "elongation_at_break_percent"
                    ],
                    "reported_young_modulus": row[
                        "reported_young_modulus"
                    ],
                    "reported_young_modulus_unit": "source_header_GPa",
                    "young_modulus_admission_status": (
                        "quarantined_unit_magnitude_inconsistent"
                    ),
                    "reported_toughness": row["reported_toughness"],
                    "reported_toughness_unit": "source_header_J_per_m3",
                    "toughness_admission_status": (
                        "quarantined_unit_scale_unresolved"
                    ),
                }
            )
    return pd.DataFrame(rows).reset_index(drop=True)


def _first_crossing(
    time_s: np.ndarray, retention: np.ndarray, threshold: float
) -> float | None:
    hits = np.flatnonzero(retention <= threshold)
    if not len(hits):
        return None
    index = int(hits[0])
    if index == 0:
        return float(time_s[0])
    t0, t1 = float(time_s[index - 1]), float(time_s[index])
    y0, y1 = float(retention[index - 1]), float(retention[index])
    if y0 == y1:
        return t1
    return t0 + (threshold - y0) * (t1 - t0) / (y1 - y0)


def _build_relaxation() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    pattern = re.compile(
        r"Relaxation-test_Raw_original-(P1T|P3T|X1T|X3T)-(\d+)°C$"
    )
    for path in sorted(RAW_DIR.glob("Relaxation-test_Raw_original-*.csv")):
        if path.stem.endswith("-Arrhenius"):
            continue
        match = pattern.match(path.stem)
        if not match:
            raise ValueError(f"无法解析玻璃体松弛曲线：{path.name}")
        formulation, temperature = match.group(1), int(match.group(2))
        source = pd.read_csv(path, skiprows=[1], encoding="cp1252")
        extra_overlay = source.shape[1] > 2
        curve = source.iloc[:, :2].copy()
        curve.columns = ["time_s", "retention"]
        curve = curve.apply(pd.to_numeric, errors="coerce").dropna()
        time = curve["time_s"].to_numpy(dtype=float)
        retention = curve["retention"].to_numpy(dtype=float)
        elapsed = time - time[0]
        record: dict[str, object] = {
            **_base_row(
                path,
                formulation,
                "original",
                "stress_relaxation_transfer_proxy",
            ),
            "sample_id": f"{formulation}_{temperature}C_relaxation",
            "temperature_degC": temperature,
            "curve_point_count": int(len(curve)),
            "record_duration_s": float(elapsed[-1]),
            "retention_at_record_end": float(retention[-1]),
            "extra_overlay_columns_ignored": extra_overlay,
            "arrhenius_table_used_as_independent_data": False,
        }
        for target in (1, 10, 30, 100):
            record[f"retention_at_{target}s"] = (
                float(np.interp(target, elapsed, retention))
                if target <= elapsed[-1]
                else float("nan")
            )
        for threshold in (0.9, 0.8, 0.5):
            value = _first_crossing(elapsed, retention, threshold)
            label = int(threshold * 100)
            record[f"time_to_{label}pct_retention_s"] = value
            record[f"time_to_{label}pct_status"] = (
                "observed" if value is not None else "right_censored_at_record_end"
            )
        rows.append(record)
    return pd.DataFrame(rows).sort_values(
        ["formulation_id", "temperature_degC"]
    ).reset_index(drop=True)


def _build_tga() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for path in sorted(RAW_DIR.glob("TGA_Raw_original-*.csv")):
        formulation, state = _state_from_stem(path.stem)
        source = pd.read_csv(path, skiprows=[1], encoding="cp1252")
        curve = pd.DataFrame(
            {
                "temperature": source.iloc[:, 0],
                "mass": source.iloc[:, 1],
            }
        )
        endpoints = extract_tga_endpoints(curve)
        rows.append(
            {
                **_base_row(
                    path,
                    formulation,
                    state,
                    "direct_TGA_thermal_transfer",
                ),
                "sample_id": f"{formulation}_{state}_TGA",
                "source_curve_row_count": int(len(source)),
                **endpoints,
                "Td_onset_degC": pd.NA,
                "Td_onset_status": (
                    "not_derived_without_protocolized_tangent_method"
                ),
            }
        )
    return pd.DataFrame(rows).reset_index(drop=True)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return _build_tensile(), _build_relaxation(), _build_tga()


def _manifest(
    tensile: pd.DataFrame,
    relaxation: pd.DataFrame,
    tga: pd.DataFrame,
    output_hashes: dict[str, str],
) -> dict[str, object]:
    formulations = set(relaxation["formulation_id"]) | set(
        tensile["formulation_id"]
    )
    return {
        "release_id": "biobased_vitrimer_transfer_v1",
        "source": {
            "dataset_doi": "10.5281/zenodo.21096098",
            "concept_doi": "10.5281/zenodo.21096097",
            "article_doi": "10.1021/acspolymersau.6c00063",
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "formulation_count_in_published_targets": len(formulations),
            "full_synthesized_formulation_count": 8,
            "material_state_count_in_published_targets": 6,
            "physical_tensile_specimen_count": int(len(tensile)),
            "relaxation_curve_count": int(len(relaxation)),
            "relaxation_source_point_count": int(
                relaxation["curve_point_count"].sum()
            ),
            "tga_curve_count": int(len(tga)),
            "tga_source_row_count": int(tga["source_curve_row_count"].sum()),
            "tga_processed_unique_temperature_point_count": int(
                tga["point_count"].sum()
            ),
            "published_compact_row_count": int(
                len(tensile) + len(relaxation) + len(tga)
            ),
            "simulation_record_count": 0,
        },
        "policy": {
            "raw_curves_republished": False,
            "tpu_core_weight": 0.0,
            "transfer_weight_ceiling": 0.20,
            "reported_modulus_quarantined": True,
            "reported_toughness_quarantined": True,
            "arrhenius_tables_count_as_independent_data": False,
            "relaxation_is_proxy_not_direct_cycles": True,
            "split_group_rule": "dataset_doi|formulation",
        },
        "outputs": output_hashes,
    }


def write_release(
    tensile: pd.DataFrame, relaxation: pd.DataFrame, tga: pd.DataFrame
) -> None:
    outputs = (
        (TENSILE_OUT, tensile),
        (RELAXATION_OUT, relaxation),
        (TGA_OUT, tga),
    )
    for path, frame in outputs:
        frame.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
    hashes = {path.name: _sha256(path) for path, _ in outputs}
    MANIFEST.write_text(
        json.dumps(
            _manifest(tensile, relaxation, tga, hashes),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(
    tensile: pd.DataFrame, relaxation: pd.DataFrame, tga: pd.DataFrame
) -> None:
    outputs = (
        (TENSILE_OUT, tensile),
        (RELAXATION_OUT, relaxation),
        (TGA_OUT, tga),
    )
    if not MANIFEST.exists() or not all(path.exists() for path, _ in outputs):
        raise SystemExit("生物基玻璃体发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        for path, frame in outputs:
            candidate = Path(directory) / path.name
            frame.to_csv(
                candidate, index=False, encoding="utf-8-sig", lineterminator="\n"
            )
            if _sha256(candidate) != _sha256(path):
                raise SystemExit(f"生物基玻璃体输出不一致：{path.name}")
    hashes = {path.name: _sha256(path) for path, _ in outputs}
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        tensile, relaxation, tga, hashes
    ):
        raise SystemExit("生物基玻璃体发布清单不一致")
    print("生物基玻璃体三目标检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    tensile, relaxation, tga = build_release()
    if args.检查:
        check_release(tensile, relaxation, tga)
    else:
        write_release(tensile, relaxation, tga)
        print(
            json.dumps(
                {
                    "tensile": len(tensile),
                    "relaxation": len(relaxation),
                    "tga": len(tga),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
