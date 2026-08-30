"""从低天花板TPUU开放来源提取四条原始TGA曲线的紧凑端点。"""

from __future__ import annotations

import argparse
import hashlib
import io
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
    / "DRUM_TPUU_低天花板"
)
TGA_DIR = SOURCE_DIR / "解包内容" / "Raw_TGA"
OUT = ROOT / "结果" / "定向筛选" / "低天花板TPUU热稳定端点.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "低天花板TPUU热稳定发布清单.json"
MATERIALS = ("C", "D", "R", "S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_source(path: Path) -> tuple[dict[str, str], pd.DataFrame]:
    text = path.read_text(encoding="utf-16")
    header, data = text.split("StartOfData", 1)
    metadata: dict[str, str] = {}
    for line in header.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        metadata[key.strip()] = value.strip()
    curve = pd.read_csv(
        io.StringIO(data),
        sep="\t",
        header=None,
        names=[
            "time_min",
            "temperature",
            "mass",
            "balance_purge_mL_min",
            "sample_purge_mL_min",
        ],
    )
    return metadata, curve


def build_release() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for code in MATERIALS:
        path = TGA_DIR / f"TPUU_{code}.txt"
        metadata, curve = _parse_source(path)
        endpoints = extract_tga_endpoints(curve[["temperature", "mass"]])
        formulation = f"TPUU-{code}"
        rows.append(
            {
                "source_id": "source_drum_zf53w893",
                "formulation_id": formulation,
                "sample_id": metadata.get("Sample"),
                "material_class": "thermoplastic_polyurethane_urea",
                "chemistry_mapping_status": "formulation_id_only",
                "mapping_status": (
                    "formulation_code_resolved_to_tpuu_family_exact_composition_pending"
                ),
                "target_role": "direct_thermal_stability",
                **endpoints,
                "instrument": metadata.get("Instrument"),
                "original_method": metadata.get("OrgMethod"),
                "original_sample_mass": metadata.get("Size"),
                "model_admission_layer": "core_tpuu_experimental",
                "usage_mode": "direct_train_after_group_split",
                "sample_weight_ceiling": 0.75,
                "split_group": f"10.13020/zf53-w893|{formulation}",
                "source_locator": str(path.relative_to(ROOT)).replace("\\", "/"),
                "source_sha256": _sha256(path),
                "license": "CC0-1.0",
                "citation_keys": "reference-55;reference-56",
            }
        )
    return pd.DataFrame(rows)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    return {
        "release_id": "low_ceiling_tpuu_tga_v1",
        "source": {
            "dataset_doi": "10.13020/zf53-w893",
            "article_doi": "10.1021/acs.macromol.4c01431",
            "license": "CC0-1.0",
        },
        "counts": {
            "material_count": int(frame["formulation_id"].nunique()),
            "curve_count": int(len(frame)),
            "source_point_count": int(frame["point_count"].sum()),
            "published_compact_row_count": int(len(frame)),
        },
        "algorithm": {
            "baseline": "maximum_of_first_5_percent_minimum_3_points",
            "normalization": "100*mass/baseline",
            "noise_handling": "cumulative_minimum_envelope",
            "thresholds_mass_percent": [95, 90, 50],
            "interpolation": "linear_between_bracketing_points",
            "Td_onset": "not_derived_without_protocolized_tangent_method",
        },
        "policy": {
            "raw_curves_republished": False,
            "material_identity": "four_formulation_codes_same_source_as_tensile_cycles",
            "sample_weight_ceiling": 0.75,
        },
        "inputs": [
            {
                "path": row.source_locator,
                "sha256": row.source_sha256,
            }
            for row in frame.itertuples(index=False)
        ],
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
        raise SystemExit("低天花板TPUU热稳定发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("低天花板TPUU热稳定端点与确定性重建不一致")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("低天花板TPUU热稳定发布清单不一致")
    print("低天花板TPUU热稳定检查通过")


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
                    "source_points": int(frame["point_count"].sum()),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
