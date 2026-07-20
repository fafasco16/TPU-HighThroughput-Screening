from __future__ import annotations

import argparse
import csv
import os
import stat
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors


ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = ROOT / "数据" / "规范" / "chemical_candidate.parquet"
OUTPUT_PATH = ROOT / "数据" / "临时" / "审计" / "SMiPoly_候选.csv"
RULE_VERSION = "smipoly-rdkit-role-v1"

CANDIDATE_COLUMNS = [
    "candidate_id",
    "source_id",
    "source_record_id",
    "source_locator",
    "preferred_name",
    "raw_smiles",
    "canonical_smiles",
    "inchikey",
    "molecular_formula_reported",
    "molecular_formula_calculated",
    "molecular_weight_reported_g_mol",
    "molecular_weight_calculated_g_mol",
    "exact_mass_g_mol",
    "formal_charge",
    "heavy_atom_count",
    "isocyanate_group_count",
    "hydroxyl_group_count",
    "amine_group_count",
    "thiol_group_count",
    "carboxylic_acid_group_count",
    "cyclic_carbonate_group_count",
    "epoxide_group_count",
    "tpu_role",
    "role_confidence",
    "role_basis",
    "screening_scope",
    "screening_priority",
    "functional_group_match",
    "structure_status",
    "duplicate_status",
    "license_spdx",
    "data_origin",
    "fidelity_level",
    "gold_layer",
    "gold_admission_status",
    "direct_property_supervision_weight_ceiling",
    "prediction_uncertainty",
    "generation_rule_version",
    "rdkit_version",
]


SMARTS_TEXT = {
    "isocyanate": "[N]=[C]=[O]",
    # 羧酸、磺酸和磷酸的酸性 OH 不计作多元醇羟基。
    "hydroxyl": "[OX2H;!$([O][C,S,P]=O)]",
    # 排除酰胺、磺酰胺和磷酰胺氮；保留脂肪胺和芳香胺。
    "amine": "[N;H2,H1;+0;!$(N-C=O);!$(N-S=O);!$(N-P=O)]",
    "thiol": "[SX2H;+0]",
    "carboxylic_acid": "[CX3](=O)[OX2H1]",
    "cyclic_carbonate": "[O;R][C;R](=[O])[O;R]",
    "epoxide": "[O;r3]1[C;r3][C;r3]1",
}
SMARTS = {name: Chem.MolFromSmarts(text) for name, text in SMARTS_TEXT.items()}
if any(pattern is None for pattern in SMARTS.values()):  # pragma: no cover
    raise RuntimeError("候选分类 SMARTS 编译失败")


def _clean(value: Any) -> Any:
    if value is None or pd.isna(value):
        return ""
    return value


def _group_counts(mol: Chem.Mol) -> dict[str, int]:
    return {
        name: len(mol.GetSubstructMatches(pattern, uniquify=True))
        for name, pattern in SMARTS.items()
    }


