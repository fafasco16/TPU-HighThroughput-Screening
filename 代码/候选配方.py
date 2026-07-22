"""线性 TPU 三组分虚拟配方候选的可审计构建函数。

本模块只做两件事：从冻结的 Gold-V 构件池中挑出化学身份明确的原型构件，
并按二异氰酸酯—二醇扩链剂—宏二醇的线性 TPU 计量关系生成虚拟配方。
它不预测性能、不声明商业可得性，也不把虚拟结构写回 Gold-E/Gold-C。
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import Descriptors, rdFingerprintGenerator, rdMolDescriptors


_ISOCYANATE = Chem.MolFromSmarts("[N]=[C]=[O]")
# NCO 中的中心碳是 sp，不能用仅匹配 sp2 碳的 [CX3]；必须覆盖全部 C=O。
_CARBONYL = Chem.MolFromSmarts("[#6](=O)")
# N 是 NCO 的必需元素；其余集合是常见有机 TPU 原型构件中的 C/O 与卤素。
_ALLOWED_ELEMENTS = {6, 7, 8, 9, 17, 35}
_FINGERPRINT = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=512)


def stable_id(prefix: str, values: Iterable[object]) -> str:
    """为候选、组合和配方生成稳定且与输入绑定的 ID。"""

    normalized = "|".join(str(value) for value in values)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _molecule(smiles: object) -> Chem.Mol:
    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("构件缺少 canonical_smiles")
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"无法解析构件 SMILES: {smiles}")
    return molecule


def _non_isocyanate_carbonyl_count(molecule: Chem.Mol) -> int:
    carbonyl_count = len(molecule.GetSubstructMatches(_CARBONYL))
    isocyanate_count = len(molecule.GetSubstructMatches(_ISOCYANATE))
    return max(0, carbonyl_count - isocyanate_count)


def _side_chain_multiple_bond_count(molecule: Chem.Mol) -> int:
    """统计 NCO 以外的脂肪族 C=C/C#C，作为原型路线的竞争反应门。"""

    count = 0
    for bond in molecule.GetBonds():
        if bond.GetIsAromatic() or bond.GetBondTypeAsDouble() <= 1.0:
            continue
        begin = bond.GetBeginAtom()
        end = bond.GetEndAtom()
        if begin.GetAtomicNum() == 6 and end.GetAtomicNum() == 6:
            count += 1
    return count


def _isocyanate_attachment_count(molecule: Chem.Mol) -> int:
    """统计 NCO 氮原子连接到的不同骨架原子数，排除同一位点的双 NCO 原型。"""

    attachments: set[int] = set()
    for nitrogen_index, carbon_index, _oxygen_index in molecule.GetSubstructMatches(_ISOCYANATE):
        nitrogen = molecule.GetAtomWithIdx(nitrogen_index)
        for neighbor in nitrogen.GetNeighbors():
            if neighbor.GetIdx() != carbon_index:
                attachments.add(neighbor.GetIdx())
    return len(attachments)


def component_descriptor_row(row: pd.Series) -> dict[str, Any]:
    """返回可解释的 RDKit 描述符；不把描述符解释为性能。"""

    molecule = _molecule(row["canonical_smiles"])
    atoms = {atom.GetAtomicNum() for atom in molecule.GetAtoms()}
    return {
        "heavy_atom_count": int(molecule.GetNumHeavyAtoms()),
        "ring_count": int(rdMolDescriptors.CalcNumRings(molecule)),
        "aromatic_atom_fraction": round(
            sum(atom.GetIsAromatic() for atom in molecule.GetAtoms())
            / max(1, molecule.GetNumHeavyAtoms()),
            6,
        ),
        "rotatable_bond_count": int(rdMolDescriptors.CalcNumRotatableBonds(molecule)),
        "rdkit_mol_weight_g_mol": round(float(Descriptors.MolWt(molecule)), 6),
        "non_isocyanate_carbonyl_count": _non_isocyanate_carbonyl_count(molecule),
        "side_chain_multiple_bond_count": _side_chain_multiple_bond_count(molecule),
        "isocyanate_attachment_count": _isocyanate_attachment_count(molecule),
        "has_isotope_label": any(atom.GetIsotope() for atom in molecule.GetAtoms()),
        "allowed_element_set": atoms.issubset(_ALLOWED_ELEMENTS),
    }


