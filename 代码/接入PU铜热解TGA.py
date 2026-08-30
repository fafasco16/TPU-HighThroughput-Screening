"""提取PU漆包铜线及无铜参考的多升温速率TGA/DTG紧凑端点。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd

from 提取TGA热稳定端点 import extract_tga_endpoints


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第八批混合_PU铜调控热解多尺度"
)
WORKBOOK = SOURCE_DIR / "原始数据.xlsx"
COMPUTATIONAL_AUDIT = SOURCE_DIR / "计算观测清单.tsv"
OUT = ROOT / "结果" / "定向筛选" / "PU铜热解TGA端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "PU铜热解TGA发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _numeric_pair(
    frame: pd.DataFrame, x_column: int, y_column: int
) -> pd.DataFrame:
    pair = frame.iloc[1:, [x_column, y_column]].copy()
    pair.columns = ["temperature", "response"]
    return pair.apply(pd.to_numeric, errors="coerce").dropna()


def _dtg_peak(curve: pd.DataFrame) -> tuple[float, float]:
    index = curve["response"].idxmin()
    return float(curve.loc[index, "temperature"]), float(curve.loc[index, "response"])


def _record(
    *,
    material: str,
    copper_status: str,
    heating_rate: int,
    tg: pd.DataFrame,
    dtg: pd.DataFrame,
    source_sheet: str,
    source_columns: str,
    admission_status: str,
    weight: float,
    qc_flags: str,
) -> dict[str, object]:
    endpoints = extract_tga_endpoints(
        tg.rename(columns={"response": "mass"})
    )
    dtg_temperature, dtg_rate = _dtg_peak(dtg)
    t50_observed = not pd.isna(endpoints["T50_degC"])
    return {
        "source_id": "source_zenodo_18414263_v1",
        "material_state": material,
        "formulation_id": material,
        "copper_status": copper_status,
        "heating_rate_degC_min": heating_rate,
        "source_TG_point_count": int(len(tg)),
        "source_DTG_point_count": int(len(dtg)),
        **endpoints,
        "T50_status": (
            "observed"
            if t50_observed
            else "right_censored_by_residual_mass_or_temperature_range"
        ),
        "DTG_peak_temperature_degC": dtg_temperature,
        "DTG_peak_rate_percent_per_min": dtg_rate,
        "Td_onset_degC": pd.NA,
        "Td_onset_status": "not_derived_without_protocolized_tangent_method",
        "target_role": "direct_TGA_thermal_transfer",
        "chemistry_mapping_status": "commercial_PU_enamel_identity_unresolved",
        "model_admission_layer": "pu_pyrolysis_thermal_transfer",
        "usage_mode": "thermal_transfer_only_not_TPU_core",
        "admission_status": admission_status,
        "sample_weight_ceiling": weight,
        "reported_replicates": 3 if copper_status == "Cu-containing" else pd.NA,
        "replicate_values_available": False,
        "independent_specimen_count_known": False,
        "qc_flags": qc_flags,
        "split_group": f"10.5281/zenodo.18414263|{material}",
        "source_sheet": source_sheet,
        "source_columns": source_columns,
        "source_locator": (
            f"{WORKBOOK.relative_to(ROOT).as_posix()}#sheet={source_sheet};"
            f"columns={source_columns}"
        ),
        "source_workbook_sha256": _sha256(WORKBOOK),
        "license": "CC-BY-4.0",
        "citation_keys": "reference-138;reference-139",
    }


def build_release() -> pd.DataFrame:
    main = pd.read_excel(WORKBOOK, sheet_name="Figure 1a-d", header=None)
    cu_free = pd.read_excel(
        WORKBOOK, sheet_name="Figure1e Cu-free TG DTG", header=None
    )
    specs = [
        (5, 2, 3, 0, 1, "C:D/A:B", "admitted_reference", 0.25, "averaged_curve_only"),
        (10, 5, 6, 7, 8, "F:G/H:I", "admitted_reference", 0.25, "averaged_curve_only"),
        (15, 10, 11, 12, 13, "K:L/M:N", "admitted_reference", 0.25, "averaged_curve_only"),
        (25, 15, 16, 17, 18, "P:Q/R:S", "admitted_reference", 0.25, "averaged_curve_only"),
        (
            20,
            20,
            21,
            22,
            23,
            "U:V/W:X",
            "conditional_reference",
            0.10,
            "article_protocol_omits_20cpm;averaged_curve_only",
        ),
    ]
    rows = []
    for rate, tx, ty, dx, dy, columns, status, weight, qc in specs:
        rows.append(
            _record(
                material="commercial_PU_enamelled_copper_wire",
                copper_status="Cu-containing",
                heating_rate=rate,
                tg=_numeric_pair(main, tx, ty),
                dtg=_numeric_pair(main, dx, dy),
                source_sheet="Figure 1a-d",
                source_columns=columns,
                admission_status=status,
                weight=weight,
                qc_flags=qc,
            )
        )
    rows.append(
        _record(
            material="PU_enamel_Cu-free_reference",
            copper_status="Cu-free",
            heating_rate=5,
            tg=_numeric_pair(cu_free, 0, 1),
            dtg=_numeric_pair(cu_free, 0, 2),
            source_sheet="Figure1e Cu-free TG DTG",
            source_columns="A:B/A:C",
            admission_status="admitted_reference",
            weight=0.20,
            qc_flags="averaged_curve_only;formulation_unresolved",
        )
    )
    return pd.DataFrame(rows).sort_values(
        ["copper_status", "heating_rate_degC_min"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    computational = pd.read_csv(COMPUTATIONAL_AUDIT, sep="\t")
    return {
        "release_id": "pu_copper_pyrolysis_tga_v1",
        "source": {
            "dataset_doi": "10.5281/zenodo.18414263",
            "concept_doi": "10.5281/zenodo.18414262",
            "article_doi": "10.1038/s43247-026-03339-9",
            "license": "CC-BY-4.0",
            "workbook_sha256": _sha256(WORKBOOK),
        },
        "counts": {
            "material_state_count": int(frame["material_state"].nunique()),
            "TGA_curve_count": int(len(frame)),
            "admitted_curve_count": int(
                frame["admission_status"].eq("admitted_reference").sum()
            ),
            "conditional_curve_count": int(
                frame["admission_status"].eq("conditional_reference").sum()
            ),
            "source_TG_point_count": int(frame["source_TG_point_count"].sum()),
            "source_DTG_point_count": int(frame["source_DTG_point_count"].sum()),
            "published_compact_row_count": int(len(frame)),
            "T50_observed_count": int(frame["T50_degC"].notna().sum()),
            "T50_right_censored_count": int(frame["T50_degC"].isna().sum()),
            "computational_audit_record_count_not_republished": int(
                len(computational)
            ),
        },
        "policy": {
            "raw_curves_republished": False,
            "computational_records_republished": False,
            "reason_computational_not_republished": (
                "audit_rows_lack_numeric_endpoint_values_and_remain_mechanistic_metadata"
            ),
            "20cpm_protocol_conflict_retained_as_conditional": True,
            "independent_specimen_count_known": False,
            "TPU_core_weight": 0.0,
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
        raise SystemExit("PU铜热解TGA发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("PU铜热解TGA端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("PU铜热解TGA发布清单不一致")
    print("PU铜热解TGA检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(json.dumps({"curves": len(frame)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
