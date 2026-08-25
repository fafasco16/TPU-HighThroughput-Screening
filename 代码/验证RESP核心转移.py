"""把四类联合RESP核心映射到12条现实TPU低聚链并严格核对计量。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from rdkit import Chem
from rdkit.Chem import rdPartialCharges


ROOT = Path(__file__).resolve().parents[1]
URETHANE_PATTERN = Chem.MolFromSmarts("OC(=O)N")
ISOCYANATE_PATTERN = Chem.MolFromSmarts("N=C=O")
ROLE_DEFINITIONS = {
    "urethane": [
        "alkoxy_oxygen",
        "carbonyl_carbon",
        "carbonyl_oxygen",
        "urethane_nitrogen",
    ],
    "isocyanate": [
        "isocyanate_nitrogen",
        "isocyanate_carbon",
        "isocyanate_oxygen",
    ],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _atomic_gzip_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    temporary.replace(path)


def classify_n_substituent(
    molecule: Chem.Mol, nitrogen_index: int, core_carbon_index: int
) -> str:
    nitrogen = molecule.GetAtomWithIdx(nitrogen_index)
    external = [
        neighbor
        for neighbor in nitrogen.GetNeighbors()
        if neighbor.GetIdx() != core_carbon_index
        and neighbor.GetAtomicNum() > 1
    ]
    if len(external) != 1:
        raise ValueError(
            f"N外部重原子邻居不是1个: N={nitrogen_index}, count={len(external)}"
        )
    return "aromatic" if external[0].GetIsAromatic() else "aliphatic"


def build_core_parameter_table(joint: pd.DataFrame) -> pd.DataFrame:
    required = {
        "fragment_name",
        "validation_family",
        "smiles",
        "atom_index_zero_based",
        "element",
        "functional_core",
        "joint_stage2_resp_charge_e",
    }
    missing = sorted(required.difference(joint.columns))
    if missing:
        raise ValueError(f"联合RESP逐原子表缺字段: {missing}")
    rows: list[dict[str, Any]] = []
    for (fragment_name, family, smiles), subset in joint.groupby(
        ["fragment_name", "validation_family", "smiles"], sort=True
    ):
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            raise ValueError(f"联合RESP片段SMILES无法解析: {fragment_name}")
        if family.endswith("urethane"):
            matches = molecule.GetSubstructMatches(URETHANE_PATTERN)
            core_type = "urethane"
        elif family.endswith("terminal_isocyanate"):
            matches = molecule.GetSubstructMatches(ISOCYANATE_PATTERN)
            core_type = "isocyanate"
        else:
            raise ValueError(f"联合RESP片段家族未知: {family}")
        if len(matches) != 1:
            raise ValueError(f"联合RESP片段核心匹配不是1个: {fragment_name}")
        match = matches[0]
        roles = ROLE_DEFINITIONS[core_type]
        if len(match) != len(roles):
            raise ValueError(f"联合RESP片段核心角色数不一致: {fragment_name}")
        indexed = subset.set_index("atom_index_zero_based")
        for role, atom_index in zip(roles, match):
            if atom_index not in indexed.index:
                raise ValueError(f"联合RESP核心原子缺电荷: {fragment_name}:{atom_index}")
            source = indexed.loc[atom_index]
            rows.append(
                {
                    "fragment_name": fragment_name,
                    "validation_family": family,
                    "core_type": core_type,
                    "core_role": role,
                    "source_atom_index_zero_based": int(atom_index),
                    "element": source["element"],
                    "joint_resp_charge_e": float(
                        source["joint_stage2_resp_charge_e"]
                    ),
                }
            )
    table = pd.DataFrame(rows).sort_values(
        ["validation_family", "source_atom_index_zero_based"], kind="stable"
    )
    expected = {
        "aliphatic_urethane": 4,
        "aromatic_urethane": 4,
        "aliphatic_terminal_isocyanate": 3,
        "aromatic_terminal_isocyanate": 3,
    }
    actual = table.groupby("validation_family").size().to_dict()
    if actual != expected:
        raise ValueError(f"联合RESP四家族核心参数不闭合: {actual}")
    return table.reset_index(drop=True)


def map_chain(
    formulation_id: str,
    canonical_smiles: str,
    parameters: pd.DataFrame,
    *,
    expected_urethane_count: int,
    expected_nco_count: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    molecule = Chem.MolFromSmiles(canonical_smiles)
    if molecule is None:
        raise ValueError(f"低聚链SMILES无法解析: {formulation_id}")
    molecule_h = Chem.AddHs(Chem.Mol(molecule))
    rdPartialCharges.ComputeGasteigerCharges(molecule_h)
    gasteiger = {
        atom.GetIdx(): float(atom.GetProp("_GasteigerCharge"))
        for atom in molecule_h.GetAtoms()
    }
    rows: list[dict[str, Any]] = []
    mapped_atoms: set[int] = set()
    family_occurrences: dict[str, int] = {}

    def add_occurrence(
        core_type: str,
        match: tuple[int, ...],
        occurrence_index: int,
        environment: str,
    ) -> None:
        family = (
            f"{environment}_urethane"
            if core_type == "urethane"
            else f"{environment}_terminal_isocyanate"
        )
        family_occurrences[family] = family_occurrences.get(family, 0) + 1
        source = parameters.loc[
            parameters["validation_family"].eq(family)
            & parameters["core_type"].eq(core_type)
        ].sort_values("source_atom_index_zero_based", kind="stable")
        roles = ROLE_DEFINITIONS[core_type]
        role_to_charge = source.set_index("core_role")["joint_resp_charge_e"]
        if set(role_to_charge.index) != set(roles):
            raise ValueError(f"RESP核心角色缺失: {family}")
        for atom_index, role in zip(match, roles):
            if atom_index in mapped_atoms:
                raise ValueError(
                    f"RESP核心映射原子重叠: {formulation_id}:{atom_index}"
                )
            mapped_atoms.add(atom_index)
            transfer = float(role_to_charge.loc[role])
            rows.append(
                {
                    "formulation_id": formulation_id,
                    "core_type": core_type,
                    "occurrence_index": occurrence_index,
                    "validation_family": family,
                    "core_role": role,
                    "chain_atom_index_zero_based": atom_index,
                    "element": molecule.GetAtomWithIdx(atom_index).GetSymbol(),
                    "joint_resp_transfer_charge_e": transfer,
                    "whole_chain_gasteiger_charge_e": gasteiger[atom_index],
                    "transfer_minus_gasteiger_e": transfer - gasteiger[atom_index],
                    "transfer_scope": "validated_core_atom_only",
                }
            )

    urethane_matches = molecule.GetSubstructMatches(URETHANE_PATTERN)
    for index, match in enumerate(urethane_matches):
        environment = classify_n_substituent(molecule, match[3], match[1])
        add_occurrence("urethane", match, index, environment)
    nco_matches = molecule.GetSubstructMatches(ISOCYANATE_PATTERN)
    for index, match in enumerate(nco_matches):
        environment = classify_n_substituent(molecule, match[0], match[1])
        add_occurrence("isocyanate", match, index, environment)
    if len(urethane_matches) != expected_urethane_count:
        raise ValueError(
            f"{formulation_id}氨基甲酸酯匹配数不闭合: "
            f"{len(urethane_matches)} != {expected_urethane_count}"
        )
    if len(nco_matches) != expected_nco_count:
        raise ValueError(
            f"{formulation_id}NCO匹配数不闭合: {len(nco_matches)} != {expected_nco_count}"
        )
    mapping = pd.DataFrame(rows)
    heavy_atoms = molecule.GetNumAtoms()
    total_atoms = molecule_h.GetNumAtoms()
    summary = {
        "formulation_id": formulation_id,
        "heavy_atom_count": heavy_atoms,
        "total_atom_count_with_hydrogen": total_atoms,
        "urethane_occurrence_count": len(urethane_matches),
        "terminal_nco_occurrence_count": len(nco_matches),
        "mapped_unique_heavy_atom_count": len(mapped_atoms),
        "mapped_heavy_atom_fraction": len(mapped_atoms) / heavy_atoms,
        "mapped_total_atom_fraction": len(mapped_atoms) / total_atoms,
        "mapped_core_charge_sum_e": float(
            math.fsum(mapping["joint_resp_transfer_charge_e"].tolist())
        ),
        "maximum_absolute_transfer_minus_gasteiger_e": float(
            mapping["transfer_minus_gasteiger_e"].abs().max()
        ),
        "aliphatic_urethane_occurrences": family_occurrences.get(
            "aliphatic_urethane", 0
        ),
        "aromatic_urethane_occurrences": family_occurrences.get(
            "aromatic_urethane", 0
        ),
        "aliphatic_terminal_nco_occurrences": family_occurrences.get(
            "aliphatic_terminal_isocyanate", 0
        ),
        "aromatic_terminal_nco_occurrences": family_occurrences.get(
            "aromatic_terminal_isocyanate", 0
        ),
        "mapping_status": "core_mapping_completed_full_charge_assignment_blocked",
        "production_md_permission": "blocked_unmapped_atoms_and_forcefield_validation",
    }
    return mapping, summary


def write_release(
    graph_path: Path,
    plan_path: Path,
    joint_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (graph_path, plan_path, joint_path):
        if not path.is_file():
            raise ValueError(f"RESP核心转移输入不存在: {path}")
    graphs = pd.read_csv(graph_path)
    plan = pd.read_csv(plan_path)
    joint = pd.read_csv(joint_path)
    if not graphs["formulation_id"].is_unique or not plan["formulation_id"].is_unique:
        raise ValueError("RESP核心转移输入formulation_id必须唯一")
    merged = graphs.merge(
        plan[
            [
                "formulation_id",
                "estimated_urethane_bond_count",
                "residual_nco_end_groups",
            ]
        ],
        on="formulation_id",
        how="inner",
        validate="one_to_one",
    )
    if len(merged) != 12:
        raise ValueError(f"RESP核心转移输入不是12条现实链: {len(merged)}")
    parameters = build_core_parameter_table(joint)
    mapping_frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for source in merged.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        mapping, summary = map_chain(
            str(source["formulation_id"]),
            str(source["canonical_smiles"]),
            parameters,
            expected_urethane_count=int(source["estimated_urethane_bond_count"]),
            expected_nco_count=int(source["residual_nco_end_groups"]),
        )
        mapping_frames.append(mapping)
        summaries.append(summary)
    mappings = pd.concat(mapping_frames, ignore_index=True).sort_values(
        ["formulation_id", "core_type", "occurrence_index", "chain_atom_index_zero_based"],
        kind="stable",
    )
    summary_table = pd.DataFrame(summaries).sort_values(
        "formulation_id", kind="stable"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    parameter_out = output_root / "RESP核心参数.csv"
    mapping_out = output_root / "RESP核心转移映射.csv.gz"
    summary_out = output_root / "RESP核心转移逐配方.csv"
    report_out = output_root / "RESP核心转移说明.md"
    _atomic_text(parameter_out, parameters.to_csv(index=False, float_format="%.12g"))
    _atomic_gzip_text(
        mapping_out, mappings.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(
        summary_out, summary_table.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# RESP核心片段到现实TPU链的转移验证",
                "",
                "12条现实低聚链逐一匹配OC(=O)N氨基甲酸酯核心和残余N=C=O端基，并依据N外部重原子是否芳香自动选择脂肪族或芳香族联合RESP参数。",
                "映射数必须与整数计量计划中的氨基甲酸酯键数和残余NCO数完全相等，且任何链原子不得被两个核心重复覆盖。",
                "",
                "本发布只转移经过四家族三构象联合RESP验证的核心重原子电荷，同时保留整链Gasteiger值作差异诊断。未覆盖的碳链、醚氧、端羟基和氢原子没有被补值；核心电荷和不是整条链总电荷。",
                "因此结果证明核心环境可定位和计量闭合，不构成完整TPU电荷集，也不放行生产MD。下一步需建立重复单元/端基等价约束并验证完整链总电荷与局部偶极。",
                "",
            ]
        ),
    )
    files = [parameter_out, mapping_out, summary_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": "twelve_chain_core_mapping_completed_full_charge_assignment_pending",
        "counts": {
            "formulations": len(summary_table),
            "core_parameter_rows": len(parameters),
            "mapped_atom_rows": len(mappings),
            "urethane_occurrences": int(
                summary_table["urethane_occurrence_count"].sum()
            ),
            "terminal_nco_occurrences": int(
                summary_table["terminal_nco_occurrence_count"].sum()
            ),
        },
        "minimum_mapped_heavy_atom_fraction": float(
            summary_table["mapped_heavy_atom_fraction"].min()
        ),
        "maximum_mapped_heavy_atom_fraction": float(
            summary_table["mapped_heavy_atom_fraction"].max()
        ),
        "maximum_absolute_transfer_minus_gasteiger_e": float(
            summary_table["maximum_absolute_transfer_minus_gasteiger_e"].max()
        ),
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (graph_path, plan_path, joint_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_unmapped_atoms_and_forcefield_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "RESP核心转移发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--化学图",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链化学图.csv.gz",
    )
    parser.add_argument(
        "--计量计划",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链计量计划.csv",
    )
    parser.add_argument(
        "--联合RESP",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "RESP联合验证" / "联合RESP逐原子比较.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "RESP核心转移",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-resp-core-transfer-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.化学图,
        args.计量计划,
        args.联合RESP,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