def component_gate_reason(row: pd.Series, component_class: str) -> str:
    """返回第一个未通过的原型结构门；空字符串代表通过。"""

    molecule = _molecule(row["canonical_smiles"])
    molecular_weight = float(row["molecular_weight_calculated_g_mol"])
    isocyanate = int(row["isocyanate_group_count"])
    hydroxyl = int(row["hydroxyl_group_count"])
    reactive_side_groups = sum(
        int(row[column])
        for column in (
            "amine_group_count",
            "thiol_group_count",
            "carboxylic_acid_group_count",
            "cyclic_carbonate_group_count",
            "epoxide_group_count",
        )
    )
    if reactive_side_groups:
        return "含竞争反应官能团"
    if any(atom.GetIsotope() for atom in molecule.GetAtoms()):
        return "含同位素标记，非原型合成构件"
    if not {atom.GetAtomicNum() for atom in molecule.GetAtoms()}.issubset(_ALLOWED_ELEMENTS):
        return "含原型路线未纳入的元素"
    if _side_chain_multiple_bond_count(molecule):
        return "含NCO以外脂肪族不饱和键"
    if component_class == "diisocyanate":
        if isocyanate != 2 or hydroxyl != 0:
            return "二异氰酸酯官能度不闭合"
        if _isocyanate_attachment_count(molecule) != 2:
            return "二异氰酸酯NCO连接位点不分离"
        if not 140.0 <= molecular_weight <= 450.0:
            return "二异氰酸酯分子量超出原型范围"
        if _non_isocyanate_carbonyl_count(molecule):
            return "二异氰酸酯含额外羰基"
        return ""
    if component_class == "chain_extender_diol":
        if hydroxyl != 2 or isocyanate != 0:
            return "扩链二醇官能度不闭合"
        if not 60.0 <= molecular_weight <= 250.0:
            return "扩链二醇分子量超出原型范围"
        if _non_isocyanate_carbonyl_count(molecule):
            return "扩链二醇含额外羰基"
        return ""
    if component_class == "macrodiol":
        if hydroxyl != 2 or isocyanate != 0:
            return "宏二醇代理官能度不闭合"
        if not 250.0 <= molecular_weight <= 450.0:
            return "宏二醇代理分子量超出原型范围"
        if _non_isocyanate_carbonyl_count(molecule):
            return "宏二醇代理含额外羰基"
        return ""
    raise ValueError(f"未知构件类别: {component_class}")


def gated_components(candidates: pd.DataFrame, component_class: str) -> tuple[pd.DataFrame, Counter[str]]:
    """执行构件门并保留每一种排除原因的数量。"""

    required = {
        "candidate_id",
        "source_id",
        "source_family_id",
        "preferred_name",
        "canonical_smiles",
        "molecular_weight_calculated_g_mol",
        "isocyanate_group_count",
        "hydroxyl_group_count",
        "amine_group_count",
        "thiol_group_count",
        "carboxylic_acid_group_count",
        "cyclic_carbonate_group_count",
        "epoxide_group_count",
        "linear_component_class",
        "linear_tpu_building_block_ready",
        "license_spdx",
        "license_status",
    }
    missing = required.difference(candidates.columns)
    if missing:
        raise ValueError(f"候选结构缺少字段: {sorted(missing)}")
    pool = candidates.loc[
        candidates["linear_tpu_building_block_ready"].fillna(False)
        & candidates["linear_component_class"].eq(component_class)
    ].copy()
    reasons: Counter[str] = Counter()
    accepted_indexes: list[int] = []
    for index, row in pool.iterrows():
        reason = component_gate_reason(row, component_class)
        if reason:
            reasons[reason] += 1
        else:
            accepted_indexes.append(index)
    accepted = pool.loc[accepted_indexes].copy()
    if accepted.empty:
        raise ValueError(f"{component_class} 没有通过原型结构门的构件")
    return accepted.sort_values("candidate_id", kind="stable").reset_index(drop=True), reasons


