"""通过官方API下载两个定向TPU开放来源并冻结哈希与引用。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "数据" / "原始" / "外部数据" / "新增开放数据"
OUTPUT = ROOT / "结果" / "定向筛选" / "外部来源候选.csv"
MANIFEST = ROOT / "结果" / "定向筛选" / "外部来源候选发布清单.json"
RELEASE_ID = "tpu-external-targeted-sources-2026-08-30-v1"

SOURCE_SPECS = [
    {
        "source_id": "source_figshare_12936989_v1",
        "directory": "Figshare_碳酸酯TPU强韧自愈",
        "repository": "Figshare",
        "title": "Mechano-responsive hydrogen-bonding array of thermoplastic polyurethane elastomer captures both strength and self-healing",
        "doi": "10.6084/m9.figshare.12936989.v1",
        "canonical_url": "https://figshare.com/articles/dataset/12936989/1",
        "metadata_url": "https://api.figshare.com/v2/articles/12936989",
        "license": "CC-BY-4.0",
        "target_families": ["toughness", "cyclic_recovery"],
        "citation": "Oh, D. Mechano-responsive hydrogen-bonding array of thermoplastic polyurethane elastomer captures both strength and self-healing [Data set]. Figshare, 2021. https://doi.org/10.6084/m9.figshare.12936989.v1.",
        "citation_keys": "reference-180",
        "acquisition_status": "materialized",
        "files": [
            {
                "name": "Source-data_Main Figures.xlsx",
                "url": "https://ndownloader.figshare.com/files/24635276",
                "size": 1_636_536,
            },
            {
                "name": "Source-data_Supplementary Figures.xlsx",
                "url": "https://ndownloader.figshare.com/files/24635282",
                "size": 3_564_847,
            },
        ],
    },
    {
        "source_id": "source_zenodo_1098206_v1",
        "directory": "Zenodo_多孔导电TPU纳米复合膜",
        "repository": "Zenodo",
        "title": "Facile Fabrication of Porous Conductive Thermoplastic Polyurethane Nanocomposite Films via Solution Casting",
        "doi": "10.1038/s41598-017-17647-w",
        "canonical_url": "https://zenodo.org/records/1098206",
        "metadata_url": "https://zenodo.org/api/records/1098206",
        "license": "CC-BY-4.0",
        "target_families": ["toughness"],
        "citation": "Wu, T.; Chen, B. Facile Fabrication of Porous Conductive Thermoplastic Polyurethane Nanocomposite Films via Solution Casting [Data set]. Zenodo, 2017. https://zenodo.org/records/1098206; related article https://doi.org/10.1038/s41598-017-17647-w.",
        "citation_keys": "reference-181",
        "acquisition_status": "materialized",
        "files": [
            {
                "name": "Supronics_Porous-TPU-Nanocomposites Dataset.xlsx",
                "url": "https://zenodo.org/api/records/1098206/files/Supronics_Porous-TPU-Nanocomposites%20Dataset.xlsx/content",
                "size": 750_592,
            }
        ],
    },
    {
        "source_id": "source_zenodo_19609901_v1",
        "directory": "Zenodo_导电自修复可回收PU复合材料",
        "repository": "Zenodo",
        "title": "Printable, Self-Healing and Recyclable PEDOT:PSS/Polyurethane Composites for Durable Bioelectronics",
        "doi": "10.5281/zenodo.19609901",
        "canonical_url": "https://zenodo.org/records/19609901",
        "metadata_url": "https://zenodo.org/api/records/19609901",
        "license": "CC-BY-4.0",
        "target_families": [
            "toughness",
            "cyclic_recovery",
            "environmental_recycling",
        ],
        "citation": "Cicoira, F.; Kim, J. Printable, Self-Healing and Recyclable PEDOT:PSS/Polyurethane Composites for Durable Bioelectronics [Data set]. Zenodo, 2026. https://doi.org/10.5281/zenodo.19609901.",
        "citation_keys": "reference-199;reference-200",
        "acquisition_status": "materialized",
        "files": [
            {
                "name": "Dataset Cicoira Materials Horizons 2026.zip",
                "url": "https://zenodo.org/api/records/19609901/files/Dataset%20Cicoira%20Materials%20Horizons%202026.zip/content",
                "size": 6_763_468,
            }
        ],
    },
    {
        "source_id": "source_zenodo_18149651_v1",
        "directory": "Zenodo_TPU鞋材热稳定与耐磨",
        "repository": "Zenodo",
        "title": "Effects of Gyroid Lattice Relative Density on Wear and Mechanical Performance of 3D Printed TPU and Flexible Nylon for Footwear",
        "doi": "10.5281/zenodo.18149651",
        "canonical_url": "https://zenodo.org/records/18149651",
        "metadata_url": "https://zenodo.org/api/records/18149651",
        "license": "CC-BY-4.0",
        "target_families": ["thermal_stability", "environmental_wear"],
        "citation": "Li, J. Effects of Gyroid Lattice Relative Density on Wear and Mechanical Performance of 3D Printed TPU and Flexible Nylon for Footwear [Data set]. Zenodo, 2026. https://doi.org/10.5281/zenodo.18149651.",
        "citation_keys": "reference-201;reference-202",
        "acquisition_status": "materialized",
        "files": [
            {
                "name": "TPU_orange.xlsx",
                "url": "https://zenodo.org/api/records/18149651/files/TPU_orange.xlsx/content",
                "size": 287_796,
            },
            {
                "name": "TPU_yellow.xlsx",
                "url": "https://zenodo.org/api/records/18149651/files/TPU_yellow.xlsx/content",
                "size": 287_596,
            },
            {
                "name": "TPU 95A White.xlsx",
                "url": "https://zenodo.org/api/records/18149651/files/TPU%2095A%20White.xlsx/content",
                "size": 286_631,
            },
            {
                "name": "DSC数据整理.xlsx",
                "url": "https://zenodo.org/api/records/18149651/files/DSC%E6%95%B0%E6%8D%AE%E6%95%B4%E7%90%86.xlsx/content",
                "size": 791_977,
            },
            {
                "name": "Rheology.xlsx",
                "url": "https://zenodo.org/api/records/18149651/files/Rheology.xlsx/content",
                "size": 136_185,
            },
        ],
    },
]


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return _hash(path, "sha256")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": "TPU-research-data-audit/1.0"})


def _download(url: str, target: Path, expected_size: int) -> None:
    if target.is_file() and target.stat().st_size == expected_size:
        return
    temporary = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(_request(url), timeout=120) as response, temporary.open("wb") as handle:
        while block := response.read(1024 * 1024):
            handle.write(block)
    if temporary.stat().st_size != expected_size:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"下载字节数不符：{target.name}")
    os.replace(temporary, target)


def acquire() -> pd.DataFrame:
    rows = []
    for spec in SOURCE_SPECS:
        directory = RAW_ROOT / spec["directory"]
        directory.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(_request(spec["metadata_url"]), timeout=60) as response:
            metadata = json.load(response)
        (directory / "官方API元数据.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        file_rows = []
        for file_spec in spec["files"]:
            target = directory / file_spec["name"]
            _download(file_spec["url"], target, file_spec["size"])
            file_rows.append(
                {
                    "name": target.name,
                    "bytes": target.stat().st_size,
                    "sha256": _sha256(target),
                    "md5": _hash(target, "md5"),
                    "download_url": file_spec["url"],
                }
            )
        source_manifest = {
            "release_id": RELEASE_ID,
            "source_id": spec["source_id"],
            "title": spec["title"],
            "repository": spec["repository"],
            "doi": spec["doi"],
            "canonical_url": spec["canonical_url"],
            "metadata_url": spec["metadata_url"],
            "license": spec["license"],
            "target_families": spec["target_families"],
            "citation": spec["citation"],
            "citation_keys": spec["citation_keys"],
            "files": file_rows,
        }
        source_manifest_path = directory / "来源清单.json"
        source_manifest_path.write_text(
            json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        rows.append(
            {
                "release_id": RELEASE_ID,
                "source_id": spec["source_id"],
                "title": spec["title"],
                "repository": spec["repository"],
                "doi": spec["doi"],
                "canonical_url": spec["canonical_url"],
                "license": spec["license"],
                "target_families": ";".join(spec["target_families"]),
                "raw_file_count": len(file_rows),
                "raw_total_bytes": sum(item["bytes"] for item in file_rows),
                "local_directory": str(directory.relative_to(ROOT)).replace("\\", "/"),
                "local_source_manifest_sha256": _sha256(source_manifest_path),
                "acquisition_status": spec["acquisition_status"],
                "citation": spec["citation"],
                "citation_keys": spec["citation_keys"],
            }
        )
    return pd.DataFrame(rows).sort_values("source_id").reset_index(drop=True)


def _write_release(frame: pd.DataFrame) -> None:
    frame.to_csv(OUTPUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    manifest = {
        "release_id": RELEASE_ID,
        "counts": {
            "source_count": len(frame),
            "downloaded_file_count": int(frame["raw_file_count"].sum()),
        },
        "output_file": {
            "path": str(OUTPUT.relative_to(ROOT)).replace("\\", "/"),
            "bytes": OUTPUT.stat().st_size,
            "sha256": _sha256(OUTPUT),
        },
        "sources": frame[
            ["source_id", "local_directory", "local_source_manifest_sha256"]
        ].to_dict(orient="records"),
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def check() -> None:
    if not OUTPUT.is_file() or not MANIFEST.is_file():
        raise SystemExit("缺少外部来源候选发布；请先运行下载模式")
    frame = pd.read_csv(OUTPUT)
    for row in frame.itertuples(index=False):
        directory = ROOT / row.local_directory
        source_manifest_path = directory / "来源清单.json"
        if _sha256(source_manifest_path) != row.local_source_manifest_sha256:
            raise SystemExit(f"来源清单哈希不一致：{row.source_id}")
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        for file_row in source_manifest["files"]:
            path = directory / file_row["name"]
            if path.stat().st_size != file_row["bytes"] or _sha256(path) != file_row["sha256"]:
                raise SystemExit(f"外部原始文件校验失败：{path.name}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["counts"] != {
        "source_count": len(frame),
        "downloaded_file_count": int(frame["raw_file_count"].sum()),
    }:
        raise SystemExit("外部来源候选数量不一致")
    print("外部定向来源检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    if args.检查:
        check()
    else:
        frame = acquire()
        _write_release(frame)
        print(frame[["source_id", "raw_file_count", "raw_total_bytes"]].to_string(index=False))


if __name__ == "__main__":
    main()
