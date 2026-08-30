"""提取两种未知配方浇注PU在高低速变形后的应力松弛迁移端点。"""

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
    / "Figshare_PU高低速变形后应力松弛"
)
ARCHIVE = SOURCE_DIR / "rspa20220830_si_002.zip"
OUT_DIR = ROOT / "结果" / "定向筛选"
CURVE_OUT = OUT_DIR / "PU高低速松弛曲线端点.csv"
CONDITION_OUT = OUT_DIR / "PU高低速松弛工况端点.csv"
MANIFEST = OUT_DIR / "PU高低速松弛发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _member(archive: zipfile.ZipFile, basename: str) -> str:
    return next(name for name in archive.namelist() if name.endswith(basename))


def _read_member(
    archive: zipfile.ZipFile, basename: str
) -> tuple[str, bytes, pd.DataFrame]:
    member = _member(archive, basename)
    raw = archive.read(member)
    if basename.endswith(".xlsx"):
        frame = pd.read_excel(io.BytesIO(raw), header=None)
    else:
        frame = pd.read_csv(io.BytesIO(raw), header=None)
    return member, raw, frame


def _first_crossing(
    elapsed: np.ndarray, retention: np.ndarray, threshold: float
) -> float | None:
    hits = np.flatnonzero(retention <= threshold)
    if not len(hits):
        return None
    index = int(hits[0])
    if index == 0:
        return float(elapsed[0])
    t0, t1 = float(elapsed[index - 1]), float(elapsed[index])
    y0, y1 = float(retention[index - 1]), float(retention[index])
    if y0 == y1:
        return t1
    return t0 + (threshold - y0) * (t1 - t0) / (y1 - y0)


def _curve_endpoint(
    curve: pd.DataFrame,
    *,
    time_unit: str,
) -> dict[str, object]:
    clean = curve.apply(pd.to_numeric, errors="coerce").dropna()
    clean = clean.sort_values(clean.columns[0]).drop_duplicates(clean.columns[0])
    time = clean.iloc[:, 0].to_numpy(dtype=float)
    stress = clean.iloc[:, 1].to_numpy(dtype=float)
    peak_index = int(np.argmax(stress))
    peak_stress = float(stress[peak_index])
    elapsed = time[peak_index:] - time[peak_index]
    retention = stress[peak_index:] / peak_stress
    record: dict[str, object] = {
        "curve_point_count": int(len(clean)),
        "peak_stress_source_unit": peak_stress,
        "peak_source_time": float(time[peak_index]),
        "source_time_unit": time_unit,
        "record_duration_after_peak": float(elapsed[-1]),
        "retention_at_record_end": float(retention[-1]),
        "relaxation_fraction_at_record_end": float(1.0 - retention[-1]),
    }
    targets = (10, 100, 300, 500) if time_unit == "s" else (20, 50, 100)
    for target in targets:
        record[f"retention_at_{target}{time_unit}"] = (
            float(np.interp(target, elapsed, retention))
            if target <= elapsed[-1]
            else float("nan")
        )
    for threshold in (0.9, 0.8, 0.5):
        value = _first_crossing(elapsed, retention, threshold)
        label = int(threshold * 100)
        record[f"time_to_{label}pct_retention"] = value
        record[f"time_to_{label}pct_status"] = (
            "observed" if value is not None else "right_censored_at_record_end"
        )
    positive = elapsed > 0
    if int(positive.sum()) >= 2:
        log_time = np.log10(elapsed[positive])
        record["normalized_log_time_auc"] = float(
            np.trapezoid(retention[positive], log_time)
            / (log_time[-1] - log_time[0])
        )
    else:
        record["normalized_log_time_auc"] = float("nan")
    return record


def _base_row(
    *,
    material: str,
    family: str,
    condition_key: str,
    replicate_index: int,
    member: str,
    raw: bytes,
    time_unit: str,
) -> dict[str, object]:
    return {
        "source_id": "source_figshare_23635998_v1",
        "material_grade": material,
        "formulation_id": material,
        "experiment_family": family,
        "condition_key": condition_key,
        "replicate_index": replicate_index,
        "material_class": "commercial_two_part_cast_polyurethane",
        "chemistry_mapping_status": "commercial_task_code_only",
        "target_role": "stress_relaxation_transfer_proxy",
        "physical_specimen_count_known": False,
        "curve_time_unit": time_unit,
        "model_admission_layer": (
            "unknown_chemistry_cast_PU_relaxation_auxiliary"
        ),
        "usage_mode": "evidence_only_nested_curve",
        "sample_weight_ceiling": 0.0,
        "split_group": f"10.6084/m9.figshare.23635998.v1|{material}",
        "source_member": member,
        "member_sha256": _raw_sha256(raw),
        "source_locator": f"{ARCHIVE.relative_to(ROOT).as_posix()}#{member}",
        "license": "CC-BY-4.0",
        "citation_keys": "reference-47;reference-48",
    }


