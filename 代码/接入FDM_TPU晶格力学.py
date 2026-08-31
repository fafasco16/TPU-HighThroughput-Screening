"""从Mendeley归档物化来源明确选中的FDM TPU基材/晶格力学端点。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import pandas as pd

from 接入DRUM机械回收 import _derive_endpoints


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Mendeley_FDM_TPU晶格与基材力学"
)
ARCHIVE = SOURCE_DIR / "dbzdkz95f8-1.zip"
AUDIT = SOURCE_DIR / "曲线审计清单.tsv"
OUT = ROOT / "结果" / "定向筛选" / "FDM_TPU晶格基材力学端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "FDM_TPU晶格力学发布清单.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _member(archive: zipfile.ZipFile, workbook_stem: str) -> str:
    suffix = f"/{workbook_stem}.xlsx"
    return next(name for name in archive.namelist() if name.endswith(suffix))


def _curve_endpoint(frame: pd.DataFrame) -> dict[str, object]:
    curve = frame.iloc[3:, [0, 1]].copy()
    curve.columns = ["strain", "stress"]
    curve = curve.apply(pd.to_numeric, errors="coerce").dropna()
    endpoints = _derive_endpoints(curve)
    return {
        "curve_point_count": endpoints["curve_point_count"],
        "peak_stress_MPa": endpoints["tensile_strength_MPa"],
        "max_observed_strain_percent": endpoints["elongation_at_break_percent"],
        "curve_area_MJ_m3": endpoints["toughness_MJ_m3"],
        "modulus_0_5pct_MPa": endpoints["young_modulus_0_5pct_MPa"],
        "negative_strain_step_fraction": endpoints[
            "negative_strain_step_fraction"
        ],
        "endpoint_quality_status": endpoints["endpoint_quality_status"],
    }


def build_release() -> pd.DataFrame:
    audit = pd.read_csv(AUDIT, sep="\t")
    selected = audit[audit["source_summary_state"].eq("selected")].copy()
    rows: list[dict[str, object]] = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for condition, group in selected.groupby("条件", sort=True):
            member = _member(archive, str(condition))
            raw = archive.read(member)
            sheet_names = [str(value).split(":", 1)[1] for value in group["曲线ID"]]
            sheets = pd.read_excel(
                io.BytesIO(raw), sheet_name=sheet_names, header=None
            )
            for audit_row in group.itertuples(index=False):
                curve_id = str(getattr(audit_row, "曲线ID"))
                sample_id = curve_id.split(":", 1)[1]
                endpoints = _curve_endpoint(sheets[sample_id])
                expected_points = int(getattr(audit_row, "点数"))
                if endpoints["curve_point_count"] != expected_points:
                    raise ValueError(
                        f"曲线点数不一致：{curve_id} "
                        f"{endpoints['curve_point_count']} != {expected_points}"
                    )
                test_type = str(getattr(audit_row, "试验类型"))
                rows.append(
                    {
                        "source_id": "source_mendeley_dbzdkz95f8_v1",
                        "material_grade": "FDM_printed_TPU_unknown_grade",
                        "formulation_id": "FDM_printed_TPU_unknown_grade",
                        "test_type": test_type,
                        "condition_name": condition,
                        "sample_id": sample_id,
                        "curve_id": curve_id,
                        **endpoints,
                        "curve_area_semantics": (
                            "stress_strain_energy_absorption_proxy_not_fracture_toughness"
                        ),
                        "material_class": "FDM_printed_TPU_base_and_lattice",
                        "chemistry_mapping_status": "commercial_grade_unresolved",
                        "target_role": (
                            "direct_experimental_stress_strain_area_application_proxy"
                        ),
                        "model_admission_layer": "FDM_TPU_application_transfer",
                        "usage_mode": "application_transfer_after_source_group_split",
                        "sample_weight_ceiling": 0.35,
                        "source_summary_state": "selected",
                        "quality_gate": getattr(audit_row, "quality_gate"),
                        "split_group": (
                            "10.17632/dbzdkz95f8.1|FDM_printed_TPU_unknown_grade"
                        ),
                        "source_member": member,
                        "member_sha256": _raw_sha256(raw),
                        "source_locator": (
                            f"{ARCHIVE.relative_to(ROOT).as_posix()}#"
                            f"{member};sheet={sample_id}"
                        ),
                        "license": "CC-BY-4.0",
                        "citation_keys": "reference-93",
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["test_type", "condition_name", "sample_id"]
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    audit = pd.read_csv(AUDIT, sep="\t")
    return {
        "release_id": "fdm_tpu_lattice_substrate_mechanics_v1",
        "source": {
            "dataset_doi": "10.17632/dbzdkz95f8.1",
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "material_grade_count": 1,
            "source_curve_count": int(len(audit)),
            "selected_curve_count": int(len(frame)),
            "conflict_curve_count": int(
                audit["source_summary_state"].eq("conflict").sum()
            ),
            "not_selected_curve_count": int(
                audit["source_summary_state"].eq("not_selected").sum()
            ),
            "selected_curve_point_count": int(frame["curve_point_count"].sum()),
            "published_compact_row_count": int(len(frame)),
            "recognized_simulation_run_count": 0,
        },
        "selected_by_test_type": {
            str(key): int(value)
            for key, value in frame.groupby("test_type").size().items()
        },
        "policy": {
            "raw_curves_republished": False,
            "conflict_curves_republished": False,
            "source_not_selected_curves_republished": False,
            "geometry_conditions_increase_material_count": False,
            "curve_area_is_fracture_toughness": False,
            "split_group_rule": "dataset_doi|material_grade",
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
        raise SystemExit("FDM TPU晶格力学发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("FDM TPU晶格力学端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("FDM TPU晶格力学发布清单不一致")
    print("FDM TPU晶格力学检查通过")


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
