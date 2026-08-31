"""提取六种微球体积分数PU复合材料的加载-卸载条件均值端点。"""

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
    / "Zenodo_PU微球复合材料拉伸"
)
ARCHIVE = SOURCE_DIR / "Data_csv.zip"
OUT = ROOT / "结果" / "定向筛选" / "PU微球复合加载卸载端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "PU微球复合发布清单.json"
VOLUME_FRACTIONS = (0, 5, 10, 15, 20, 25)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _member(archive: zipfile.ZipFile, volume_fraction: int) -> str:
    suffix = f"Data_csv/Post/poro_{volume_fraction:02d}_moyenne.csv"
    return next(name for name in archive.namelist() if name.endswith(suffix))


def _endpoints(frame: pd.DataFrame) -> dict[str, object]:
    frame = frame.apply(pd.to_numeric, errors="coerce").dropna()
    frame.columns = ["stretch", "nominal_stress", "lateral_stretch"]
    peak_stretch_index = int(frame["stretch"].idxmax())
    loading = frame.loc[:peak_stretch_index].copy()
    unloading = frame.loc[peak_stretch_index:].copy()
    loading_strain = loading["stretch"].to_numpy(dtype=float) - 1.0
    unloading_strain = unloading["stretch"].to_numpy(dtype=float) - 1.0
    loading_stress = np.maximum(
        loading["nominal_stress"].to_numpy(dtype=float), 0.0
    )
    unloading_stress = np.maximum(
        unloading["nominal_stress"].to_numpy(dtype=float), 0.0
    )
    loading_area = float(np.trapezoid(loading_stress, loading_strain))
    recovered_area = float(abs(np.trapezoid(unloading_stress, unloading_strain)))
    hysteresis = loading_area - recovered_area
    return {
        "curve_point_count": int(len(frame)),
        "maximum_stretch_ratio": float(frame["stretch"].max()),
        "maximum_engineering_strain_percent": float(
            (frame["stretch"].max() - 1.0) * 100.0
        ),
        "peak_nominal_stress_source_unit": float(
            frame["nominal_stress"].max()
        ),
        "loading_area_source_stress_unit": loading_area,
        "recovered_area_source_stress_unit": recovered_area,
        "hysteresis_area_source_stress_unit": hysteresis,
        "energy_recovery_ratio": (
            recovered_area / loading_area if loading_area > 0 else float("nan")
        ),
        "hysteresis_fraction": (
            hysteresis / loading_area if loading_area > 0 else float("nan")
        ),
        "minimum_lateral_stretch_ratio": float(
            frame["lateral_stretch"].min()
        ),
        "lateral_stretch_at_maximum_axial_stretch": float(
            loading.iloc[-1]["lateral_stretch"]
        ),
    }


def build_release() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for volume_fraction in VOLUME_FRACTIONS:
            member = _member(archive, volume_fraction)
            raw = archive.read(member)
            frame = pd.read_csv(
                io.BytesIO(raw),
                header=None,
                names=["stretch", "nominal_stress", "lateral_stretch"],
            )
            formulation = f"PU_microsphere_{volume_fraction:02d}volpct"
            rows.append(
                {
                    "source_id": "source_zenodo_6390478_v1",
                    "formulation_id": formulation,
                    "microsphere_volume_fraction_percent": volume_fraction,
                    "physical_specimen_count": 2,
                    **_endpoints(frame),
                    "stress_unit_status": (
                        "unresolved_in_deposit_metadata_no_MPa_claim"
                    ),
                    "area_semantics": (
                        "source_stress_unit_times_engineering_strain_"
                        "not_MJ_per_m3_until_unit_closed"
                    ),
                    "target_role": (
                        "loading_area_and_hysteresis_transfer_proxy"
                    ),
                    "material_class": (
                        "polyurethane_hollow_thermoplastic_microsphere_composite"
                    ),
                    "chemistry_mapping_status": (
                        "microsphere_fraction_mapped_matrix_identity_unresolved"
                    ),
                    "model_admission_layer": "PU_microsphere_composite_transfer",
                    "usage_mode": "conditional_transfer_until_stress_unit_closed",
                    "sample_weight_ceiling": 0.15,
                    "split_group": f"10.5281/zenodo.6390478|{formulation}",
                    "source_member": member,
                    "member_sha256": _raw_sha256(raw),
                    "source_locator": (
                        f"{ARCHIVE.relative_to(ROOT).as_posix()}#{member}"
                    ),
                    "license": "CC-BY-4.0",
                    "citation_keys": "reference-42;reference-43",
                }
            )
    return pd.DataFrame(rows).sort_values(
        "microsphere_volume_fraction_percent"
    ).reset_index(drop=True)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    return {
        "release_id": "pu_microsphere_composite_loading_unloading_v1",
        "source": {
            "dataset_doi": "10.5281/zenodo.6390478",
            "article_doi": "10.1007/s42558-022-00046-1",
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "composition_condition_count": int(len(frame)),
            "physical_specimen_count": int(frame["physical_specimen_count"].sum()),
            "condition_mean_curve_count": int(len(frame)),
            "condition_mean_curve_point_count": int(
                frame["curve_point_count"].sum()
            ),
            "machine_curve_count_not_republished": 12,
            "DIC_curve_count_not_republished": 24,
            "machine_point_count_not_republished": 7974,
            "DIC_usable_point_count_not_republished": 15939,
            "published_compact_row_count": int(len(frame)),
        },
        "policy": {
            "raw_curves_republished": False,
            "condition_mean_curves_are_new_specimens": False,
            "stress_unit_closed": False,
            "absolute_MPa_or_MJ_m3_values_published": False,
            "MinMax_semantic_duplicate_quarantined": True,
            "microsphere_fraction_increases_composition_count": True,
            "two_specimens_per_condition": True,
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
        raise SystemExit("PU微球复合发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("PU微球复合端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("PU微球复合发布清单不一致")
    print("PU微球复合检查通过")


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
