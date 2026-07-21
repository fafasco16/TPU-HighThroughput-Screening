"""只读审计 Open Polymer Challenge 官方赛后测试集。

本脚本不联网、不解包、不写文件、不创建训练拆分或训练权重。它只核验已冻结的
Kaggle 原件、论文证据和两个已解包 CSV，并将确定性审计结果打印到标准输出。

运行：

    python 代码/审计/第十批OpenPolymerChallenge.py
    python 代码/审计/第十批OpenPolymerChallenge.py --data-dir <迁移后的目录>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

import pandas as pd
from rdkit import Chem


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = (
    PROJECT_ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十批计算_OpenPolymerChallenge"
)
AUDIT_DATE = "2026-07-21"
AUDIT_VERSION = "1.0"

DATASET_REF = "alexliu99/neurips-open-polymer-prediction-2025-test-data"
DATASET_ID = 8_954_694
DATASET_VERSION = 1
DATASET_PAGE = f"https://www.kaggle.com/datasets/{DATASET_REF}"
METADATA_URL = f"https://www.kaggle.com/api/v1/datasets/view/{DATASET_REF}"
DOWNLOAD_URL = (
    f"https://www.kaggle.com/api/v1/datasets/download/{DATASET_REF}"
    "?datasetVersionNumber=1"
)
PAPER_IDENTIFIER = "arXiv:2512.08896"
PAPER_URL = "https://arxiv.org/abs/2512.08896"
LICENSE_NAME = "MIT"

FROZEN_FILES: dict[str, tuple[int, str, str]] = {
    "Kaggle官方API元数据.json": (
        3_458,
        "304aa35195899cf63dcae6ec7dc9734fb19be673f9225bdd37116cec9352eb31",
        "official_dataset_metadata",
    ),
    "下载响应头.txt": (
        1_244,
        "961cc772838f387e3a01d6e159f53d497a1eec28ac43d1ae5818e7fcaa2a52b6",
        "download_response_evidence",
    ),
    "OpenPolymerChallenge_官方赛后测试集_v1.zip": (
        66_197,
        "0f048769eb8330ab4ae36784a3f1329e4677768805f9f4531b58d1fa6ec95336",
        "official_dataset_archive",
    ),
    "arXiv官方元数据.xml": (
        3_833,
        "23aefe2f4d4bf8333690b6b528705d82c9791f398398522d812cdab0290a76e9",
        "publication_metadata",
    ),
    "论文_OpenPolymerChallenge_arXiv2512.08896.pdf": (
        2_302_430,
        "3600267c6eb34a8cc855751d0448bfffd4859b7aea2840e565215e74696fcdf0",
        "publication_pdf",
    ),
    "论文HTML_OpenPolymerChallenge_arXiv2512.08896.html": (
        117_238,
        "710aae63aab187b702997824550b5454f2e23f54bf94dd20d46e4fb3605d3929",
        "publication_method_and_unit_evidence",
    ),
    "解包/public.csv": (
        19_513,
        "0b5b59f9464fb30da82252cc9e4f56552ff641284c627f11aa93fa4d7031c514",
        "official_public_leaderboard_csv",
    ),
    "解包/private.csv": (
        208_864,
        "55cac160c1c139240968a96ba070b583e93902dd1901be2e54d722c4d590a952",
        "official_private_leaderboard_csv",
    ),
}

ARCHIVE_NAME = "OpenPolymerChallenge_官方赛后测试集_v1.zip"
ARCHIVE_MEMBERS: dict[str, tuple[int, str]] = {
    "public.csv": (
        19_513,
        "0b5b59f9464fb30da82252cc9e4f56552ff641284c627f11aa93fa4d7031c514",
    ),
    "private.csv": (
        208_864,
        "55cac160c1c139240968a96ba070b583e93902dd1901be2e54d722c4d590a952",
    ),
}

EXPECTED_COLUMNS = ("SMILES", "Tg", "FFV", "Tc", "Density", "Rg")
PROPERTY_SPECS: dict[str, dict[str, str]] = {
    "Tg": {
        "unit": "degC",
        "simulation_group": "md_group_tg_ffv",
        "method": "density_temperature_cooling_MD_and_fit",
    },
    "FFV": {
        "unit": "dimensionless",
        "simulation_group": "md_group_tg_ffv",
        "method": "NPT_MD_and_PoreBlazer_4.0_geometric_FFV",
    },
    "Tc": {
        "unit": "W/(m*K)",
        "simulation_group": "md_group_tc_density_rg",
        "method": "non_equilibrium_MD_Fourier_law",
    },
    "Density": {
        "unit": "g/cm^3",
        "simulation_group": "md_group_tc_density_rg",
        "method": "300_K_1_atm_NPT_MD_time_average",
    },
    "Rg": {
        "unit": "angstrom",
        "simulation_group": "md_group_tc_density_rg",
        "method": "300_K_1_atm_NPT_MD_chain_time_average",
    },
}
CARBAMATE_SMARTS = "[NX3][CX3](=[OX1])[OX2]"


class AuditBlocked(RuntimeError):
    """输入原件、证据或数据结构偏离冻结状态。"""


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(flag and attributes & flag) or bool(is_junction())


def _require_plain_directory(path: Path) -> None:
    if not path.is_dir() or _is_reparse_point(path):
        raise AuditBlocked(f"数据目录不是普通目录：{path}")


def _require_plain_file(path: Path, data_dir: Path) -> None:
    if not path.is_file() or _is_reparse_point(path):
        raise AuditBlocked(f"输入不是普通文件：{path}")
    try:
        path.resolve(strict=True).relative_to(data_dir.resolve(strict=True))
    except ValueError as exc:
        raise AuditBlocked(f"输入路径逃逸数据目录：{path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_frozen_files(data_dir: Path) -> list[dict[str, Any]]:
    _require_plain_directory(data_dir)
    rows: list[dict[str, Any]] = []
    for relative_name, (expected_size, expected_hash, role) in FROZEN_FILES.items():
        path = data_dir / Path(relative_name)
        _require_plain_file(path, data_dir)
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
        if actual_size != expected_size:
            raise AuditBlocked(
                f"输入字节数漂移：{relative_name}，期望{expected_size}，实际{actual_size}"
            )
        if actual_hash != expected_hash:
            raise AuditBlocked(
                f"输入SHA-256漂移：{relative_name}，期望{expected_hash}，实际{actual_hash}"
            )
        rows.append(
            {
                "path": relative_name,
                "role": role,
                "bytes": expected_size,
                "sha256": expected_hash,
                "integrity": "pass",
            }
        )
    return rows


def verify_archive(data_dir: Path) -> list[dict[str, Any]]:
    archive_path = data_dir / ARCHIVE_NAME
    _require_plain_file(archive_path, data_dir)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if archive.testzip() is not None:
                raise AuditBlocked("ZIP CRC完整性失败")
            if {item.filename for item in infos} != set(ARCHIVE_MEMBERS):
                raise AuditBlocked(
                    f"ZIP成员集合漂移：{sorted(item.filename for item in infos)}"
                )
            if len(infos) != len(ARCHIVE_MEMBERS):
                raise AuditBlocked("ZIP含重复成员名")
            output: list[dict[str, Any]] = []
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                pure = PurePosixPath(normalized)
                mode = info.external_attr >> 16
                if (
                    not normalized
                    or normalized.startswith("/")
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or ":" in normalized
                    or info.is_dir()
                    or info.flag_bits & 0x1
                    or (mode and stat.S_ISLNK(mode))
                ):
                    raise AuditBlocked(f"ZIP成员不安全：{normalized}")
                payload = archive.read(info)
                expected_size, expected_hash = ARCHIVE_MEMBERS[normalized]
                actual_hash = hashlib.sha256(payload).hexdigest()
                if len(payload) != expected_size or actual_hash != expected_hash:
                    raise AuditBlocked(f"ZIP成员内容漂移：{normalized}")
                extracted = data_dir / "解包" / normalized
                _require_plain_file(extracted, data_dir)
                if _sha256(extracted) != expected_hash:
                    raise AuditBlocked(f"解包文件与ZIP成员不一致：{normalized}")
                output.append(
                    {
                        "member": normalized,
                        "bytes": expected_size,
                        "compressed_bytes": info.compress_size,
                        "crc32": f"{info.CRC:08x}",
                        "sha256": expected_hash,
                    }
                )
    except zipfile.BadZipFile as exc:
        raise AuditBlocked("ZIP文件损坏") from exc
    return sorted(output, key=lambda row: row["member"])


def verify_provenance(data_dir: Path) -> dict[str, Any]:
    metadata_path = data_dir / "Kaggle官方API元数据.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked("Kaggle官方API元数据无法解析") from exc
    required = {
        "id": DATASET_ID,
        "ref": DATASET_REF,
        "currentVersionNumber": DATASET_VERSION,
        "licenseName": LICENSE_NAME,
        "totalBytes": 228_377,
        "isPrivate": False,
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise AuditBlocked(f"Kaggle元数据字段漂移：{key}={metadata.get(key)!r}")

    headers = (data_dir / "下载响应头.txt").read_text(encoding="utf-8")
    required_header_fragments = (
        "HTTP/1.1 302 Found",
        "HTTP/1.1 200 OK",
        "Content-Type: application/zip",
        "Content-Length: 66197",
        'ETag: "5506f260b9f46a47f04fae786772f24d"',
        "Last-Modified: Tue, 09 Dec 2025 04:27:35 GMT",
        "x-goog-hash: md5=VQbyYLn0akfwT654Z3LyTQ==",
    )
    for fragment in required_header_fragments:
        if fragment not in headers:
            raise AuditBlocked(f"下载响应证据缺失：{fragment}")

    arxiv_root = ElementTree.parse(data_dir / "arXiv官方元数据.xml").getroot()
    atom = {"atom": "http://www.w3.org/2005/Atom"}
    entry = arxiv_root.find("atom:entry", atom)
    if entry is None:
        raise AuditBlocked("arXiv官方元数据缺少entry")
    arxiv_id = (entry.findtext("atom:id", default="", namespaces=atom)).strip()
    if not re.fullmatch(r"http://arxiv\.org/abs/2512\.08896v\d+", arxiv_id):
        raise AuditBlocked(f"arXiv标识漂移：{arxiv_id}")

    html = (data_dir / "论文HTML_OpenPolymerChallenge_arXiv2512.08896.html").read_text(
        encoding="utf-8"
    )
    evidence_fragments = (
        "Two research groups simulated the polymers in parallel",
        "Molecular dynamics",
        "Density",
        "Radius of Gyration",
        "Thermal Conductivity",
        "Fractional Free Volume",
        "Glass Transition Temperature",
    )
    for fragment in evidence_fragments:
        if fragment not in html:
            raise AuditBlocked(f"论文方法证据缺失：{fragment}")

    pdf = data_dir / "论文_OpenPolymerChallenge_arXiv2512.08896.pdf"
    if pdf.read_bytes()[:5] != b"%PDF-":
        raise AuditBlocked("论文PDF文件头无效")

    members = verify_archive(data_dir)
    return {
        "dataset_id": DATASET_ID,
        "dataset_ref": DATASET_REF,
        "dataset_version": DATASET_VERSION,
        "dataset_page": DATASET_PAGE,
        "metadata_url": METADATA_URL,
        "stable_download_url": DOWNLOAD_URL,
        "dataset_title": metadata["title"],
        "dataset_version_created_utc": metadata["lastUpdated"],
        "retrieved_at_utc": "2026-07-21T00:22:16Z",
        "license": LICENSE_NAME,
        "license_evidence": {
            "path": "Kaggle官方API元数据.json",
            "json_field": "licenseName",
            "license_text_file_in_archive": False,
        },
        "archive": {
            "path": ARCHIVE_NAME,
            "bytes": FROZEN_FILES[ARCHIVE_NAME][0],
            "sha256": FROZEN_FILES[ARCHIVE_NAME][1],
            "etag": "5506f260b9f46a47f04fae786772f24d",
            "upstream_md5_base64": "VQbyYLn0akfwT654Z3LyTQ==",
            "last_modified_utc": "2025-12-09T04:27:35Z",
            "members": members,
        },
        "method_and_unit_evidence": {
            "paper_identifier": PAPER_IDENTIFIER,
            "paper_url": PAPER_URL,
            "local_pdf": "论文_OpenPolymerChallenge_arXiv2512.08896.pdf",
            "local_html": "论文HTML_OpenPolymerChallenge_arXiv2512.08896.html",
        },
        "redistribution_note": (
            "官方Kaggle API将数据集标为MIT；ZIP内未附独立许可证文本，"
            "再分发时须同时保留官方API元数据作为许可依据。"
        ),
    }


def _load_dataset(data_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source_group in ("public", "private"):
        path = data_dir / "解包" / f"{source_group}.csv"
        frame = pd.read_csv(path, dtype={"SMILES": "string"})
        if tuple(frame.columns) != EXPECTED_COLUMNS:
            raise AuditBlocked(
                f"{source_group}.csv字段漂移：{tuple(frame.columns)!r}"
            )
        if frame["SMILES"].isna().any() or (frame["SMILES"].str.len() == 0).any():
            raise AuditBlocked(f"{source_group}.csv含空SMILES")
        for property_name in PROPERTY_SPECS:
            values = frame[property_name].dropna()
            if not values.map(lambda value: math.isfinite(float(value))).all():
                raise AuditBlocked(f"{source_group}.csv的{property_name}含非有限数")
        frame.insert(0, "source_row", range(1, len(frame) + 1))
        frame.insert(0, "source_group", source_group)
        frame.insert(
            0,
            "row_id",
            [f"{source_group}:{index:04d}" for index in range(1, len(frame) + 1)],
        )
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _duplicate_groups(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, group in frame.groupby(column, dropna=False, sort=True):
        if len(group) < 2:
            continue
        source_groups = sorted(group["source_group"].unique().tolist())
        rows.append(
            {
                "key": str(key),
                "row_ids": sorted(group["row_id"].tolist()),
                "source_groups": source_groups,
                "cross_source_group": len(source_groups) > 1,
            }
        )
    return rows


def audit_dataset(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    files = verify_frozen_files(data_dir)
    provenance = verify_provenance(data_dir)
    frame = _load_dataset(data_dir)

    motif = Chem.MolFromSmarts(CARBAMATE_SMARTS)
    if motif is None:
        raise AuditBlocked("氨基甲酸酯SMARTS无法编译")
    canonical_smiles: list[str] = []
    motif_match_counts: list[int] = []
    invalid_rows: list[str] = []
    for row_id, smiles in frame[["row_id", "SMILES"]].itertuples(index=False):
        molecule = Chem.MolFromSmiles(str(smiles))
        if molecule is None:
            invalid_rows.append(row_id)
            canonical_smiles.append("")
            motif_match_counts.append(0)
            continue
        canonical_smiles.append(Chem.MolToSmiles(molecule, canonical=True))
        motif_match_counts.append(len(molecule.GetSubstructMatches(motif)))
    frame["canonical_smiles"] = canonical_smiles
    frame["carbamate_match_count"] = motif_match_counts
    frame["has_carbamate"] = frame["carbamate_match_count"] > 0

    property_names = list(PROPERTY_SPECS)
    observed = frame[property_names].notna()
    frame["observed_label_count"] = observed.sum(axis=1)
    frame["has_any_observed_label"] = frame["observed_label_count"] > 0

    source_groups: dict[str, dict[str, Any]] = {}
    for source_group, group in frame.groupby("source_group", sort=True):
        group_observed = group[property_names].notna()
        source_groups[source_group] = {
            "row_count": len(group),
            "unique_raw_smiles_count": group["SMILES"].nunique(dropna=False),
            "unique_canonical_smiles_count": group["canonical_smiles"].nunique(),
            "rows_with_any_observed_md_label": int(group_observed.any(axis=1).sum()),
            "rows_with_all_labels_missing": int((~group_observed.any(axis=1)).sum()),
            "observed_md_label_cell_count": int(group_observed.sum().sum()),
            "missing_label_cell_count": int((~group_observed).sum().sum()),
            "carbamate_structure_count": int(group["has_carbamate"].sum()),
            "property_observed_counts": {
                name: int(group_observed[name].sum()) for name in property_names
            },
        }

    properties: dict[str, dict[str, Any]] = {}
    for name, specification in PROPERTY_SPECS.items():
        values = frame[name].dropna().astype(float)
        properties[name] = {
            **specification,
            "target_origin": "molecular_dynamics",
            "observed_md_label_count": len(values),
            "missing_not_a_label_count": int(frame[name].isna().sum()),
            "minimum": float(values.min()),
            "median": float(values.median()),
            "maximum": float(values.max()),
            "source_group_observed_counts": {
                group: int(part[name].notna().sum())
                for group, part in frame.groupby("source_group", sort=True)
            },
        }

    exact_duplicate_groups = _duplicate_groups(frame, "SMILES")
    canonical_duplicate_groups = _duplicate_groups(frame, "canonical_smiles")
    carbamate = frame[frame["has_carbamate"]]
    carbamate_observed = carbamate[property_names].notna()
    label_masks = observed.astype(int).astype(str).agg("".join, axis=1)
    label_mask_counts = {
        key: int(value) for key, value in label_masks.value_counts().sort_index().items()
    }

    result = {
        "audit_date": AUDIT_DATE,
        "audit_version": AUDIT_VERSION,
        "dataset_ref": DATASET_REF,
        "dataset_version": DATASET_VERSION,
        "classification": "official_post_competition_MD_property_labels",
        "gold_layer_candidate": "Gold-C",
        "row_count": len(frame),
        "unique_raw_smiles_count": frame["SMILES"].nunique(dropna=False),
        "rdkit_valid_smiles_count": len(frame) - len(invalid_rows),
        "rdkit_invalid_row_ids": invalid_rows,
        "unique_canonical_smiles_count": frame["canonical_smiles"].nunique(),
        "rows_with_any_observed_md_label": int(observed.any(axis=1).sum()),
        "rows_with_all_labels_missing": int((~observed.any(axis=1)).sum()),
        "observed_md_label_cell_count": int(observed.sum().sum()),
        "missing_not_a_label_cell_count": int((~observed).sum().sum()),
        "source_groups": source_groups,
        "properties": properties,
        "label_presence_mask_order": property_names,
        "label_presence_mask_counts": label_mask_counts,
        "simulation_source_groups": {
            "md_group_tc_density_rg": ["Tc", "Density", "Rg"],
            "md_group_tg_ffv": ["Tg", "FFV"],
        },
        "candidate_structure_source_pool": {
            "paper_reported_sources": [
                "100_PI1M_structures_without_competition_overlap",
                "2000_PI1M_generative_model_structures",
                "900_experimentally_validated_unlabeled_polymers_backup_pool",
            ],
            "row_level_mapping_in_csv": False,
            "note": "CSV没有逐行候选结构来源字段，禁止推断单行属于哪一来源。",
        },
        "carbamate_audit": {
            "smarts": CARBAMATE_SMARTS,
            "structure_count": len(carbamate),
            "structure_fraction": len(carbamate) / len(frame),
            "rows_with_any_observed_md_label": int(
                carbamate_observed.any(axis=1).sum()
            ),
            "rows_with_all_labels_missing": int(
                (~carbamate_observed.any(axis=1)).sum()
            ),
            "total_substructure_match_count": int(
                carbamate["carbamate_match_count"].sum()
            ),
            "property_observed_counts": {
                name: int(carbamate_observed[name].sum()) for name in property_names
            },
            "source_group_structure_counts": {
                group: int(part["has_carbamate"].sum())
                for group, part in frame.groupby("source_group", sort=True)
            },
            "source_group_property_observed_counts": {
                group: {
                    name: int(part.loc[part["has_carbamate"], name].notna().sum())
                    for name in property_names
                }
                for group, part in frame.groupby("source_group", sort=True)
            },
        },
        "duplicate_leakage_audit": {
            "scope": "public_vs_private_within_this_release",
            "exact_raw_smiles_duplicate_group_count": len(exact_duplicate_groups),
            "canonical_smiles_duplicate_group_count": len(canonical_duplicate_groups),
            "cross_source_exact_duplicate_group_count": sum(
                row["cross_source_group"] for row in exact_duplicate_groups
            ),
            "cross_source_canonical_duplicate_group_count": sum(
                row["cross_source_group"] for row in canonical_duplicate_groups
            ),
            "exact_groups": exact_duplicate_groups,
            "canonical_groups": canonical_duplicate_groups,
            "training_set_overlap_audited": False,
            "training_set_overlap_note": (
                "本批官方赛后文件不含竞赛训练集，因此不能声称已完成与训练集的重叠审计。"
            ),
        },
        "label_state_rule": {
            "non_missing_numeric_cell": "observed_md_label",
            "empty_csv_cell": "missing_not_a_label",
        },
        "training_split_materialized": False,
        "training_weight_materialized": False,
        "training_weight": "",
        "frozen_files": files,
        "provenance": provenance,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="已冻结的数据目录；默认指向本轮暂存目录。",
    )
    arguments = parser.parse_args()
    result = audit_dataset(arguments.data_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
