"""登记 Mendeley TPU-95A TPMS 的 FEA 输入模型，不伪造仿真输出。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Mendeley_TPU95A_TPMS应变率力学"
)
ZIP = SOURCE_DIR / "mc6zh4cwhf-2.zip"
FEA_CATALOG = SOURCE_DIR / "官方FEA文件清单_仅登记未下载.json"
OUTPUT_DIR = ROOT / "结果" / "定向筛选"
OUT = OUTPUT_DIR / "TPMS_FEA输入清单.csv"
MANIFEST = OUTPUT_DIR / "TPMS_FEA输入发布清单.json"

DATASET_DOI = "10.17632/mc6zh4cwhf.2"
LICENSE = "CC-BY-4.0"
SOURCE_ID = "source_mendeley_mc6zh4cwhf_v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_catalog() -> list[dict[str, object]]:
    if not ZIP.exists() or not FEA_CATALOG.exists():
        raise FileNotFoundError("TPMS FEA归档或官方文件清单缺失")
    records = json.loads(FEA_CATALOG.read_text(encoding="utf-8"))
    if len(records) != 9:
        raise ValueError(f"官方FEA输入文件数量异常: {len(records)}")
    return records


def _parse_filename(filename: str) -> tuple[str, float]:
    match = re.fullmatch(r"(P|D|IWP)0_(001|01|1)\.inp", filename)
    if not match:
        raise ValueError(f"未识别的TPMS FEA文件名: {filename}")
    topology, rate = match.groups()
    return topology, {"001": 0.001, "01": 0.01, "1": 0.1}[rate]


def build_release() -> pd.DataFrame:
    rows = []
    with zipfile.ZipFile(ZIP) as archive:
        members = archive.namelist()
        for record in sorted(_load_catalog(), key=lambda item: item["filename"]):
            filename = str(record["filename"])
            topology, rate = _parse_filename(filename)
            details = record["content_details"]
            member = next(
                (
                    name
                    for name in members
                    if name.endswith(f"/FEA models/{filename}")
                ),
                None,
            )
            if member is None:
                raise ValueError(f"归档中缺少FEA输入: {filename}")
            raw = archive.read(member)
            actual_sha = hashlib.sha256(raw).hexdigest()
            if actual_sha != details["sha256_hash"] or len(raw) != int(record["size"]):
                raise ValueError(f"FEA输入SHA或大小不一致: {filename}")
            rows.append(
                {
                "source_id": SOURCE_ID,
                "dataset_doi": DATASET_DOI,
                "material_grade": "eSUN eTPU-95A",
                "material_family": "commercial_thermoplastic_polyurethane_95A",
                "topology": topology,
                "strain_rate_s-1": rate,
                "load_direction": "[001]",
                "solver": "Abaqus/CAE 2022 input deck",
                "input_filename": filename,
                "source_file_uuid": record["id"],
                "source_member_sha256": details["sha256_hash"],
                "source_member_bytes": int(record["size"]),
                "input_only": True,
                "simulation_output_available": False,
                "reported_response_count": 0,
                "target_role": "TPMS_topology_and_strain_rate_simulation_input_reference",
                "model_admission_layer": "simulation_input_reference",
                "chemistry_mapping_status": "commercial_grade_identity_only",
                "usage_mode": "future_FEA_reproduction_or_feature_extraction_not_performance_label",
                "split_group": f"{DATASET_DOI}|eSUN eTPU-95A|{topology}",
                "license": LICENSE,
                "citation_keys": "reference-192;reference-80",
                }
            )
    return pd.DataFrame(rows)


def _manifest(frame: pd.DataFrame, output_hash: str) -> dict[str, object]:
    catalog = _load_catalog()
    return {
        "release_id": "mendeley_tpms_fea_input_catalog_v1",
        "source": {
            "source_id": SOURCE_ID,
            "dataset_doi": DATASET_DOI,
            "article_doi": "10.1080/17452759.2026.2662048",
            "license": LICENSE,
            "archive_bytes": ZIP.stat().st_size,
            "archive_sha256": _sha256(ZIP),
            "official_fea_catalog_sha256": _sha256(FEA_CATALOG),
            "official_fea_file_count": len(catalog),
        },
        "counts": {
            "input_file_count": len(frame),
            "topology_count": int(frame["topology"].nunique()),
            "strain_rate_count": int(frame["strain_rate_s-1"].nunique()),
            "simulation_output_available_count": int(
                frame["simulation_output_available"].sum()
            ),
            "reported_response_count": int(frame["reported_response_count"].sum()),
            "published_compact_row_count": len(frame),
        },
        "policy": {
            "input_decks_republished": False,
            "simulation_outputs_present": False,
            "input_count_is_material_count": False,
            "simulation_input_is_performance_label": False,
            "experimental_curves_from_same_dataset": "historical_mirror_already_materialized_elsewhere",
            "future_use": "run_or_parse_FEA_only_after_user_releases_computation_gate",
        },
        "output": {
            "path": OUT.relative_to(ROOT).as_posix(),
            "sha256": output_hash,
        },
    }


def write_release(frame: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MANIFEST.write_text(
        json.dumps(_manifest(frame, _sha256(OUT)), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def check_release(frame: pd.DataFrame) -> None:
    if not OUT.exists() or not MANIFEST.exists():
        raise SystemExit("TPMS FEA输入发布物尚未生成")
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / OUT.name
        frame.to_csv(candidate, index=False, encoding="utf-8-sig", lineterminator="\n")
        if _sha256(candidate) != _sha256(OUT):
            raise SystemExit("TPMS FEA输入清单无法确定性重建")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        frame, _sha256(OUT)
    ):
        raise SystemExit("TPMS FEA输入发布清单不一致")
    print("TPMS FEA输入检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    frame = build_release()
    if args.检查:
        check_release(frame)
    else:
        write_release(frame)
        print(json.dumps({"rows": len(frame)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
