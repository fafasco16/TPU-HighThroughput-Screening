"""审计 PUR-GEN 公开下载包中的聚氨酯虚拟片段。

数据集可以公开下载，但 Zenodo 与 DataCite 元数据均未声明许可证，关联
GitHub 仓库也没有 LICENSE。因此本脚本把结构科学准入和训练/再分发权利
拆成两道门：结构进入零监督权重 Gold-V 参考，原始文件权利仍待复核；不会
把“开放访问”误写成“允许再分发”。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors

try:
    from .SMiPoly_TPU候选分类 import CANDIDATE_COLUMNS, _group_counts
except ImportError:  # 直接执行本文件时，脚本目录位于 sys.path。
    from SMiPoly_TPU候选分类 import CANDIDATE_COLUMNS, _group_counts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    PROJECT_ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第七批虚拟_PUR-GEN片段库"
)
ARCHIVE = SOURCE_DIR / "PUR-GEN片段库.zip"
METADATA = SOURCE_DIR / "官方Zenodo元数据.json"
SUMMARY_PATH = SOURCE_DIR / "内容审计摘要.json"
FRAGMENT_PATH = SOURCE_DIR / "片段审计清单.tsv"
FILE_PATH = SOURCE_DIR / "文件校验清单.tsv"

SOURCE_ID = "source_zenodo_11612378_purgen_fragments"
DATASET_DOI = "10.5281/zenodo.11612378"
ARTICLE_DOI = "10.1016/j.csbj.2024.12.004"
ARCHIVE_SIZE = 752_485
ARCHIVE_SHA256 = "965cf1d04b9b5358bf71beaddbb14ab43346acae8b80eb3baefe7e60cd452e24"
ARCHIVE_MD5 = "140fabd4d284d74fdeee014a5ddc2c84"
RULE_VERSION = "purgen-fragment-audit-v1"

FRAGMENT_COLUMNS = [
    "candidate_id",
    "unit_count",
    "fragment_index",
    "mol2_member",
    "member_sha256",
    "descriptor_row_present",
    "descriptor_smiles",
    "canonical_smiles",
    "inchikey",
    "molecular_formula_calculated",
    "molecular_weight_reported_g_mol",
    "molecular_weight_calculated_g_mol",
    "clogp_reported",
    "molar_refractivity_reported",
    "rotatable_bonds_reported",
    "aromatic_atoms_reported",
    "heavy_atoms_reported",
    "aromatic_proportion_reported",
    "tpsa_reported_angstrom2",
    "descriptor_structure_match",
    "structure_status",
    "rights_status",
    "gold_layer",
    "gold_admission_status",
    "weight_ceiling",
    "source_locator",
    "notes",
]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value: Any) -> float | int | str:
    text = str(value or "").strip()
    if not text:
        return ""
    number = float(text)
    return int(number) if number.is_integer() else number


def _descriptor_rows(zf: zipfile.ZipFile, unit_count: int) -> dict[int, dict[str, str]]:
    member = f"1.PUR-GEN/{unit_count}_units_properties.csv"
    text = zf.read(member).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    mapped: dict[int, dict[str, str]] = {}
    for row in rows:
        # 上游首列标题为空，值为0-based；PU_*.mol2 为1-based。
        fragment_index = int(row[""]) + 1
        if fragment_index in mapped:
            raise ValueError(f"PUR-GEN 描述符索引重复: {unit_count}/{fragment_index}")
        mapped[fragment_index] = row
    return mapped


def _achiral_smiles(mol: Chem.Mol) -> str:
    copy = Chem.Mol(mol)
    Chem.RemoveStereochemistry(copy)
    return Chem.MolToSmiles(copy, canonical=True, isomericSmiles=False)


def build_fragment_rows(
    archive_path: Path = ARCHIVE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if archive_path.stat().st_size != ARCHIVE_SIZE:
        raise ValueError(
            f"PUR-GEN 归档大小漂移: {archive_path.stat().st_size} != {ARCHIVE_SIZE}"
        )
    if _sha256_file(archive_path) != ARCHIVE_SHA256:
        raise ValueError("PUR-GEN 归档 SHA-256 漂移")
    if _md5_file(archive_path) != ARCHIVE_MD5:
        raise ValueError("PUR-GEN 归档官方 MD5 漂移")

    fragments: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    canonical_seen: set[str] = set()
    inchikey_seen: set[str] = set()
    with zipfile.ZipFile(archive_path) as zf:
        descriptors = {unit: _descriptor_rows(zf, unit) for unit in (2, 3, 4)}
        pattern = re.compile(r"^1\.PUR-GEN/([234])_units/PU_(\d+)\.mol2$")
        members = sorted(
            (name for name in zf.namelist() if pattern.fullmatch(name)),
            key=lambda name: (
                int(pattern.fullmatch(name).group(1)),
                int(pattern.fullmatch(name).group(2)),
            ),
        )
        for member in members:
            match = pattern.fullmatch(member)
            assert match
            unit_count = int(match.group(1))
            fragment_index = int(match.group(2))
            candidate_id = f"purgen_u{unit_count}_{fragment_index:03d}"
            payload = zf.read(member)
            mol = Chem.MolFromMol2Block(
                payload.decode("utf-8"), sanitize=True, removeHs=False
            )
            if mol is None:
                raise ValueError(f"PUR-GEN MOL2 无法解析: {member}")
            mol = Chem.RemoveHs(mol)
            canonical = Chem.MolToSmiles(
                mol, canonical=True, isomericSmiles=True
            )
            inchikey = Chem.MolToInchiKey(mol)
            if canonical in canonical_seen:
                raise ValueError(f"PUR-GEN 规范 SMILES 重复: {member}")
            if inchikey in inchikey_seen:
                raise ValueError(f"PUR-GEN InChIKey 重复: {member}")
            canonical_seen.add(canonical)
            inchikey_seen.add(inchikey)

            descriptor = descriptors[unit_count].get(fragment_index)
            descriptor_smiles = str((descriptor or {}).get("SMILES", ""))
            if descriptor_smiles:
                descriptor_mol = Chem.MolFromSmiles(descriptor_smiles)
                if descriptor_mol is None:
                    raise ValueError(
                        f"PUR-GEN 描述符 SMILES 无法解析: {candidate_id}"
                    )
                descriptor_match = (
                    "exact"
                    if Chem.MolToSmiles(
                        descriptor_mol, canonical=True, isomericSmiles=True
                    )
                    == canonical
                    else "achiral_match_mol2_has_inferred_stereochemistry"
                )
                if _achiral_smiles(descriptor_mol) != _achiral_smiles(mol):
                    raise ValueError(
                        f"PUR-GEN 描述符与 MOL2 连接关系不一致: {candidate_id}"
                    )
            else:
                descriptor_match = "descriptor_row_missing"

            molecular_weight = float(Descriptors.MolWt(mol))
            groups = _group_counts(mol)
            locator = f"doi:{DATASET_DOI}#{member}"
            fragments.append(
                {
                    "candidate_id": candidate_id,
                    "unit_count": unit_count,
                    "fragment_index": fragment_index,
                    "mol2_member": member,
                    "member_sha256": _sha256_bytes(payload),
                    "descriptor_row_present": descriptor is not None,
                    "descriptor_smiles": descriptor_smiles,
                    "canonical_smiles": canonical,
                    "inchikey": inchikey,
                    "molecular_formula_calculated": rdMolDescriptors.CalcMolFormula(
                        mol
                    ),
                    "molecular_weight_reported_g_mol": _number(
                        (descriptor or {}).get("MW")
                    ),
                    "molecular_weight_calculated_g_mol": round(
                        molecular_weight, 6
                    ),
                    "clogp_reported": _number((descriptor or {}).get("clogP")),
                    "molar_refractivity_reported": _number(
                        (descriptor or {}).get("MR")
                    ),
                    "rotatable_bonds_reported": _number(
                        (descriptor or {}).get("RotBonds")
                    ),
                    "aromatic_atoms_reported": _number(
                        (descriptor or {}).get("AromaticAtoms")
                    ),
                    "heavy_atoms_reported": _number(
                        (descriptor or {}).get("HeavyAtoms")
                    ),
                    "aromatic_proportion_reported": _number(
                        (descriptor or {}).get("AromaticProportion")
                    ),
                    "tpsa_reported_angstrom2": _number(
                        (descriptor or {}).get("TPSA")
                    ),
                    "descriptor_structure_match": descriptor_match,
                    "structure_status": "rdkit_validated",
                    "rights_status": "open_access_license_unspecified",
                    "gold_layer": "Gold-V",
                    "gold_admission_status": "admitted_reference",
                    "weight_ceiling": 0.0,
                    "source_locator": locator,
                    "notes": "聚氨酯片段，不是完整TPU配方或性能标签",
                }
            )
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "source_id": SOURCE_ID,
                    "source_record_id": f"{unit_count}_units/PU_{fragment_index}",
                    "source_locator": locator,
                    "preferred_name": f"PUR-GEN {unit_count}-unit fragment {fragment_index}",
                    "raw_smiles": descriptor_smiles or canonical,
                    "canonical_smiles": canonical,
                    "inchikey": inchikey,
                    "molecular_formula_reported": "",
                    "molecular_formula_calculated": rdMolDescriptors.CalcMolFormula(
                        mol
                    ),
                    "molecular_weight_reported_g_mol": _number(
                        (descriptor or {}).get("MW")
                    ),
                    "molecular_weight_calculated_g_mol": round(
                        molecular_weight, 6
                    ),
                    "exact_mass_g_mol": round(
                        float(Descriptors.ExactMolWt(mol)), 6
                    ),
                    "formal_charge": int(Chem.GetFormalCharge(mol)),
                    "heavy_atom_count": int(mol.GetNumHeavyAtoms()),
                    "isocyanate_group_count": groups["isocyanate"],
                    "hydroxyl_group_count": groups["hydroxyl"],
                    "amine_group_count": groups["amine"],
                    "thiol_group_count": groups["thiol"],
                    "carboxylic_acid_group_count": groups["carboxylic_acid"],
                    "cyclic_carbonate_group_count": groups["cyclic_carbonate"],
                    "epoxide_group_count": groups["epoxide"],
                    "tpu_role": "generated_polyurethane_fragment",
                    "role_confidence": "source_defined_high",
                    "role_basis": f"PUR-GEN source fragment with {unit_count} units",
                    "screening_scope": "virtual_polyurethane_fragment",
                    "screening_priority": 2,
                    "functional_group_match": True,
                    "structure_status": "rdkit_validated",
                    "duplicate_status": "canonical_unique",
                    "license_spdx": "",
                    "data_origin": "reaction_rule_generated",
                    "fidelity_level": "candidate_fragment_3d",
                    "gold_layer": "Gold-V",
                    "gold_admission_status": "admitted_reference",
                    "direct_property_supervision_weight_ceiling": 0.0,
                    "prediction_uncertainty": "",
                    "generation_rule_version": RULE_VERSION,
                    "rdkit_version": rdBase.rdkitVersion,
                }
            )

    if len(fragments) != 414:
        raise ValueError(f"PUR-GEN 片段数漂移: {len(fragments)}")
    return fragments, candidates


def summarize_fragments(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unit_counts = Counter(int(row["unit_count"]) for row in rows)
    match_counts = Counter(str(row["descriptor_structure_match"]) for row in rows)
    return {
        "audit_version": RULE_VERSION,
        "source": {
            "title": "PUR-GEN: A Web Server for Automated Generation of Polyurethane Fragment Libraries",
            "dataset_doi": DATASET_DOI,
            "article_doi": ARTICLE_DOI,
            "accessed_at": "2026-07-21",
            "access_right": "open",
            "license_status": "unspecified_in_Zenodo_and_DataCite_metadata",
            "rights_decision": "local conditional reference only; no redistribution or training until rights review",
            "archive_size_bytes": ARCHIVE_SIZE,
            "archive_sha256": ARCHIVE_SHA256,
            "archive_md5_upstream": ARCHIVE_MD5,
        },
        "content": {
            "fragment_count": len(rows),
            "unique_canonical_smiles": len(
                {str(row["canonical_smiles"]) for row in rows}
            ),
            "unique_inchikey": len({str(row["inchikey"]) for row in rows}),
            "unit_counts": {str(key): value for key, value in sorted(unit_counts.items())},
            "descriptor_row_count": sum(
                bool(row["descriptor_row_present"]) for row in rows
            ),
            "missing_descriptor_row_count": sum(
                not bool(row["descriptor_row_present"]) for row in rows
            ),
            "descriptor_structure_match_counts": dict(sorted(match_counts.items())),
        },
        "admission": {
            "recommended_layer": "Gold-V admitted scientific reference",
            "weight_ceiling": 0.0,
            "rights_gate": "training and redistribution remain blocked until license review",
            "blockers": [
                "dataset license absent from Zenodo and DataCite metadata",
                "fragments have no experimental or computed TPU performance labels",
                "54 MOL2 structures have no matching descriptor CSV row",
                "fragment multiplicity is not an independent material count",
            ],
        },
    }


def _atomic_write_text(path: Path, text: str) -> None:
    root = PROJECT_ROOT.resolve(strict=True)
    target = path.resolve(strict=False)
    target.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and (
        target.is_symlink() or not stat.S_ISREG(target.lstat().st_mode)
    ):
        raise ValueError(f"拒绝覆盖非普通文件: {target}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _write_tsv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=columns,
        delimiter="\t",
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    _atomic_write_text(path, "\ufeff" + buffer.getvalue())


def write_outputs(rows: list[dict[str, Any]]) -> None:
    summary = summarize_fragments(rows)
    _atomic_write_text(
        SUMMARY_PATH, json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    _write_tsv(FRAGMENT_PATH, rows, FRAGMENT_COLUMNS)
    _write_tsv(
        FILE_PATH,
        [
            {
                "relative_path": ARCHIVE.name,
                "size_bytes": ARCHIVE.stat().st_size,
                "sha256": _sha256_file(ARCHIVE),
                "upstream_checksum": f"md5:{_md5_file(ARCHIVE)}",
                "decision": "verified_local_only_rights_pending",
            },
            {
                "relative_path": METADATA.name,
                "size_bytes": METADATA.stat().st_size,
                "sha256": _sha256_file(METADATA),
                "upstream_checksum": "",
                "decision": "metadata_evidence",
            },
        ],
        [
            "relative_path",
            "size_bytes",
            "sha256",
            "upstream_checksum",
            "decision",
        ],
    )


def main() -> None:
    rows, _ = build_fragment_rows()
    write_outputs(rows)
    print(json.dumps(summarize_fragments(rows)["content"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