def select_diverse_components(pool: pd.DataFrame, count: int) -> pd.DataFrame:
    """以 Morgan-Tanimoto max-min 从通过门的构件中稳定抽取多样化子集。"""

    if count <= 0:
        raise ValueError("选择数量必须为正")
    ordered = pool.sort_values("candidate_id", kind="stable").reset_index(drop=True)
    if len(ordered) <= count:
        result = ordered.copy()
        result["diversity_selection_rank"] = np.arange(1, len(result) + 1)
        return result
    fingerprints = [_FINGERPRINT.GetFingerprint(_molecule(smiles)) for smiles in ordered["canonical_smiles"]]
    median_weight = float(ordered["molecular_weight_calculated_g_mol"].median())
    first = min(
        range(len(ordered)),
        key=lambda index: (
            abs(float(ordered.loc[index, "molecular_weight_calculated_g_mol"]) - median_weight),
            str(ordered.loc[index, "candidate_id"]),
        ),
    )
    selected = [first]
    remaining = set(range(len(ordered)))
    remaining.remove(first)
    while len(selected) < count:
        distances = {
            index: min(
                1.0 - DataStructs.TanimotoSimilarity(fingerprints[index], fingerprints[other])
                for other in selected
            )
            for index in remaining
        }
        maximum_distance = max(distances.values())
        winner = min(
            (
                str(ordered.loc[index, "candidate_id"]),
                index,
            )
            for index, distance in distances.items()
            if np.isclose(distance, maximum_distance, rtol=0.0, atol=1e-15)
        )
        winner = winner[1]
        selected.append(winner)
        remaining.remove(winner)
    result = ordered.loc[selected].copy().reset_index(drop=True)
    result["diversity_selection_rank"] = np.arange(1, len(result) + 1)
    return result


