"""将无溶剂宏二醇-二异氰酸酯NCO滴定曲线压缩为合成时间窗端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    / "第十批实验_无溶剂PU反应动力学"
)
CONDITIONS = SOURCE_DIR / "反应条件清单.tsv"
MEASUREMENTS = SOURCE_DIR / "NCO测量长表.tsv"
ARCHIVE = SOURCE_DIR / "Solvent_Free_Adhesives_Dataset_5-2.zip"
OUT = ROOT / "结果" / "定向筛选" / "无溶剂PU反应动力学端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "无溶剂PU反应动力学发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _threshold_time(
    time_h: np.ndarray,
    nco_pct: np.ndarray,
    threshold: float,
) -> tuple[float | None, str]:
    if nco_pct[0] <= threshold:
        return float(time_h[0]), "left_censored_at_first_measurement"
    hits = np.flatnonzero(nco_pct <= threshold)
    if not len(hits):
        return None, "right_censored_at_last_measurement"
    index = int(hits[0])
    t0, t1 = float(time_h[index - 1]), float(time_h[index])
    y0, y1 = float(nco_pct[index - 1]), float(nco_pct[index])
    if y0 == y1:
        return t1, "observed"
    return t0 + (threshold - y0) * (t1 - t0) / (y1 - y0), "observed"


def _condition_endpoint(
    condition: pd.Series, measurements: pd.DataFrame
) -> dict[str, object]:
    timed = measurements.dropna(subset=["时间_h_原始"]).copy()
    timed["时间_h_原始"] = pd.to_numeric(timed["时间_h_原始"])
    timed["实测NCO_pct"] = pd.to_numeric(timed["实测NCO_pct"])
    curve = (
        timed.groupby("时间_h_原始", as_index=False)["实测NCO_pct"]
        .median()
        .sort_values("时间_h_原始")
    )
    time_h = curve["时间_h_原始"].to_numpy(dtype=float)
    nco = np.minimum.accumulate(curve["实测NCO_pct"].to_numpy(dtype=float))
    theoretical = float(condition["工作簿理论初始NCO_pct"])
    t50, t50_status = _threshold_time(time_h, nco, theoretical * 0.5)
    t90, t90_status = _threshold_time(time_h, nco, theoretical * 0.1)
    initial_slope = (
        float((nco[1] - nco[0]) / (time_h[1] - time_h[0]))
        if len(curve) >= 2 and time_h[1] != time_h[0]
        else float("nan")
    )
    return {
        "source_id": "source_zenodo_6406174_v1",
        "condition_id": condition["条件ID"],
        "reaction_system": condition["反应体系"],
        "macrodiol_code": condition["宏二醇代码"],
        "macrodiol_identity": condition["宏二醇化学身份"],
        "macrodiol_CAS": condition["宏二醇CAS"],
        "macrodiol_Mn_g_mol": condition["宏二醇Mn_g_mol"],
        "diisocyanate_code": condition["二异氰酸酯代码"],
        "diisocyanate_identity": condition["二异氰酸酯化学身份"],
        "diisocyanate_CAS": condition["二异氰酸酯CAS"],
        "diisocyanate_MW_g_mol": condition["二异氰酸酯分子量_g_mol"],
        "macrodiol_molar_parts": condition["宏二醇摩尔份"],
        "isocyanate_molar_parts": condition["异氰酸酯摩尔份"],
        "molar_ratio": condition["摩尔比"],
        "temperature_degC": condition["温度_C"],
        "workbook_theoretical_initial_NCO_percent": theoretical,
        "paper_batch_theoretical_initial_NCO_percent": condition[
            "论文批次理论初始NCO_pct"
        ],
        "source_measurement_row_count": int(len(measurements)),
        "missing_time_measurement_count": int(
            measurements["时间_h_原始"].isna().sum()
        ),
        "replicate_series_count": int(measurements["测量列ID"].nunique()),
        "unique_timed_point_count": int(len(curve)),
        "first_measurement_time_h": float(time_h[0]),
        "first_measured_NCO_percent": float(nco[0]),
        "last_measurement_time_h": float(time_h[-1]),
        "last_measured_NCO_percent": float(nco[-1]),
        "NCO_retention_at_last_measurement": float(nco[-1] / theoretical),
        "initial_NCO_decline_rate_percent_per_h": initial_slope,
        "time_to_50pct_theoretical_NCO_h": t50,
        "time_to_50pct_status": t50_status,
        "time_to_10pct_theoretical_NCO_h": t90,
        "time_to_10pct_status": t90_status,
        "zero_NCO_observed": bool((measurements["实测NCO_pct"] == 0).any()),
        "target_role": "synthesis_feasibility_NCO_consumption_kinetics",
        "model_admission_layer": "synthesis_kinetics_experimental",
        "usage_mode": "reaction_window_and_synthesis_priority_model",
        "direct_mechanical_supervision": False,
        "sample_weight_ceiling": 0.60,
        "split_group": condition["拆分组"],
        "license": "CC-BY-4.0",
        "citation_keys": "reference-159;reference-160",
    }


def build_release() -> pd.DataFrame:
    conditions = pd.read_csv(CONDITIONS, sep="\t")
    measurements = pd.read_csv(MEASUREMENTS, sep="\t")
    rows = []
    for _, condition in conditions.iterrows():
        subset = measurements[
            measurements["条件ID"].eq(condition["条件ID"])
        ].copy()
        if subset.empty:
            raise ValueError(f"条件缺少测量点：{condition['条件ID']}")
        rows.append(_condition_endpoint(condition, subset))
    return pd.DataFrame(rows).sort_values("condition_id").reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    measurements = pd.read_csv(MEASUREMENTS, sep="\t")
    return {
        "release_id": "solvent_free_pu_reaction_kinetics_v1",
        "source": {
            "dataset_doi": "10.5281/zenodo.6406174",
            "article_doi": "10.1039/D2RA08326D",
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "reaction_condition_count": int(len(frame)),
            "reaction_system_count": int(frame["reaction_system"].nunique()),
            "macrodiol_identity_count": int(frame["macrodiol_code"].nunique()),
            "diisocyanate_identity_count": int(
                frame["diisocyanate_code"].nunique()
            ),
            "source_measurement_row_count": int(len(measurements)),
            "admitted_measurement_count": int(
                measurements["准入状态"].eq("admitted_reference").sum()
            ),
            "conditional_missing_time_count": int(
                measurements["时间_h_原始"].isna().sum()
            ),
            "zero_NCO_measurement_count": int(
                measurements["实测NCO_pct"].eq(0).sum()
            ),
            "published_compact_row_count": int(len(frame)),
        },
        "policy": {
            "raw_measurement_long_table_republished": False,
            "missing_times_imputed": False,
            "theoretical_t0_counted_as_measured": False,
            "direct_toughness_or_cycle_label": False,
            "condition_level_split_required": True,
        },
        "output_sha256": output_hash,
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
        raise SystemExit("无溶剂PU反应动力学发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("无溶剂PU反应动力学端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("无溶剂PU反应动力学发布清单不一致")
    print("无溶剂PU反应动力学检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(json.dumps({"conditions": len(frame)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