def _pairs(
    frame: pd.DataFrame,
    *,
    pair_count: int,
) -> list[pd.DataFrame]:
    return [
        frame.iloc[:, [2 * index, 2 * index + 1]].dropna()
        for index in range(pair_count)
    ]


def _append_slow_temperature(
    rows: list[dict[str, object]],
    archive: zipfile.ZipFile,
    material: str,
    basename: str,
) -> None:
    member, raw, frame = _read_member(archive, basename)
    temperatures = (-60, -40, -20, 0, 20)
    for index, curve in enumerate(_pairs(frame, pair_count=15)):
        condition_index, replicate = divmod(index, 3)
        temperature = temperatures[condition_index]
        rows.append(
            {
                **_base_row(
                    material=material,
                    family="slow_temperature_relaxation",
                    condition_key=f"temperature_{temperature}C",
                    replicate_index=replicate + 1,
                    member=member,
                    raw=raw,
                    time_unit="s",
                ),
                "temperature_degC": temperature,
                "nominal_strain_percent": 2.0,
                "nominal_strain_rate_s-1": 0.001,
                **_curve_endpoint(curve, time_unit="s"),
            }
        )


def _append_slow_strain(
    rows: list[dict[str, object]],
    archive: zipfile.ZipFile,
    material: str,
    basename: str,
    strains: tuple[int, int, int],
) -> None:
    member, raw, frame = _read_member(archive, basename)
    for index, curve in enumerate(_pairs(frame, pair_count=9)):
        condition_index, replicate = divmod(index, 3)
        strain = strains[condition_index]
        rows.append(
            {
                **_base_row(
                    material=material,
                    family="slow_large_strain_relaxation",
                    condition_key=f"strain_{strain}pct",
                    replicate_index=replicate + 1,
                    member=member,
                    raw=raw,
                    time_unit="s",
                ),
                "temperature_degC": 20,
                "nominal_strain_percent": strain,
                "nominal_strain_rate_s-1": 0.001,
                **_curve_endpoint(curve, time_unit="s"),
            }
        )


def _append_shpb(
    rows: list[dict[str, object]],
    archive: zipfile.ZipFile,
    material: str,
    basename: str,
    geometry_mm: int,
    replicate_count: int,
) -> None:
    member, raw, frame = _read_member(archive, basename)
    for index, curve in enumerate(_pairs(frame, pair_count=replicate_count)):
        rows.append(
            {
                **_base_row(
                    material=material,
                    family=f"SHPB_{geometry_mm}mm_relaxation",
                    condition_key=f"SHPB_{geometry_mm}mm",
                    replicate_index=index + 1,
                    member=member,
                    raw=raw,
                    time_unit="us",
                ),
                "temperature_degC": 20,
                "nominal_strain_percent": pd.NA,
                "nominal_strain_rate_s-1": 1000,
                **_curve_endpoint(curve, time_unit="us"),
            }
        )


def _build_curves() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        _append_slow_temperature(rows, archive, "Task 3", "Figure_16a.csv")
        _append_slow_temperature(rows, archive, "Task 11", "Figure_16b.csv")
        _append_slow_strain(
            rows, archive, "Task 3", "Figure_20a.xlsx", (2, 4, 10)
        )
        _append_slow_strain(
            rows, archive, "Task 11", "Figure_20b.csv", (6, 10, 15)
        )
        _append_shpb(rows, archive, "Task 3", "Figure_22a.csv", 10, 3)
        _append_shpb(rows, archive, "Task 11", "Figure_22b.csv", 10, 3)
        _append_shpb(rows, archive, "Task 3", "Figure_26a.csv", 6, 3)
        _append_shpb(rows, archive, "Task 11", "Figure_26b.csv", 6, 2)
    return pd.DataFrame(rows).sort_values(
        ["material_grade", "experiment_family", "condition_key", "replicate_index"]
    ).reset_index(drop=True)


def _iqr(series: pd.Series) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.quantile(0.75) - clean.quantile(0.25)) if len(clean) else float("nan")


def _median(series: pd.Series) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.median()) if len(clean) else float("nan")