def build_component_library(candidates: pd.DataFrame, selection_counts: dict[str, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """构建被选构件库与门禁审计摘要。"""

    rows: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []
    for component_class in ("diisocyanate", "chain_extender_diol", "macrodiol"):
        gated, excluded = gated_components(candidates, component_class)
        selected = select_diverse_components(gated, int(selection_counts[component_class]))
        descriptors = pd.DataFrame(
            [component_descriptor_row(row) for _, row in selected.iterrows()]
        )
        selected = pd.concat([selected.reset_index(drop=True), descriptors], axis=1)
        selected["component_class"] = component_class
        selected["selection_status"] = "selected_for_virtual_formulation_space"
        selected["selection_reason"] = "通过原型结构门；Morgan-Tanimoto最大最小多样性抽样"
        selected["experimental_status"] = "未采购未合成；需路线、EHS与供应复核"
        selected["source_citation_key"] = np.where(
            selected["source_id"].eq("source_zenodo_12585902_polyuniverse_pu"),
            "ledger-135-yue-2024-polyuniverse-data;ledger-136-yue-2024-polyuniverse;ledger-137-yue-polyuniverse-code",
            "ledger-007-smipoly-2023",
        )
        rows.append(selected)
        audit_rows.append(
            {
                "component_class": component_class,
                "input_linear_building_blocks": int(
                    (
                        candidates["linear_tpu_building_block_ready"].fillna(False)
                        & candidates["linear_component_class"].eq(component_class)
                    ).sum()
                ),
                "passed_prototype_gate": len(gated),
                "selected_for_virtual_space": len(selected),
                "excluded_reason_counts": "; ".join(
                    f"{reason}:{number}" for reason, number in sorted(excluded.items())
                )
                or "无",
            }
        )
    library = pd.concat(rows, ignore_index=True)
    keep = [
        "candidate_id", "component_class", "source_id", "source_family_id",
        "source_citation_key", "preferred_name", "canonical_smiles",
        "molecular_weight_calculated_g_mol", "isocyanate_group_count",
        "hydroxyl_group_count", "heavy_atom_count", "ring_count",
        "aromatic_atom_fraction", "rotatable_bond_count", "rdkit_mol_weight_g_mol",
        "non_isocyanate_carbonyl_count", "side_chain_multiple_bond_count",
        "isocyanate_attachment_count", "has_isotope_label", "allowed_element_set",
        "diversity_selection_rank", "selection_status",
        "selection_reason", "experimental_status", "license_spdx", "license_status",
    ]
    return library[keep].sort_values(["component_class", "diversity_selection_rank"]), pd.DataFrame(audit_rows)


def build_component_combinations(library: pd.DataFrame, macro_choices_per_diisocyanate: int, extender_choices_per_macro: int) -> pd.DataFrame:
    """用平衡轮转构造组合，不做三组分全笛卡尔积。"""

    if macro_choices_per_diisocyanate <= 0 or extender_choices_per_macro <= 0:
        raise ValueError("每种构件的轮转选择数量必须为正")
    groups = {
        name: frame.sort_values("candidate_id", kind="stable").reset_index(drop=True)
        for name, frame in library.groupby("component_class", sort=False)
    }
    diisocyanates = groups["diisocyanate"]
    macrodiols = groups["macrodiol"]
    extenders = groups["chain_extender_diol"]
    rows: list[dict[str, object]] = []
    for di_index, diisocyanate in diisocyanates.iterrows():
        for macro_offset in range(macro_choices_per_diisocyanate):
            macro_index = (di_index * macro_choices_per_diisocyanate + macro_offset) % len(macrodiols)
            macrodiol = macrodiols.iloc[macro_index]
            for extender_offset in range(extender_choices_per_macro):
                extender_index = (
                    di_index * extender_choices_per_macro
                    + macro_index
                    + extender_offset
                ) % len(extenders)
                extender = extenders.iloc[extender_index]
                component_ids = [
                    diisocyanate["candidate_id"],
                    macrodiol["candidate_id"],
                    extender["candidate_id"],
                ]
                rows.append(
                    {
                        "combination_id": stable_id("combo", component_ids),
                        "diisocyanate_id": diisocyanate["candidate_id"],
                        "diisocyanate_smiles": diisocyanate["canonical_smiles"],
                        "diisocyanate_mw_g_mol": diisocyanate["rdkit_mol_weight_g_mol"],
                        "macrodiol_proxy_id": macrodiol["candidate_id"],
                        "macrodiol_proxy_smiles": macrodiol["canonical_smiles"],
                        "macrodiol_proxy_monomer_mw_g_mol": macrodiol["rdkit_mol_weight_g_mol"],
                        "chain_extender_id": extender["candidate_id"],
                        "chain_extender_smiles": extender["canonical_smiles"],
                        "chain_extender_mw_g_mol": extender["rdkit_mol_weight_g_mol"],
                        "combination_rule": "平衡轮转；非全笛卡尔积",
                        "structure_status": "three_components_passed_prototype_gate",
                        "experimental_status": "未采购未合成；需路线、EHS与供应复核",
                        "citation_keys": ";".join(sorted({
                            str(diisocyanate["source_citation_key"]),
                            str(macrodiol["source_citation_key"]),
                            str(extender["source_citation_key"]),
                        })),
                    }
                )
    frame = pd.DataFrame(rows)
    if not frame["combination_id"].is_unique:
        raise ValueError("组合 ID 重复，构件轮转逻辑不应产生重复组合")
    return frame.sort_values("combination_id", kind="stable").reset_index(drop=True)


def solve_chain_extender_moles(
    macrodiol_nominal_mn_g_mol: float,
    diisocyanate_mw_g_mol: float,
    chain_extender_mw_g_mol: float,
    hard_segment_mass_fraction: float,
    nco_oh_ratio: float,
) -> float:
    """按双官能端基、无副反应假设求每摩尔宏二醇所需扩链剂摩尔数。"""

    if not 0.0 < hard_segment_mass_fraction < 1.0:
        raise ValueError("硬段质量分数必须在 0 与 1 之间")
    if macrodiol_nominal_mn_g_mol <= 0 or diisocyanate_mw_g_mol <= 0 or chain_extender_mw_g_mol <= 0:
        raise ValueError("计量相关分子量必须为正")
    if nco_oh_ratio <= 0:
        raise ValueError("NCO/OH 必须为正")
    diisocyanate_mass_per_macro = nco_oh_ratio * diisocyanate_mw_g_mol
    denominator = chain_extender_mw_g_mol + diisocyanate_mass_per_macro
    value = (
        hard_segment_mass_fraction * macrodiol_nominal_mn_g_mol / (1.0 - hard_segment_mass_fraction)
        - diisocyanate_mass_per_macro
    ) / denominator
    if value <= 0:
        raise ValueError("给定硬段目标下扩链剂摩尔数不为正")
    return float(value)


def build_formulations(combinations: pd.DataFrame, formulation_grid: dict[str, list[float]]) -> pd.DataFrame:
    """把每个构件组合展开为计量闭合的虚拟线性 TPU 配方。"""

    rows: list[dict[str, object]] = []
    for _, combination in combinations.iterrows():
        for nominal_mn in formulation_grid["macrodiol_nominal_mn_g_mol"]:
            for hard_fraction in formulation_grid["hard_segment_mass_fraction_target"]:
                for nco_oh in formulation_grid["nco_oh_ratio_target"]:
                    extender_moles = solve_chain_extender_moles(
                        float(nominal_mn),
                        float(combination["diisocyanate_mw_g_mol"]),
                        float(combination["chain_extender_mw_g_mol"]),
                        float(hard_fraction),
                        float(nco_oh),
                    )
                    diisocyanate_moles = float(nco_oh) * (1.0 + extender_moles)
                    macro_mass = float(nominal_mn)
                    extender_mass = extender_moles * float(combination["chain_extender_mw_g_mol"])
                    diisocyanate_mass = diisocyanate_moles * float(combination["diisocyanate_mw_g_mol"])
                    total_mass = macro_mass + extender_mass + diisocyanate_mass
                    actual_hard_fraction = (extender_mass + diisocyanate_mass) / total_mass
                    nco_equivalent = 2.0 * diisocyanate_moles
                    oh_equivalent = 2.0 * (1.0 + extender_moles)
                    formulation_id = stable_id(
                        "formulation",
                        [
                            combination["combination_id"],
                            format(float(nominal_mn), ".6g"),
                            format(float(hard_fraction), ".6g"),
                            format(float(nco_oh), ".6g"),
                        ],
                    )
                    rows.append(
                        {
                            "formulation_id": formulation_id,
                            "combination_id": combination["combination_id"],
                            "screening_stage": "structure_and_stoichiometry_ready",
                            "formulation_route": "linear_tpu_three_component_two_step_prepolymer_hypothesis",
                            "diisocyanate_id": combination["diisocyanate_id"],
                            "diisocyanate_smiles": combination["diisocyanate_smiles"],
                            "macrodiol_proxy_id": combination["macrodiol_proxy_id"],
                            "macrodiol_proxy_smiles": combination["macrodiol_proxy_smiles"],
                            "chain_extender_id": combination["chain_extender_id"],
                            "chain_extender_smiles": combination["chain_extender_smiles"],
                            "macrodiol_nominal_mn_g_mol": float(nominal_mn),
                            "hard_segment_mass_fraction_target": float(hard_fraction),
                            "nco_oh_ratio_target": float(nco_oh),
                            "macrodiol_moles_per_mol_macrodiol": 1.0,
                            "chain_extender_moles_per_mol_macrodiol": extender_moles,
                            "diisocyanate_moles_per_mol_macrodiol": diisocyanate_moles,
                            "macrodiol_mass_g_per_mol_macrodiol": macro_mass,
                            "chain_extender_mass_g_per_mol_macrodiol": extender_mass,
                            "diisocyanate_mass_g_per_mol_macrodiol": diisocyanate_mass,
                            "total_reactive_mass_g_per_mol_macrodiol": total_mass,
                            "hard_segment_mass_fraction_calculated": actual_hard_fraction,
                            "nco_oh_ratio_calculated": nco_equivalent / oh_equivalent,
                            "stoichiometry_residual": abs(actual_hard_fraction - float(hard_fraction)),
                            "performance_prediction_status": "not_scored_by_baseline",
                            "model_applicability_status": "not_evaluated",
                            "novelty_status": "not_assessed_against_full_literature",
                            "dft_md_status": "not_calculated",
                            "experimental_status": "virtual_hypothesis_requires_route_ehs_procurement_and_synthesis_review",
                            "citation_keys": combination["citation_keys"],
                        }
                    )
    frame = pd.DataFrame(rows)
    if not frame["formulation_id"].is_unique:
        raise ValueError("配方 ID 重复")
    return frame.sort_values("formulation_id", kind="stable").reset_index(drop=True)