def _classify_role(
    groups: dict[str, int], molecular_weight: float
) -> tuple[str, str, str, int, str]:
    """Return role, confidence, scope, priority and an auditable rule label.

    这是候选角色建议，不是商业可得性、反应收率或实验可合成性的证明。
    规则按直接 TPU/TPUU 组分、邻域前驱体、单官能模型物和未分类依次判定。
    """

    if groups["isocyanate"] >= 2:
        return (
            "di_polyisocyanate_candidate",
            "rule_high",
            "direct_tpu_building_block",
            1,
            "isocyanate_group_count>=2",
        )
    if groups["hydroxyl"] >= 3:
        return (
            "polyol_crosslinker_candidate",
            "rule_high",
            "direct_tpu_building_block",
            1,
            "hydroxyl_group_count>=3",
        )
    if groups["hydroxyl"] == 2:
        if molecular_weight <= 250:
            return (
                "diol_chain_extender_candidate",
                "rule_medium",
                "direct_tpu_building_block",
                1,
                "hydroxyl_group_count=2 and RDKit_MolWt<=250",
            )
        return (
            "macrodiol_polyol_candidate",
            "rule_medium",
            "direct_tpu_building_block",
            1,
            "hydroxyl_group_count=2 and RDKit_MolWt>250",
        )
    if groups["amine"] >= 2:
        return (
            "diamine_chain_extender_candidate",
            "rule_medium",
            "tpuu_or_nipu_building_block",
            1,
            "amine_group_count>=2",
        )
    if groups["cyclic_carbonate"] >= 1:
        return (
            "cyclic_carbonate_nipu_precursor",
            "rule_medium",
            "tpuu_or_nipu_building_block",
            2,
            "cyclic_carbonate_group_count>=1",
        )
    if groups["carboxylic_acid"] >= 2:
        return (
            "polyester_polyol_precursor",
            "rule_medium",
            "polyol_synthesis_precursor",
            2,
            "carboxylic_acid_group_count>=2",
        )
    if groups["epoxide"] >= 1:
        return (
            "epoxy_polyol_precursor",
            "rule_medium",
            "polyol_synthesis_precursor",
            2,
            "epoxide_group_count>=1",
        )
    if groups["thiol"] >= 2:
        return (
            "polythiol_adjacent_candidate",
            "rule_medium",
            "adjacent_reactive_building_block",
            3,
            "thiol_group_count>=2",
        )
    if groups["isocyanate"] == 1:
        return (
            "monoisocyanate_model_compound",
            "rule_high",
            "adjacent_model_compound",
            3,
            "isocyanate_group_count=1",
        )
    if groups["hydroxyl"] == 1:
        return (
            "monool_model_compound",
            "rule_medium",
            "adjacent_model_compound",
            3,
            "hydroxyl_group_count=1",
        )
    if groups["amine"] == 1:
        return (
            "monoamine_model_compound",
            "rule_medium",
            "adjacent_model_compound",
            3,
            "amine_group_count=1",
        )
    return (
        "unclassified",
        "none",
        "unresolved",
        9,
        "no_supported_TPU_role_rule_matched",
    )