def _build_conditions(curves: pd.DataFrame) -> pd.DataFrame:
    keys = ["material_grade", "experiment_family", "condition_key"]
    numeric = [
        column
        for column in curves.columns
        if column.startswith("retention_at_")
        or column.startswith("time_to_") and not column.endswith("status")
        or column
        in {
            "peak_stress_source_unit",
            "retention_at_record_end",
            "relaxation_fraction_at_record_end",
            "normalized_log_time_auc",
        }
    ]
    rows: list[dict[str, object]] = []
    for group_key, group in curves.groupby(keys, sort=True):
        first = group.iloc[0]
        record: dict[str, object] = {
            "source_id": first["source_id"],
            "material_grade": group_key[0],
            "formulation_id": group_key[0],
            "experiment_family": group_key[1],
            "condition_key": group_key[2],
            "replicate_curve_count": int(len(group)),
            "physical_specimen_count_known": False,
            "curve_time_unit": first["curve_time_unit"],
            "temperature_degC": first["temperature_degC"],
            "nominal_strain_percent": first["nominal_strain_percent"],
            "nominal_strain_rate_s-1": first["nominal_strain_rate_s-1"],
            "target_role": "stress_relaxation_transfer_proxy_condition_aggregate",
            "chemistry_mapping_status": "commercial_task_code_only",
            "model_admission_layer": (
                "unknown_chemistry_cast_PU_relaxation_auxiliary"
            ),
            "usage_mode": "auxiliary_train_condition_aggregate",
            "sample_weight_ceiling": 0.20,
            "split_group": first["split_group"],
            "license": "CC-BY-4.0",
            "citation_keys": "reference-47;reference-48",
        }
        for column in numeric:
            record[f"{column}_median"] = _median(group[column])
            record[f"{column}_IQR"] = _iqr(group[column])
        rows.append(record)
    return pd.DataFrame(rows).reset_index(drop=True)


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    curves = _build_curves()
    return curves, _build_conditions(curves)


def _manifest(
    curves: pd.DataFrame,
    conditions: pd.DataFrame,
    curve_hash: str,
    condition_hash: str,
) -> dict[str, object]:
    return {
        "release_id": "cast_pu_high_low_rate_relaxation_v1",
        "source": {
            "dataset_doi": "10.6084/m9.figshare.23635998.v1",
            "article_doi": "10.1098/rspa.2022.0830",
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "material_code_count": int(curves["material_grade"].nunique()),
            "curve_evidence_row_count": int(len(curves)),
            "condition_aggregate_row_count": int(len(conditions)),
            "slow_temperature_curve_count": int(
                curves["experiment_family"].eq("slow_temperature_relaxation").sum()
            ),
            "slow_large_strain_curve_count": int(
                curves["experiment_family"].eq("slow_large_strain_relaxation").sum()
            ),
            "SHPB_10mm_curve_count": int(
                curves["experiment_family"].eq("SHPB_10mm_relaxation").sum()
            ),
            "SHPB_6mm_curve_count": int(
                curves["experiment_family"].eq("SHPB_6mm_relaxation").sum()
            ),
            "processed_curve_point_count": int(curves["curve_point_count"].sum()),
        },
        "policy": {
            "raw_curves_republished": False,
            "physical_specimen_count_known": False,
            "curve_rows_training_weight": 0.0,
            "condition_aggregate_weight_ceiling": 0.20,
            "direct_cyclic_recovery_available": False,
            "duplicate_or_transformed_figures_used": False,
            "Figure31_protocol_conflict_quarantined": True,
            "split_group_rule": "dataset_doi|material",
        },
        "outputs": {
            CURVE_OUT.name: curve_hash,
            CONDITION_OUT.name: condition_hash,
        },
    }


def write_release(curves: pd.DataFrame, conditions: pd.DataFrame) -> None:
    curves.to_csv(
        CURVE_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    conditions.to_csv(
        CONDITION_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    MANIFEST.write_text(
        json.dumps(
            _manifest(
                curves,
                conditions,
                _sha256(CURVE_OUT),
                _sha256(CONDITION_OUT),
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(curves: pd.DataFrame, conditions: pd.DataFrame) -> None:
    if not all(path.exists() for path in (CURVE_OUT, CONDITION_OUT, MANIFEST)):
        raise SystemExit("PU高低速松弛发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        for path, frame in ((CURVE_OUT, curves), (CONDITION_OUT, conditions)):
            candidate = Path(directory) / path.name
            frame.to_csv(
                candidate, index=False, encoding="utf-8-sig", lineterminator="\n"
            )
            if _sha256(candidate) != _sha256(path):
                raise SystemExit(f"PU高低速松弛输出不一致：{path.name}")
    expected = _manifest(
        curves, conditions, _sha256(CURVE_OUT), _sha256(CONDITION_OUT)
    )
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("PU高低速松弛发布清单不一致")
    print("PU高低速松弛检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    curves, conditions = build_release()
    if args.检查:
        check_release(curves, conditions)
    else:
        write_release(curves, conditions)
        print(
            json.dumps(
                {"curves": len(curves), "conditions": len(conditions)},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
