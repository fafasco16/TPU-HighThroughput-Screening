"""提取标准化商业热塑性弹性体的应力松弛代理端点。

原始曲线只保留在本地来源包中；Git 发布物仅包含可复核的紧凑端点。
该数据是循环恢复性的低权重代理，不等同于直接循环拉伸实验。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
import zipfile
from functools import lru_cache
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
    / "Zenodo_标准化弹性体表征"
)
SOURCE = SOURCE_DIR / "Stress relaxation.zip"
OUT = ROOT / "结果" / "定向筛选" / "标准热塑性弹性体松弛端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "标准热塑性弹性体松弛发布清单.json"

MATERIALS = ("Cheetah", "Filaflex 60A")
HOLD_STRAIN_PERCENT = 25.0
HOLD_TOLERANCE_PERCENT = 0.01
STABLE_POINT_COUNT = 100
ENDPOINT_TIMES_S = (1, 10, 100, 1000, 5000, 10000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _member_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_hold_index(strain: np.ndarray, peak_index: int) -> int:
    within = np.abs(strain - HOLD_STRAIN_PERCENT) <= HOLD_TOLERANCE_PERCENT
    run_length = 0
    for index in range(peak_index, len(within)):
        if within[index]:
            run_length += 1
            if run_length == STABLE_POINT_COUNT:
                return index - STABLE_POINT_COUNT + 1
        else:
            run_length = 0
    raise ValueError("未找到连续100点稳定的25%应变保持段")


def _interpolate(time_s: np.ndarray, values: np.ndarray, target_s: float) -> float:
    if target_s > time_s[-1]:
        return float("nan")
    return float(np.interp(target_s, time_s, values))


def _first_crossing(
    time_s: np.ndarray, retention: np.ndarray, threshold: float
) -> float | None:
    candidates = np.flatnonzero(retention <= threshold)
    if not len(candidates):
        return None
    index = int(candidates[0])
    if index == 0:
        return float(time_s[0])
    t0, t1 = float(time_s[index - 1]), float(time_s[index])
    y0, y1 = float(retention[index - 1]), float(retention[index])
    if y1 == y0:
        return t1
    fraction = (threshold - y0) / (y1 - y0)
    return t0 + fraction * (t1 - t0)


def _curve_endpoints(material: str, raw: bytes, member: str) -> dict[str, object]:
    frame = pd.read_csv(io.BytesIO(raw))
    frame = frame[["Force (N)", "Strain (%)", "Stress (MPa)", "Time (s)"]].dropna()
    time = frame["Time (s)"].to_numpy(dtype=float)
    strain = frame["Strain (%)"].to_numpy(dtype=float)
    stress = frame["Stress (MPa)"].to_numpy(dtype=float)

    peak_index = int(np.argmax(stress))
    stable_index = _stable_hold_index(strain, peak_index)
    reference_time = float(time[stable_index])
    reference_stress = float(stress[stable_index])
    elapsed = time[stable_index:] - reference_time
    retention = stress[stable_index:] / reference_stress

    endpoints = {
        f"retention_at_{target}s": _interpolate(elapsed, retention, target)
        for target in ENDPOINT_TIMES_S
    }
    t90 = _first_crossing(elapsed, retention, 0.90)
    t80 = _first_crossing(elapsed, retention, 0.80)
    t50 = _first_crossing(elapsed, retention, 0.50)

    return {
        "source_id": "source_zenodo_14983287_v1",
        "material_grade": material,
        "formulation_id": material,
        "material_class": "commercial_thermoplastic_elastomer",
        "target_role": "stress_relaxation_transfer_proxy",
        "nominal_hold_strain_percent": HOLD_STRAIN_PERCENT,
        "overshoot_peak_time_s": float(time[peak_index]),
        "overshoot_peak_strain_percent": float(strain[peak_index]),
        "overshoot_peak_stress_MPa": float(stress[peak_index]),
        "stable_hold_reference_time_s": reference_time,
        "stable_hold_reference_stress_MPa": reference_stress,
        "stable_hold_detection": (
            "first_100_consecutive_points_within_25_plusminus_0.01pct"
        ),
        "record_duration_after_reference_s": float(elapsed[-1]),
        **endpoints,
        "retention_at_record_end": float(retention[-1]),
        "time_to_90pct_retention_s": t90,
        "time_to_80pct_retention_s": t80,
        "time_to_50pct_retention_s": t50,
        "time_to_50pct_status": (
            "observed" if t50 is not None else "right_censored_at_record_end"
        ),
        "source_point_count": int(len(frame)),
        "physical_specimen_count_known": False,
        "chemistry_mapping_status": "commercial_grade_identity_only",
        "model_admission_layer": "commercial_elastomer_auxiliary",
        "usage_mode": "auxiliary_train_proxy",
        "sample_weight_ceiling": 0.35,
        "material_join_key": f"zenodo14983287|{material}",
        "split_group": f"10.5281/zenodo.14983287|{material}",
        "source_member": member,
        "member_sha256": _member_sha256(raw),
        "source_locator": f"{SOURCE.relative_to(ROOT).as_posix()}#{member}",
        "license": "CC-BY-4.0",
        "citation_keys": "reference-40;reference-41",
    }


@lru_cache(maxsize=1)
def build_release() -> pd.DataFrame:
    rows = []
    with zipfile.ZipFile(SOURCE) as archive:
        for material in MATERIALS:
            member = f"Stress relaxation/{material}-relaxation.csv"
            raw = archive.read(member)
            rows.append(_curve_endpoints(material, raw, member))
    return pd.DataFrame(rows)


def _manifest_payload(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    return {
        "release_id": "standard_elastomer_relaxation_v1",
        "source": {
            "title": (
                "A standardized elastomer characterization framework for soft "
                "robotics - accompanying dataset"
            ),
            "doi": "10.5281/zenodo.14983287",
            "related_article_doi": "10.1002/aisy.202500699",
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(SOURCE),
        },
        "counts": {
            "material_grade_count": int(frame["material_grade"].nunique()),
            "relaxation_curve_count": int(len(frame)),
            "source_point_count": int(frame["source_point_count"].sum()),
            "published_compact_row_count": int(len(frame)),
        },
        "policy": {
            "target_semantics": "stress_relaxation_proxy_not_direct_cycles",
            "normalization_reference": "first_stable_25pct_hold_point_after_overshoot",
            "raw_curves_republished": False,
            "physical_specimen_count_known": False,
            "sample_weight_ceiling": 0.35,
        },
        "output_sha256": output_hash,
    }


def write_release(frame: pd.DataFrame) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(_manifest_payload(frame, _sha256(OUT)), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def check_release(frame: pd.DataFrame) -> None:
    if not OUT.exists() or not MANIFEST.exists():
        raise SystemExit("标准热塑性弹性体松弛发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("标准热塑性弹性体松弛端点与确定性重建不一致")
    expected = _manifest_payload(frame, _sha256(OUT))
    actual = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if actual != expected:
        raise SystemExit("标准热塑性弹性体松弛发布清单与确定性重建不一致")
    print("标准热塑性弹性体松弛检查通过")


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
                    "materials": len(frame),
                    "source_points": int(frame["source_point_count"].sum()),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
