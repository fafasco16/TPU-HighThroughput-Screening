"""从第十六批再生 PU 泡沫 Gold-E 长表生成分模态参考包。

该来源不是热塑性 TPU。压缩曲线、压缩端点、黏度、热导率和配方表
分别发布，统一标记为 PU 泡沫迁移层，避免把不同物态的数据混入 TPU
核心监督标签。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十六批实验_再生PU泡沫"
)
GOLD = SOURCE_DIR / "Gold_E_实验记录.tsv"
AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUTPUT_DIR = ROOT / "结果" / "定向筛选"
MANIFEST = OUTPUT_DIR / "再生PU泡沫发布清单.json"

OUTPUTS = {
    "compression_points": OUTPUT_DIR / "再生PU泡沫压缩曲线.csv",
    "compression_endpoints": OUTPUT_DIR / "再生PU泡沫压缩端点.csv",
    "viscosity_points": OUTPUT_DIR / "再生PU泡沫黏度曲线.csv",
    "thermal_conductivity": OUTPUT_DIR / "再生PU泡沫热导端点.csv",
    "formulation_components": OUTPUT_DIR / "再生PU泡沫配方组件.csv",
    "aggregate_scalars": OUTPUT_DIR / "再生PU泡沫聚合标量.csv",
}

DATASET_DOI = "10.5281/zenodo.5713819"
ARTICLE_DOI = "10.12688/openreseurope.13288.2"
LICENSE = "CC-BY-4.0"
SOURCE_ID = "source_zenodo_5713819_v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load() -> pd.DataFrame:
    if not GOLD.exists() or not AUDIT.exists():
        raise FileNotFoundError("再生PU泡沫 Gold-E 或审计摘要缺失")
    frame = pd.read_csv(GOLD, sep="\t", low_memory=False)
    required = {
        "source_directory",
        "source_record_id",
        "observation_id",
        "formulation_id",
        "record_kind",
        "property_name",
        "value",
        "unit",
        "gold_admission_status",
        "split_group",
        "source_locator",
        "file_sha256",
        "citation_keys",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Gold-E 缺少字段: {sorted(missing)}")
    if not frame["source_directory"].eq("第十六批实验_再生PU泡沫").all():
        raise ValueError("Gold-E 混入其他来源目录")
    if not frame["split_group"].eq("family_elorza_recycled_pu_foam").all():
        raise ValueError("再生 PU 泡沫 split_group 不一致")
    return frame


def _select(frame: pd.DataFrame, *, kinds: set[str], properties: set[str]) -> pd.DataFrame:
    selected = frame[frame["record_kind"].isin(kinds)].copy()
    if properties:
        selected = selected[selected["property_name"].isin(properties)].copy()
    if selected.empty:
        raise ValueError(f"空发布包: kinds={kinds}, properties={properties}")
    return selected.reset_index(drop=True)


def build_release() -> dict[str, pd.DataFrame]:
    frame = _load()
    return {
        "compression_points": _select(
            frame,
            kinds={"curve_point"},
            properties={"compressive_stress"},
        ),
        "compression_endpoints": _select(
            frame,
            kinds={"derived_scalar"},
            properties={
                "compressive_stress_at_10_percent_strain",
                "compressive_stress_at_25_percent_strain",
            },
        ),
        "viscosity_points": _select(
            frame,
            kinds={"curve_point"},
            properties={"dynamic_viscosity"},
        ),
        "thermal_conductivity": _select(
            frame,
            kinds={"scalar_measurement"},
            properties={"thermal_conductivity"},
        ),
        "formulation_components": _select(
            frame,
            kinds={"formulation_component"},
            properties={"formulation_mass_fraction"},
        ),
        "aggregate_scalars": _select(
            frame,
            kinds={"aggregate_scalar"},
            properties=set(),
        ),
    }


def _manifest(
    frames: dict[str, pd.DataFrame], output_hashes: dict[str, str]
) -> dict[str, object]:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    source_archive = next(SOURCE_DIR.glob("*.rar"), None)
    counts = {
        "gold_e_row_count": int(sum(len(frame) for frame in frames.values())),
        "compression_curve_point_count": len(frames["compression_points"]),
        "compression_curve_count": int(
            frames["compression_points"]
            .loc[:, ["formulation_id", "sample_id"]]
            .drop_duplicates()
            .shape[0]
        ),
        "compression_endpoint_row_count": len(frames["compression_endpoints"]),
        "viscosity_curve_point_count": len(frames["viscosity_points"]),
        "viscosity_curve_count": int(
            frames["viscosity_points"]["formulation_id"].nunique()
        ),
        "thermal_conductivity_row_count": len(frames["thermal_conductivity"]),
        "formulation_component_row_count": len(frames["formulation_components"]),
        "aggregate_scalar_row_count": len(frames["aggregate_scalars"]),
        "published_output_count": len(OUTPUTS),
        "published_compact_row_count": int(sum(len(frame) for frame in frames.values())),
    }
    return {
        "release_id": "recycled_pu_foam_gold_e_modal_v2",
        "source": {
            "source_id": SOURCE_ID,
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "license": LICENSE,
            "archive_sha256": audit["source"]["archive_sha256"],
            "archive_file": source_archive.name if source_archive else None,
            "gold_e_sha256": _sha256(GOLD),
            "audit_summary_sha256": _sha256(AUDIT),
            "peer_review_status": audit["source"]["peer_review_status"],
        },
        "counts": counts,
        "outputs": {
            key: {
                "path": path.relative_to(ROOT).as_posix(),
                "row_count": len(frames[key]),
                "sha256": output_hashes[key],
            }
            for key, path in OUTPUTS.items()
        },
        "policy": {
            "material_class": "recycled_and_soy_polyurethane_foam",
            "model_admission_layer": "polyurethane_foam_transfer",
            "tpu_core_supervision": False,
            "compression_energy_is_fracture_toughness": False,
            "compression_curve_is_quasistatic_bulk_tpu": False,
            "viscosity_is_reaction_kinetics": False,
            "thermal_conductivity_is_thermal_decomposition": False,
            "all_source_rows_share_split_group": True,
            "conditional_reference_rows_keep_source_status": True,
            "source_archive_not_republished": True,
        },
    }


def write_release(frames: dict[str, pd.DataFrame]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for key, path in OUTPUTS.items():
        frames[key].to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
        hashes[key] = _sha256(path)
    MANIFEST.write_text(
        json.dumps(_manifest(frames, hashes), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def check_release(frames: dict[str, pd.DataFrame]) -> None:
    if not MANIFEST.exists():
        raise SystemExit("再生 PU 泡沫发布清单尚未生成")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    hashes = {}
    for key, path in OUTPUTS.items():
        if not path.exists():
            raise SystemExit(f"再生 PU 泡沫发布物缺失: {path.name}")
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / path.name
            frames[key].to_csv(
                candidate, index=False, encoding="utf-8-sig", lineterminator="\n"
            )
            if _sha256(candidate) != _sha256(path):
                raise SystemExit(f"再生 PU 泡沫发布物不可确定性重建: {path.name}")
        hashes[key] = _sha256(path)
    if manifest != _manifest(frames, hashes):
        raise SystemExit("再生 PU 泡沫发布清单不一致")
    print("再生 PU 泡沫检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frames = build_release()
    if args.检查:
        check_release(frames)
    else:
        write_release(frames)
        print(
            json.dumps(
                {"outputs": {key: len(frame) for key, frame in frames.items()}},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
