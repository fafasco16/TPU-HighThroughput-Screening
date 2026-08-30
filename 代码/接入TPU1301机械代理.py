"""从Zenodo原始归档提取EOS TPU 1301拉伸与应力松弛紧凑端点。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np
import pandas as pd

from 接入DRUM机械回收 import _derive_endpoints


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Zenodo_TPU1301热黏弹黏塑本构"
)
ARCHIVE = SOURCE_DIR / "ijss_2025_vevp_ScriptsForTestsImages.zip"
TENSILE_OUT = ROOT / "结果" / "定向筛选" / "TPU1301拉伸端点.csv"
RELAXATION_OUT = ROOT / "结果" / "定向筛选" / "TPU1301应力松弛端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "TPU1301机械代理发布清单.json"
SOURCE_PREFIX = "ijss_2025_vevp_ScriptsForTestsImages/Experiments/TPU/"
EXCLUDED_RELAXATION = "Relaxation_7H_1E-1_RT_TPU.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_curve(raw: bytes) -> tuple[dict[str, str], pd.DataFrame]:
    lines = raw.decode("utf-8-sig").splitlines()
    header_index = next(
        index for index, line in enumerate(lines) if line.startswith("Time\t")
    )
    metadata: dict[str, str] = {}
    for line in lines[:header_index]:
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        metadata[key.rstrip(": ")] = value.strip()
    curve = pd.read_csv(
        io.StringIO("\n".join(lines[header_index + 2 :])),
        sep="\t",
        header=None,
        names=["time_s", "extension_mm", "load_N", "strain", "stress_MPa"],
    )
    curve = curve.apply(pd.to_numeric, errors="coerce").dropna()
    return metadata, curve


def _base_row(member: str, raw: bytes, metadata: dict[str, str]) -> dict[str, object]:
    sample = metadata.get("Specimen label")
    return {
        "source_id": "source_zenodo_15370425_v1",
        "material_grade": "EOS TPU 1301",
        "formulation_id": "EOS TPU 1301",
        "sample_id": sample,
        "material_class": "commercial_sls_thermoplastic_polyurethane",
        "chemistry_mapping_status": "commercial_grade_identity_only",
        "test_temperature": "room_temperature",
        "strain_rate_s-1": pd.to_numeric(
            metadata.get("Strain rate (s-1)"), errors="coerce"
        ),
        "thickness_mm": pd.to_numeric(
            metadata.get("Thickness (mm)"), errors="coerce"
        ),
        "width_mm": pd.to_numeric(metadata.get("Width (mm)"), errors="coerce"),
        "height_mm": pd.to_numeric(metadata.get("Height (mm)"), errors="coerce"),
        "model_admission_layer": "core_tpu_application_experimental",
        "split_group": "10.5281/zenodo.15370425|EOS TPU 1301",
        "source_member": member,
        "member_sha256": _raw_sha256(raw),
        "source_locator": f"{ARCHIVE.relative_to(ROOT).as_posix()}#{member}",
        "license": "CC-BY-4.0",
        "citation_keys": "reference-38;reference-39",
    }


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


def _relaxation_endpoints(curve: pd.DataFrame) -> dict[str, object]:
    stress = curve["stress_MPa"].to_numpy(dtype=float)
    time = curve["time_s"].to_numpy(dtype=float)
    strain = curve["strain"].to_numpy(dtype=float)
    peak_index = int(np.argmax(stress))
    reference_stress = float(stress[peak_index])
    elapsed = time[peak_index:] - time[peak_index]
    retention = stress[peak_index:] / reference_stress
    result: dict[str, object] = {
        "curve_point_count": int(len(curve)),
        "peak_time_s": float(time[peak_index]),
        "peak_strain": float(strain[peak_index]),
        "reference_peak_stress_MPa": reference_stress,
        "record_duration_after_peak_s": float(elapsed[-1]),
        "retention_at_record_end": float(retention[-1]),
    }
    for target in (1, 10, 100, 300):
        result[f"retention_at_{target}s"] = float(
            np.interp(target, elapsed, retention)
        )
    for threshold in (0.9, 0.8, 0.5):
        result[f"time_to_{int(threshold * 100)}pct_retention_s"] = (
            _first_crossing(elapsed, retention, threshold)
        )
    return result


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    tensile_rows: list[dict[str, object]] = []
    relaxation_rows: list[dict[str, object]] = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for member in sorted(archive.namelist()):
            basename = PurePosixPath(member).name
            if not member.startswith(SOURCE_PREFIX) or not basename.endswith(".csv"):
                continue
            is_tensile = basename.startswith("Uniaxial_tension_")
            is_relaxation = basename.startswith("Relaxation_")
            if not (is_tensile or is_relaxation):
                continue
            if basename == EXCLUDED_RELAXATION:
                continue
            raw = archive.read(member)
            metadata, curve = _parse_curve(raw)
            common = _base_row(member, raw, metadata)
            if is_tensile:
                endpoints = _derive_endpoints(
                    pd.DataFrame(
                        {
                            "strain": curve["strain"] * 100.0,
                            "stress": curve["stress_MPa"],
                        }
                    )
                )
                tensile_rows.append(
                    {
                        **common,
                        "target_role": "direct_tensile_curve_area_application",
                        "usage_mode": "application_auxiliary_train",
                        "sample_weight_ceiling": 0.65,
                        **endpoints,
                    }
                )
            else:
                relaxation_rows.append(
                    {
                        **common,
                        "target_role": "stress_relaxation_recovery_proxy",
                        "usage_mode": "application_auxiliary_proxy",
                        "sample_weight_ceiling": 0.45,
                        **_relaxation_endpoints(curve),
                    }
                )
    tensile = pd.DataFrame(tensile_rows).sort_values("sample_id").reset_index(drop=True)
    relaxation = (
        pd.DataFrame(relaxation_rows).sort_values("sample_id").reset_index(drop=True)
    )
    return tensile, relaxation


def _manifest(
    tensile: pd.DataFrame,
    relaxation: pd.DataFrame,
    tensile_hash: str,
    relaxation_hash: str,
) -> dict[str, object]:
    return {
        "release_id": "tpu1301_mechanical_proxy_v1",
        "source": {
            "dataset_doi": "10.5281/zenodo.15370425",
            "article_doi": "10.1016/j.ijsolstr.2025.113517",
            "license": "CC-BY-4.0",
            "archive_sha256": _sha256(ARCHIVE),
        },
        "counts": {
            "material_grade_count": 1,
            "tensile_run_count": int(len(tensile)),
            "relaxation_run_count": int(len(relaxation)),
            "quarantined_identity_conflict_count": 1,
            "source_point_count": int(
                tensile["curve_point_count"].sum()
                + relaxation["curve_point_count"].sum()
            ),
            "published_compact_row_count": int(len(tensile) + len(relaxation)),
        },
        "policy": {
            "raw_curves_republished": False,
            "quarantined_member": EXCLUDED_RELAXATION,
            "quarantine_reason": "filename_7H_embedded_specimen_label_6V",
            "material_count_rule": "one_commercial_grade_many_runs_and_conditions",
            "relaxation_is_proxy_not_direct_cycles": True,
        },
        "outputs": {
            TENSILE_OUT.name: tensile_hash,
            RELAXATION_OUT.name: relaxation_hash,
        },
    }


def write_release(tensile: pd.DataFrame, relaxation: pd.DataFrame) -> None:
    tensile.to_csv(
        TENSILE_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    relaxation.to_csv(
        RELAXATION_OUT, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    MANIFEST.write_text(
        json.dumps(
            _manifest(
                tensile,
                relaxation,
                _sha256(TENSILE_OUT),
                _sha256(RELAXATION_OUT),
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(tensile: pd.DataFrame, relaxation: pd.DataFrame) -> None:
    if not all(path.exists() for path in (TENSILE_OUT, RELAXATION_OUT, MANIFEST)):
        raise SystemExit("TPU1301机械代理发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        tensile_candidate = Path(directory) / TENSILE_OUT.name
        relaxation_candidate = Path(directory) / RELAXATION_OUT.name
        tensile.to_csv(
            tensile_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        relaxation.to_csv(
            relaxation_candidate,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        if _sha256(tensile_candidate) != _sha256(TENSILE_OUT):
            raise SystemExit("TPU1301拉伸端点与确定性重建不一致")
        if _sha256(relaxation_candidate) != _sha256(RELAXATION_OUT):
            raise SystemExit("TPU1301松弛端点与确定性重建不一致")
    expected = _manifest(
        tensile,
        relaxation,
        _sha256(TENSILE_OUT),
        _sha256(RELAXATION_OUT),
    )
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != expected:
        raise SystemExit("TPU1301机械代理发布清单不一致")
    print("TPU1301机械代理检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    tensile, relaxation = build_release()
    if args.检查:
        check_release(tensile, relaxation)
    else:
        write_release(tensile, relaxation)
        print(
            json.dumps(
                {"tensile": len(tensile), "relaxation": len(relaxation)},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