def build_candidate_rows(input_path: Path = INPUT_PATH) -> list[dict[str, Any]]:
    frame = pd.read_parquet(input_path)
    required = {
        "chemical_id",
        "source_id",
        "source_record_id",
        "source_locator",
        "preferred_name",
        "raw_smiles",
        "molecular_formula_raw",
        "molecular_weight_raw",
        "license_spdx",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"SMiPoly 规范表缺字段: {missing}")

    rows: list[dict[str, Any]] = []
    canonical_seen: set[str] = set()
    inchikey_seen: set[str] = set()
    candidate_seen: set[str] = set()
    for source in frame.sort_values("chemical_id", kind="mergesort").to_dict(
        orient="records"
    ):
        candidate_id = str(source["chemical_id"]).strip()
        raw_smiles = str(source["raw_smiles"]).strip()
        mol = Chem.MolFromSmiles(raw_smiles)
        if mol is None:
            raise ValueError(
                f"SMiPoly SMILES 无法解析: {candidate_id} / {raw_smiles}"
            )
        canonical_smiles = Chem.MolToSmiles(
            mol, canonical=True, isomericSmiles=True
        )
        inchikey = Chem.MolToInchiKey(mol)
        if not inchikey:
            raise ValueError(f"SMiPoly InChIKey 生成失败: {candidate_id}")
        if candidate_id in candidate_seen:
            raise ValueError(f"SMiPoly candidate_id 重复: {candidate_id}")
        if canonical_smiles in canonical_seen:
            raise ValueError(f"SMiPoly 规范 SMILES 重复: {canonical_smiles}")
        if inchikey in inchikey_seen:
            raise ValueError(f"SMiPoly InChIKey 重复: {inchikey}")
        candidate_seen.add(candidate_id)
        canonical_seen.add(canonical_smiles)
        inchikey_seen.add(inchikey)

        groups = _group_counts(mol)
        molecular_weight = float(Descriptors.MolWt(mol))
        role, confidence, scope, priority, basis = _classify_role(
            groups, molecular_weight
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "source_id": str(source["source_id"]),
                "source_record_id": str(source["source_record_id"]),
                "source_locator": str(source["source_locator"]),
                "preferred_name": _clean(source["preferred_name"]),
                "raw_smiles": raw_smiles,
                "canonical_smiles": canonical_smiles,
                "inchikey": inchikey,
                "molecular_formula_reported": _clean(
                    source["molecular_formula_raw"]
                ),
                "molecular_formula_calculated": rdMolDescriptors.CalcMolFormula(
                    mol
                ),
                "molecular_weight_reported_g_mol": _clean(
                    source["molecular_weight_raw"]
                ),
                "molecular_weight_calculated_g_mol": round(molecular_weight, 6),
                "exact_mass_g_mol": round(float(Descriptors.ExactMolWt(mol)), 6),
                "formal_charge": int(Chem.GetFormalCharge(mol)),
                "heavy_atom_count": int(mol.GetNumHeavyAtoms()),
                "isocyanate_group_count": groups["isocyanate"],
                "hydroxyl_group_count": groups["hydroxyl"],
                "amine_group_count": groups["amine"],
                "thiol_group_count": groups["thiol"],
                "carboxylic_acid_group_count": groups["carboxylic_acid"],
                "cyclic_carbonate_group_count": groups["cyclic_carbonate"],
                "epoxide_group_count": groups["epoxide"],
                "tpu_role": role,
                "role_confidence": confidence,
                "role_basis": basis,
                "screening_scope": scope,
                "screening_priority": priority,
                "functional_group_match": role != "unclassified",
                "structure_status": "rdkit_validated",
                "duplicate_status": "canonical_unique",
                "license_spdx": str(source["license_spdx"]),
                "data_origin": "reaction_rule_generated",
                "fidelity_level": "candidate_structure",
                "gold_layer": "Gold-V",
                "gold_admission_status": "admitted_reference",
                "direct_property_supervision_weight_ceiling": 0.0,
                "prediction_uncertainty": "",
                "generation_rule_version": RULE_VERSION,
                "rdkit_version": rdBase.rdkitVersion,
            }
        )

    if len(rows) != 1071:
        raise ValueError(f"SMiPoly 候选数漂移: expected=1071, actual={len(rows)}")
    return rows


def summarize_candidates(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    roles = Counter(str(row["tpu_role"]) for row in materialized)
    scopes = Counter(str(row["screening_scope"]) for row in materialized)
    return {
        "candidate_count": len(materialized),
        "role_counts": dict(sorted(roles.items())),
        "screening_scope_counts": dict(sorted(scopes.items())),
        "direct_building_block_count": sum(
            int(row["screening_priority"]) == 1 for row in materialized
        ),
        "functional_group_matched_count": sum(
            bool(row["functional_group_match"]) for row in materialized
        ),
        "unclassified_count": roles.get("unclassified", 0),
    }


def _assert_safe_output(path: Path) -> None:
    root = ROOT.resolve(strict=True)
    target = path.resolve(strict=False)
    target.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and (
        target.is_symlink() or not stat.S_ISREG(target.lstat().st_mode)
    ):
        raise ValueError(f"拒绝覆盖非普通文件: {target}")


def write_candidate_csv(
    rows: list[dict[str, Any]], output_path: Path = OUTPUT_PATH
) -> None:
    _assert_safe_output(output_path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            descriptor = -1
            writer = csv.DictWriter(
                handle,
                fieldnames=CANDIDATE_COLUMNS,
                extrasaction="raise",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="审计SMiPoly来源候选；不会覆盖综合Gold_候选.csv"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help="来源级候选CSV输出；必须位于项目目录内",
    )
    args = parser.parse_args(argv)
    rows = build_candidate_rows()
    write_candidate_csv(rows, args.output)
    print(summarize_candidates(rows))


if __name__ == "__main__":
    main()
