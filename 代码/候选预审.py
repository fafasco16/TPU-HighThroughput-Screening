"""TPU 虚拟配方的人工预审状态与第一层 DFT 队列选择。

结构警示是人工复核触发器，不是 GHS 分类、SDS 替代品或供应结论。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator


_HALOGENS = {9, 17, 35}


def structure_alerts(smiles: str, component_class: str) -> list[str]:
    """返回只用于人工复核分流的结构警示。"""

    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("SMILES 不能为空")
    molecule = Chem.MolFromSmiles(smiles.strip())
    if molecule is None:
        raise ValueError(f"无法解析 SMILES: {smiles}")
    alerts: list[str] = []
    if component_class == "diisocyanate":
        alerts.append("isocyanate_group_requires_SDS_review")
    if any(atom.GetIsAromatic() for atom in molecule.GetAtoms()):
        alerts.append("aromatic_structure_requires_exposure_review")
    if any(atom.GetAtomicNum() in _HALOGENS for atom in molecule.GetAtoms()):
        alerts.append("halogenated_structure_requires_environmental_review")
    return alerts


def _component_review_table(components: pd.DataFrame) -> pd.DataFrame:
    required = {"candidate_id", "component_class", "canonical_smiles"}
    missing = required.difference(components.columns)
    if missing:
        raise ValueError(f"构件库缺少字段: {sorted(missing)}")
    if not components["candidate_id"].is_unique:
        raise ValueError("构件库 candidate_id 不唯一")
    rows = []
    for row in components.itertuples(index=False):
        alerts = structure_alerts(row.canonical_smiles, row.component_class)
        rows.append(
            {
                "candidate_id": row.candidate_id,
                "component_class": row.component_class,
                "structure_review_alerts": ";".join(alerts) if alerts else "none_detected_by_limited_rules",
                "structure_review_alert_count": len(alerts),
            }
        )
    return pd.DataFrame(rows)


def annotate_formulations(
    formulations: pd.DataFrame,
    components: pd.DataFrame,
    manual_review: Mapping[str, str],
) -> pd.DataFrame:
    """连接三构件警示并写入不能由结构自动断言的人工状态。"""

    required_formulation = {
        "formulation_id",
        "combination_id",
        "diisocyanate_id",
        "macrodiol_proxy_id",
        "chain_extender_id",
        "macrodiol_nominal_mn_g_mol",
        "hard_segment_mass_fraction_target",
        "nco_oh_ratio_target",
        "citation_keys",
    }
    missing = required_formulation.difference(formulations.columns)
    if missing:
        raise ValueError(f"配方候选缺少字段: {sorted(missing)}")
    if not formulations["formulation_id"].is_unique:
        raise ValueError("配方候选 formulation_id 不唯一")
    review = _component_review_table(components).set_index("candidate_id")
    output = formulations[
        [
            "formulation_id",
            "combination_id",
            "diisocyanate_id",
            "diisocyanate_smiles",
            "macrodiol_proxy_id",
            "macrodiol_proxy_smiles",
            "chain_extender_id",
            "chain_extender_smiles",
            "macrodiol_nominal_mn_g_mol",
            "hard_segment_mass_fraction_target",
            "nco_oh_ratio_target",
            "citation_keys",
        ]
    ].copy()
    role_columns = {
        "diisocyanate": "diisocyanate_id",
        "macrodiol_proxy": "macrodiol_proxy_id",
        "chain_extender": "chain_extender_id",
    }
    alert_count = np.zeros(len(output), dtype=np.int64)
    for role, id_column in role_columns.items():
        ids = output[id_column]
        missing_ids = sorted(set(ids).difference(review.index))
        if missing_ids:
            raise ValueError(f"{role} 构件未在构件库中找到: {missing_ids[:3]}")
        output[f"{role}_structure_review_alerts"] = ids.map(
            review["structure_review_alerts"]
        )
        counts = ids.map(review["structure_review_alert_count"]).astype(int)
        output[f"{role}_structure_review_alert_count"] = counts
        alert_count += counts.to_numpy()
    output["total_structure_review_alert_count"] = alert_count
    output["structure_review_status"] = "limited_rule_screen_completed_manual_confirmation_required"
    for field in (
        "procurement_status",
        "ehs_status",
        "literature_novelty_status",
        "experimental_eligibility",
    ):
        if field not in manual_review:
            raise ValueError(f"人工预审配置缺少字段: {field}")
        output[field] = str(manual_review[field])
    output["macrodiol_identity_status"] = "proxy_only_real_oligomer_identity_not_closed"
    output["performance_claim_status"] = "no_performance_claim"
    output["precheck_interpretation"] = (
        "structure_alerts_are_review_triggers_not_hazard_or_supply_conclusions"
    )
    return output.sort_values("formulation_id", kind="stable").reset_index(drop=True)


def _fingerprint_array(smiles: str, radius: int, bits: int) -> np.ndarray:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"无法解析队列构件 SMILES: {smiles}")
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=bits)
    fingerprint = generator.GetFingerprint(molecule)
    array = np.zeros(bits, dtype=np.uint8)
    DataStructs.ConvertToNumpyArray(fingerprint, array)
    return array.astype(bool)


def _union_fingerprints(frame: pd.DataFrame, radius: int, bits: int) -> np.ndarray:
    cache: dict[str, np.ndarray] = {}
    rows: list[np.ndarray] = []
    smiles_columns = (
        "diisocyanate_smiles",
        "macrodiol_proxy_smiles",
        "chain_extender_smiles",
    )
    for row in frame.itertuples(index=False):
        combined = np.zeros(bits, dtype=bool)
        for column in smiles_columns:
            smiles = str(getattr(row, column))
            if smiles not in cache:
                cache[smiles] = _fingerprint_array(smiles, radius, bits)
            combined |= cache[smiles]
        rows.append(combined)
    return np.stack(rows)


def _maxmin_indexes(fingerprints: np.ndarray, ids: pd.Series, count: int) -> list[int]:
    if fingerprints.ndim != 2 or len(fingerprints) != len(ids):
        raise ValueError("组合指纹与候选 ID 数量不一致")
    if not 0 < count <= len(ids):
        raise ValueError("队列数量必须为正且不超过候选数")
    bit_counts = fingerprints.sum(axis=1)
    median_bits = float(np.median(bit_counts))
    first = min(
        range(len(ids)),
        key=lambda index: (abs(float(bit_counts[index]) - median_bits), str(ids.iloc[index])),
    )
    selected = [first]
    available = np.ones(len(ids), dtype=bool)
    available[first] = False
    maximum_similarity = np.zeros(len(ids), dtype=np.float64)
    while len(selected) < count:
        latest = fingerprints[selected[-1]]
        intersections = np.logical_and(fingerprints, latest).sum(axis=1)
        unions = np.logical_or(fingerprints, latest).sum(axis=1)
        similarities = np.divide(
            intersections,
            unions,
            out=np.ones(len(ids), dtype=np.float64),
            where=unions > 0,
        )
        maximum_similarity = np.maximum(maximum_similarity, similarities)
        best_similarity = float(maximum_similarity[available].min())
        tied = np.flatnonzero(available & np.isclose(maximum_similarity, best_similarity, rtol=0.0, atol=1e-15))
        winner = min(tied, key=lambda index: str(ids.iloc[index]))
        selected.append(int(winner))
        available[winner] = False
    return selected


def select_dft_queue(precheck: pd.DataFrame, dft_config: Mapping[str, object]) -> pd.DataFrame:
    """从固定工艺格点按三构件联合指纹多样性选择 Tier-1 DFT 队列。"""

    grid = precheck.loc[
        precheck["macrodiol_nominal_mn_g_mol"].eq(float(dft_config["macrodiol_nominal_mn_g_mol"]))
        & precheck["hard_segment_mass_fraction_target"].eq(float(dft_config["hard_segment_mass_fraction_target"]))
        & precheck["nco_oh_ratio_target"].eq(float(dft_config["nco_oh_ratio_target"]))
    ].sort_values("formulation_id", kind="stable").reset_index(drop=True)
    queue_size = int(dft_config["queue_size"])
    if len(grid) < queue_size:
        raise ValueError(f"固定格点只有 {len(grid)} 条，无法选择 {queue_size} 条队列")
    fingerprints = _union_fingerprints(
        grid,
        radius=int(dft_config["fingerprint_radius"]),
        bits=int(dft_config["fingerprint_bits"]),
    )
    indexes = _maxmin_indexes(fingerprints, grid["formulation_id"], queue_size)
    queue = grid.iloc[indexes].copy().reset_index(drop=True)
    queue.insert(0, "queue_rank", np.arange(1, len(queue) + 1))
    for field in (
        "dft_stage",
        "dft_protocol_id",
        "md_stage",
        "queue_selection_basis",
        "performance_claim_status",
    ):
        queue[field] = str(dft_config[field])
    queue["dft_calculation_status"] = "not_started"
    queue["dft_input_status"] = "requires_conformer_generation_and_charge_spin_review"
    queue["md_block_reason"] = "macrodiol_is_structure_proxy_not_real_oligomer_distribution"
    keep = [
        "queue_rank",
        "formulation_id",
        "combination_id",
        "diisocyanate_id",
        "diisocyanate_smiles",
        "macrodiol_proxy_id",
        "macrodiol_proxy_smiles",
        "chain_extender_id",
        "chain_extender_smiles",
        "macrodiol_nominal_mn_g_mol",
        "hard_segment_mass_fraction_target",
        "nco_oh_ratio_target",
        "total_structure_review_alert_count",
        "procurement_status",
        "ehs_status",
        "literature_novelty_status",
        "experimental_eligibility",
        "dft_stage",
        "dft_protocol_id",
        "dft_calculation_status",
        "dft_input_status",
        "md_stage",
        "md_block_reason",
        "queue_selection_basis",
        "performance_claim_status",
        "citation_keys",
    ]
    return queue[keep]
