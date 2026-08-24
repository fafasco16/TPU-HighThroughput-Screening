"""按现实MD整数计量计划构建线性TPU低聚链二维化学图。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


ROOT = Path(__file__).resolve().parents[1]
_NCO = Chem.MolFromSmarts("[N:1]=[C:2]=[O:3]")
_OH = Chem.MolFromSmarts("[O;H1;+0:1]")
_URETHANE = Chem.MolFromSmarts("[N;H1:1]-[C:2](=[O:3])-[O:4]")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nco_sites(molecule: Chem.Mol) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        sorted(
            (
                (int(match[0]), int(match[1]), int(match[2]))
                for match in molecule.GetSubstructMatches(_NCO)
            ),
            key=lambda match: (match[1], match[0], match[2]),
        )
    )


def oh_sites(molecule: Chem.Mol) -> tuple[int, ...]:
    return tuple(sorted(int(match[0]) for match in molecule.GetSubstructMatches(_OH)))


def urethane_bond_count(molecule: Chem.Mol) -> int:
    return len(molecule.GetSubstructMatches(_URETHANE))


def couple_specific_nco_oh(
    nco_molecule: Chem.Mol,
    oh_molecule: Chem.Mol,
    *,
    nco_carbon_index: int,
    oh_oxygen_index: int,
) -> Chem.Mol:
    matches = {
        carbon: (nitrogen, oxygen) for nitrogen, carbon, oxygen in nco_sites(nco_molecule)
    }
    if nco_carbon_index not in matches:
        raise ValueError("指定NCO碳不属于未反应异氰酸酯基")
    if oh_oxygen_index not in oh_sites(oh_molecule):
        raise ValueError("指定OH氧不属于未反应羟基")
    nitrogen_index, _ = matches[nco_carbon_index]
    combined = Chem.CombineMols(nco_molecule, oh_molecule)
    editable = Chem.RWMol(combined)
    existing = editable.GetBondBetweenAtoms(nitrogen_index, nco_carbon_index)
    if existing is None or existing.GetBondType() != Chem.BondType.DOUBLE:
        raise ValueError("NCO氮碳键不是预期双键")
    editable.RemoveBond(nitrogen_index, nco_carbon_index)
    editable.AddBond(nitrogen_index, nco_carbon_index, Chem.BondType.SINGLE)
    editable.AddBond(
        nco_carbon_index,
        nco_molecule.GetNumAtoms() + oh_oxygen_index,
        Chem.BondType.SINGLE,
    )
    product = editable.GetMol()
    try:
        Chem.SanitizeMol(product)
    except Exception as exc:
        raise ValueError("氨基甲酸酯偶联后价态/芳香性门失败") from exc
    return product


def _oh_unit_sequence(
    macrodiol_count: int, chain_extender_count: int
) -> list[str]:
    base, remainder = divmod(chain_extender_count, macrodiol_count)
    sequence: list[str] = []
    for index in range(macrodiol_count):
        sequence.append("macrodiol")
        sequence.extend(
            ["chain_extender"] * (base + (1 if index < remainder else 0))
        )
    return sequence


def _parse_bifunctional(smiles: str, kind: str) -> Chem.Mol:
    molecule = Chem.MolFromSmiles(str(smiles))
    if molecule is None:
        raise ValueError(f"{kind}结构无法解析")
    if kind == "diisocyanate" and len(nco_sites(molecule)) != 2:
        raise ValueError("二异氰酸酯必须恰好两个NCO基")
    if kind in {"macrodiol", "chain_extender"} and len(oh_sites(molecule)) != 2:
        raise ValueError(f"{kind}必须恰好两个OH基")
    return molecule


def build_linear_oligomer(
    diisocyanate_smiles: str,
    macrodiol_smiles: str,
    chain_extender_smiles: str,
    macrodiol_count: int,
    chain_extender_count: int,
    diisocyanate_count: int,
) -> dict[str, Any]:
    if macrodiol_count < 1 or chain_extender_count < 1:
        raise ValueError("宏二醇和扩链剂数必须为正")
    if diisocyanate_count != macrodiol_count + chain_extender_count:
        raise ValueError("二异氰酸酯数必须等于宏二醇与扩链剂数之和")
    diisocyanate = _parse_bifunctional(diisocyanate_smiles, "diisocyanate")
    macrodiol = _parse_bifunctional(macrodiol_smiles, "macrodiol")
    extender = _parse_bifunctional(chain_extender_smiles, "chain_extender")
    sequence = _oh_unit_sequence(macrodiol_count, chain_extender_count)
    if len(sequence) != diisocyanate_count:
        raise ValueError("OH构件序列长度与二异氰酸酯数不一致")

    chain = Chem.Mol(diisocyanate)
    first_unit = Chem.Mol(macrodiol if sequence[0] == "macrodiol" else extender)
    chain_sites = nco_sites(chain)
    unit_sites = oh_sites(first_unit)
    growth_nco = chain_sites[-1][1]
    used_oh = unit_sites[0]
    other_oh = unit_sites[1]
    old_chain_atoms = chain.GetNumAtoms()
    chain = couple_specific_nco_oh(
        chain,
        first_unit,
        nco_carbon_index=growth_nco,
        oh_oxygen_index=used_oh,
    )
    growth_oh = old_chain_atoms + other_oh

    for unit_kind in sequence[1:]:
        new_diisocyanate = Chem.Mol(diisocyanate)
        new_sites = nco_sites(new_diisocyanate)
        reacting_nco = new_sites[0][1]
        remaining_nco = new_sites[1][1]
        chain = couple_specific_nco_oh(
            new_diisocyanate,
            chain,
            nco_carbon_index=reacting_nco,
            oh_oxygen_index=growth_oh,
        )
        growth_nco = remaining_nco
        next_unit = Chem.Mol(macrodiol if unit_kind == "macrodiol" else extender)
        unit_sites = oh_sites(next_unit)
        used_oh = unit_sites[0]
        other_oh = unit_sites[1]
        old_chain_atoms = chain.GetNumAtoms()
        chain = couple_specific_nco_oh(
            chain,
            next_unit,
            nco_carbon_index=growth_nco,
            oh_oxygen_index=used_oh,
        )
        growth_oh = old_chain_atoms + other_oh

    expected_urethane = 2 * diisocyanate_count - 1
    observed_nco = len(nco_sites(chain))
    observed_oh = len(oh_sites(chain))
    observed_urethane = urethane_bond_count(chain)
    if (
        observed_nco != 1
        or observed_oh != 1
        or observed_urethane != expected_urethane
    ):
        raise ValueError(
            "低聚链端基/氨基甲酸酯键计数不闭合: "
            f"NCO={observed_nco}, OH={observed_oh}, urethane={observed_urethane}"
        )
    canonical_smiles = Chem.MolToSmiles(chain, canonical=True)
    canonical = Chem.MolFromSmiles(canonical_smiles)
    if canonical is None:
        raise ValueError("低聚链规范SMILES无法回读")
    return {
        "canonical_smiles": canonical_smiles,
        "molecular_formula": rdMolDescriptors.CalcMolFormula(canonical),
        "exact_mol_weight_g_mol": float(Descriptors.ExactMolWt(canonical)),
        "atom_count": int(Chem.AddHs(canonical).GetNumAtoms()),
        "oh_unit_sequence": ";".join(sequence),
        "urethane_bond_count": observed_urethane,
        "remaining_nco_group_count": observed_nco,
        "remaining_oh_group_count": observed_oh,
        "chemical_graph_status": "completed",
        "three_dimensional_status": "not_generated",
        "forcefield_status": "not_parameterized",
        "model_scope": "single_sequence_oligomer_proxy",
        "performance_claim_status": "no_performance_claim",
    }


def _maps(
    components: pd.DataFrame, macro_models: pd.DataFrame
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    required_components = {"component_id", "role", "canonical_smiles"}
    required_macro = {"component_id", "representative_smiles"}
    missing = required_components.difference(components.columns)
    if missing:
        raise ValueError(f"现实构件缺少字段: {sorted(missing)}")
    missing = required_macro.difference(macro_models.columns)
    if missing:
        raise ValueError(f"PTMG模型缺少字段: {sorted(missing)}")
    if not components["component_id"].is_unique or not macro_models[
        "component_id"
    ].is_unique:
        raise ValueError("现实构件或PTMG模型ID不唯一")
    return (
        {
            str(row["component_id"]): row
            for row in components.to_dict(orient="records")
        },
        {
            str(row["component_id"]): row
            for row in macro_models.to_dict(orient="records")
        },
    )


def build_graph_table(
    plans: pd.DataFrame,
    components: pd.DataFrame,
    macro_models: pd.DataFrame,
) -> pd.DataFrame:
    required = {
        "formulation_id",
        "diisocyanate_id",
        "macrodiol_id",
        "chain_extender_id",
        "macrodiol_count",
        "chain_extender_count",
        "diisocyanate_count",
        "estimated_atom_count",
        "model_scope",
    }
    missing = required.difference(plans.columns)
    if missing:
        raise ValueError(f"MD计量计划缺少字段: {sorted(missing)}")
    if not plans["formulation_id"].is_unique:
        raise ValueError("MD计量计划formulation_id不唯一")
    component_map, macro_map = _maps(components, macro_models)
    rows: list[dict[str, Any]] = []
    for plan in plans.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        di_id = str(plan["diisocyanate_id"])
        macro_id = str(plan["macrodiol_id"])
        extender_id = str(plan["chain_extender_id"])
        if di_id not in component_map or extender_id not in component_map:
            raise ValueError("MD计量计划离散构件身份缺失")
        if macro_id not in macro_map:
            raise ValueError("MD计量计划宏二醇模型缺失")
        result = build_linear_oligomer(
            str(component_map[di_id]["canonical_smiles"]),
            str(macro_map[macro_id]["representative_smiles"]),
            str(component_map[extender_id]["canonical_smiles"]),
            int(plan["macrodiol_count"]),
            int(plan["chain_extender_count"]),
            int(plan["diisocyanate_count"]),
        )
        if result["atom_count"] != int(plan["estimated_atom_count"]):
            raise ValueError(
                f"{plan['formulation_id']}化学图原子数与计量估算不一致"
            )
        rows.append(
            {
                "formulation_id": plan["formulation_id"],
                "diisocyanate_id": di_id,
                "macrodiol_id": macro_id,
                "chain_extender_id": extender_id,
                "macrodiol_count": int(plan["macrodiol_count"]),
                "chain_extender_count": int(plan["chain_extender_count"]),
                "diisocyanate_count": int(plan["diisocyanate_count"]),
                **result,
            }
        )
    return pd.DataFrame(rows).sort_values("formulation_id").reset_index(drop=True)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    plan_path: Path,
    components_path: Path,
    macro_models_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (plan_path, components_path, macro_models_path):
        if not path.is_file():
            raise ValueError(f"低聚链化学图输入不存在: {path}")
    table = build_graph_table(
        pd.read_csv(plan_path),
        pd.read_csv(components_path),
        pd.read_csv(macro_models_path),
    )
    output_path = output_root / "低聚链化学图.csv.gz"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    table.to_csv(
        temporary,
        index=False,
        encoding="utf-8",
        compression={"method": "gzip", "mtime": 0},
    )
    temporary.replace(output_path)
    manifest = {
        "release_id": release_id,
        "status": "chemical_graph_ready_3d_and_forcefield_blocked",
        "counts": {
            "plans": len(table),
            "graphs_completed": int(
                table["chemical_graph_status"].eq("completed").sum()
            ),
        },
        "inputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (plan_path, components_path, macro_models_path)
        },
        "file": {
            "path": output_path.name,
            "bytes": output_path.stat().st_size,
            "sha256": sha256(output_path),
        },
        "interpretation_limit": (
            "single deterministic 2D oligomer graph; no 3D conformation, forcefield, "
            "chain distribution, morphology, or TPU performance claim"
        ),
    }
    _atomic_text(
        output_root / "低聚链化学图发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--计量计划",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链计量计划.csv",
    )
    parser.add_argument(
        "--现实构件", type=Path, default=ROOT / "数据" / "现实库" / "构件.csv"
    )
    parser.add_argument(
        "--PTMG模型",
        type=Path,
        default=ROOT / "数据" / "现实库" / "PTMG代表模型.csv",
    )
    parser.add_argument(
        "--输出目录", type=Path, default=ROOT / "计算" / "现实MD"
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-oligomer-graphs-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.计量计划,
        args.现实构件,
        args.PTMG模型,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
