"""第十批开放数据的统一多保真物化接口。

本模块只读取已冻结并完成独立审计的四批原件/派生表，不联网、不改写原始数据，
也不创建训练权重或训练/验证划分。百万行 OMG 计算属性通过可重复调用的流式
迭代器提供，避免一次性构造 ``list[dict]`` 占用数 GB 内存。

接口约定：

* ``build_omg_candidate_rows``：100,584 个 PU 反应模板 Gold-V 候选；
* ``build_openpoly_candidate_rows``：默认 3,502 个 Gold-V 候选，可按已有
  canonical SMILES 集去重；
* ``iter_omg_gold_c_rows``：47,676 个计算体系 × 25 属性的 1,191,900 行；
* ``build_openpoly_gold_c_rows``：4,524 个公开 MD 标签；
* ``build_sciencedb_gold_e_rows``：643 个样品 × 3 个实验目标；
* ``build_kinetics_gold_e_rows``：171 个实测 %NCO 动力学点。

聚合物 pSMILES 中的 ``*`` 连接点可由 RDKit 规范化，但不能可靠生成 InChI。
因此 Gold-V 的 ``canonical_smiles`` 始终保留规范 pSMILES；候选表要求的
InChIKey、分子式和分子量则来自把每个 ``*`` 替换为碳的端甲基代理，并在
``structure_status`` 中显式标明，禁止把代理身份误当作无限链身份。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from functools import lru_cache
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Iterator

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors

try:
    from .SMiPoly_TPU候选分类 import CANDIDATE_COLUMNS, _group_counts
    from .第十批ACS表格物化 import RECORD_COLUMNS as GOLD_E_RECORD_COLUMNS
    from .第十批OpenPolymerChallenge import (
        DEFAULT_DATA_DIR as OPENPOLY_DIR,
        PROPERTY_SPECS as OPENPOLY_PROPERTY_SPECS,
        _load_dataset as load_openpoly_dataset,
        audit_dataset as audit_openpoly_dataset,
    )
except ImportError:  # pragma: no cover - 支持直接执行脚本
    from SMiPoly_TPU候选分类 import CANDIDATE_COLUMNS, _group_counts
    from 第十批ACS表格物化 import RECORD_COLUMNS as GOLD_E_RECORD_COLUMNS
    from 第十批OpenPolymerChallenge import (
        DEFAULT_DATA_DIR as OPENPOLY_DIR,
        PROPERTY_SPECS as OPENPOLY_PROPERTY_SPECS,
        _load_dataset as load_openpoly_dataset,
        audit_dataset as audit_openpoly_dataset,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "数据/原始/外部数据/新增开放数据"

OMG_DIR = DATA_ROOT / "第十批计算_OMG"
OMG_CANDIDATE_CSV = OMG_DIR / "派生/OMG_PU_反应候选_100584.csv"
OMG_PU_PROPERTY_CSV = OMG_DIR / "派生/OMG_PU_计算属性_2086.csv"
OMG_PROPERTY_FILES = (
    (OMG_DIR / "原始/OMG_train_batch_3_chemprop_with_reaction_id.csv", "active_learning_round_3"),
    (OMG_DIR / "原始/test_chemprop_with_reaction_id.csv", "stratified_test"),
)
OMG_FIELD_DICTIONARY = OMG_DIR / "计算字段字典.json"
OMG_AUDIT = OMG_DIR / "审计结果.json"

SCIENCEDB_DIR = DATA_ROOT / "第十批实验_ScienceDB643"
SCIENCEDB_CSV = SCIENCEDB_DIR / "派生/PUE643_标准化643.csv"
SCIENCEDB_AUDIT = SCIENCEDB_DIR / "审计结果.json"

KINETICS_DIR = DATA_ROOT / "第十批实验_无溶剂PU反应动力学"
KINETICS_MEASUREMENTS = KINETICS_DIR / "NCO测量长表.tsv"
KINETICS_CONDITIONS = KINETICS_DIR / "反应条件清单.tsv"
KINETICS_HASHES = KINETICS_DIR / "来源文件哈希.json"
KINETICS_AUDIT = KINETICS_DIR / "内容审计摘要.json"

SOURCE_OMG = "source_omg_batch10"
SOURCE_OPENPOLY = "source_openpolymer_challenge_v1"
SOURCE_SCIENCEDB = "source_sciencedb_pue643_v1"
SOURCE_KINETICS = "source_zenodo_6406174"

COMPUTATIONAL_RECORD_COLUMNS = (
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "system_identity",
    "structure_identity_status",
    "global_structure_family_key",
    "simulation_key",
    "property_name",
    "value",
    "unit",
    "unit_status",
    "method_family",
    "method_detail",
    "fidelity_level",
    "temp",
    "press",
    "gold_admission_status",
    "property_admission_status",
    "source_validation_status",
    "record_role",
    "potential_weight_ceiling",
    "current_weight_materialized",
    "training_weight",
    "source_locator",
    "citation_keys",
)

OMG_SYSTEM_COUNT = 47_676
OMG_PROPERTY_COUNT = 25
OMG_GOLD_C_COUNT = OMG_SYSTEM_COUNT * OMG_PROPERTY_COUNT
OMG_PU_SYSTEM_COUNT = 2_086
OMG_CANDIDATE_COUNT = 100_584
OPENPOLY_CANDIDATE_COUNT = 3_502
OPENPOLY_GOLD_C_COUNT = 4_524
SCIENCEDB_GOLD_E_COUNT = 643 * 3
KINETICS_GOLD_E_COUNT = 171

OMG_CITATIONS = (
    "ledger-154-kim-2024-omg-property-database;"
    "ledger-155-kim-2025-functional-monomer-design"
)
OMG_CANDIDATE_CITATIONS = (
    "ledger-152-kim-2023-omg-v1-0b-data;"
    "ledger-153-kim-2023-open-macromolecular-genome"
)
OPENPOLY_CITATIONS = (
    "ledger-156-liu-2025-open-polymer-challenge-data;"
    "ledger-157-liu-2025-open-polymer-challenge-report"
)
SCIENCEDB_CITATIONS = "ledger-158-li-2024-sciencedb-pue643-data"
KINETICS_CITATIONS = (
    "ledger-159-asadauskas-2022-solventfree-pu-kinetics-data;"
    "ledger-160-asadauskas-2023-solventless-pu-kinetics"
)

SCIENCEDB_INPUT_COLUMNS = (
    "ZS_CHS",
    "ZS_R",
    "ZS_log_Tr1K",
    "ZS_log_Tr2K",
    "PMStep",
    "Form_Method",
    "ZS_log_CSArea",
    "ZS_log_StrainRate",
    "ZS_log_PO_MW",
    "ZS_log_FCVm",
    "ZS_FCCED",
    "ZS_log_Fchi",
    "ZS_SS_TPSA_norm",
    "ZS_SS_MolLogP_norm",
    "ZS_HS_BertzCT",
    "ZS_SS_VSA_EState8",
    "ZS_SS_PEOE_VSA8",
    "ZS_log_HS_NumNHCO_norm",
    "ZS_FC_NumHAcceptors_norm",
    "ZS_FC_RingCount_norm",
)
SCIENCEDB_TARGETS = {
    "logYM": "published log-transformed Young's modulus",
    "logTS": "published log-transformed tensile strength",
    "logEB": "published log-transformed elongation at break",
}

CARBAMATE = Chem.MolFromSmarts("[NX3][CX3](=[OX1])[OX2]")
if CARBAMATE is None:  # pragma: no cover
    raise RuntimeError("氨基甲酸酯 SMARTS 编译失败")


class MaterializationBlocked(RuntimeError):
    """冻结数据、审计元数据或确定性行数发生漂移。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationBlocked(f"JSON 无法读取：{path}") from exc


def _verify_hash(path: Path, expected: str) -> None:
    if not path.is_file():
        raise MaterializationBlocked(f"缺少冻结文件：{path}")
    actual = _sha256(path)
    if actual != expected:
        raise MaterializationBlocked(
            f"冻结文件 SHA-256 漂移：{path}; expected={expected}; actual={actual}"
        )


def _canonical_psmiles(raw_smiles: str, context: str) -> tuple[str, Chem.Mol]:
    molecule = Chem.MolFromSmiles(raw_smiles)
    if molecule is None:
        raise MaterializationBlocked(f"pSMILES 无法解析：{context}")
    return Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True), molecule


def _methyl_capped_proxy(molecule: Chem.Mol, context: str) -> Chem.Mol:
    editable = Chem.RWMol(molecule)
    for atom in editable.GetAtoms():
        if atom.GetAtomicNum() == 0:
            atom.SetAtomicNum(6)
            atom.SetIsotope(0)
            atom.SetAtomMapNum(0)
            atom.SetNoImplicit(False)
    capped = editable.GetMol()
    try:
        Chem.SanitizeMol(capped)
    except Exception as exc:  # pragma: no cover - 冻结数据已通过 RDKit 审计
        raise MaterializationBlocked(f"端甲基代理无法规范化：{context}") from exc
    return capped


def _candidate_row(
    *,
    candidate_id: str,
    source_id: str,
    source_record_id: str,
    source_locator: str,
    preferred_name: str,
    raw_smiles: str,
    license_spdx: str,
    data_origin: str,
    generation_rule: str,
    force_tpu_core: bool,
    parsed_structure: tuple[str, Chem.Mol] | None = None,
) -> dict[str, Any]:
    canonical, molecule = parsed_structure or _canonical_psmiles(
        raw_smiles, source_record_id
    )
    proxy = _methyl_capped_proxy(molecule, source_record_id)
    inchikey = Chem.MolToInchiKey(proxy)
    if not inchikey:
        raise MaterializationBlocked(f"端甲基代理 InChIKey 生成失败：{source_record_id}")
    groups = _group_counts(molecule)
    carbamate_count = len(molecule.GetSubstructMatches(CARBAMATE, uniquify=True))
    tpu_core = force_tpu_core or carbamate_count > 0
    proxy_weight = float(Descriptors.MolWt(proxy))
    row = {
        "candidate_id": candidate_id,
        "source_id": source_id,
        "source_record_id": source_record_id,
        "source_locator": source_locator,
        "preferred_name": preferred_name,
        "raw_smiles": raw_smiles,
        "canonical_smiles": canonical,
        "inchikey": inchikey,
        "molecular_formula_reported": "",
        "molecular_formula_calculated": rdMolDescriptors.CalcMolFormula(proxy),
        "molecular_weight_reported_g_mol": "",
        "molecular_weight_calculated_g_mol": round(proxy_weight, 6),
        "exact_mass_g_mol": round(float(Descriptors.ExactMolWt(proxy)), 6),
        "formal_charge": int(Chem.GetFormalCharge(proxy)),
        "heavy_atom_count": int(proxy.GetNumHeavyAtoms()),
        "isocyanate_group_count": groups["isocyanate"],
        "hydroxyl_group_count": groups["hydroxyl"],
        "amine_group_count": groups["amine"],
        "thiol_group_count": groups["thiol"],
        "carboxylic_acid_group_count": groups["carboxylic_acid"],
        "cyclic_carbonate_group_count": groups["cyclic_carbonate"],
        "epoxide_group_count": groups["epoxide"],
        "tpu_role": (
            "polyurethane_repeat_unit_candidate"
            if tpu_core
            else "polymer_repeat_unit_transfer_candidate"
        ),
        "role_confidence": "rule_high" if tpu_core else "transfer_medium",
        "role_basis": (
            "OMG reaction_idx=6 diisocyanate+diol template"
            if force_tpu_core
            else f"carbamate_substructure_match_count={carbamate_count}"
        ),
        "screening_scope": (
            "virtual_tpu_repeat_unit_candidate"
            if source_id == SOURCE_OMG
            else "general_polymer_md_transfer_reference"
        ),
        "screening_priority": 2 if source_id == SOURCE_OMG else 3,
        "functional_group_match": bool(tpu_core),
        "structure_status": (
            "rdkit_validated_polymer_psmiles;"
            "inchikey_formula_and_descriptors_from_methyl_capped_proxy"
        ),
        "duplicate_status": "canonical_unique_within_materialized_call",
        "license_spdx": license_spdx,
        "data_origin": data_origin,
        "fidelity_level": "candidate_structure",
        "gold_layer": "Gold-V",
        "gold_admission_status": "admitted_reference",
        "direct_property_supervision_weight_ceiling": 0.0,
        "prediction_uncertainty": "",
        "generation_rule_version": generation_rule,
        "rdkit_version": rdBase.rdkitVersion,
    }
    if tuple(row) != tuple(CANDIDATE_COLUMNS):
        raise MaterializationBlocked("候选字段顺序与 CANDIDATE_COLUMNS 不一致")
    return row


@lru_cache(maxsize=1)
def _omg_evidence() -> tuple[dict[str, dict[str, str]], frozenset[int]]:
    audit = _read_json(OMG_AUDIT)
    if audit.get("status") != "pass":
        raise MaterializationBlocked("OMG 审计状态不是 pass")
    counts = audit.get("raw_row_counts", {})
    if counts.get("computed_property_total") != OMG_SYSTEM_COUNT:
        raise MaterializationBlocked("OMG 计算体系数漂移")
    join = audit.get("reaction_id_join", {})
    if join.get("pu_computed_rows") != OMG_PU_SYSTEM_COUNT:
        raise MaterializationBlocked("OMG PU 计算体系数漂移")
    for item in audit.get("source_integrity", []):
        relative = str(item["relative_path"]).replace("\\", "/")
        if relative.endswith("chemprop_with_reaction_id.csv"):
            _verify_hash(OMG_DIR / Path(relative), str(item["sha256"]))
    for item in audit.get("derived_files", []):
        relative = str(item["relative_path"]).replace("\\", "/")
        _verify_hash(OMG_DIR / Path(relative), str(item["sha256"]))

    field_dictionary = _read_json(OMG_FIELD_DICTIONARY)
    fields = field_dictionary.get("fields", [])
    if len(fields) != OMG_PROPERTY_COUNT:
        raise MaterializationBlocked("OMG 计算字段字典不是 25 个属性")
    field_specs = {str(row["name"]): row for row in fields}
    if len(field_specs) != OMG_PROPERTY_COUNT:
        raise MaterializationBlocked("OMG 计算字段名重复")

    with OMG_PU_PROPERTY_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        pu_ids = frozenset(int(row["reaction_id"]) for row in csv.DictReader(handle))
    if len(pu_ids) != OMG_PU_SYSTEM_COUNT:
        raise MaterializationBlocked("OMG PU reaction_id 集合漂移")
    return field_specs, pu_ids


@lru_cache(maxsize=1)
def _openpoly_frame_and_audit() -> tuple[Any, dict[str, Any]]:
    result = audit_openpoly_dataset(OPENPOLY_DIR)
    if result.get("row_count") != OPENPOLY_CANDIDATE_COUNT:
        raise MaterializationBlocked("OpenPoly 候选结构数漂移")
    if result.get("observed_md_label_cell_count") != OPENPOLY_GOLD_C_COUNT:
        raise MaterializationBlocked("OpenPoly MD 标签数漂移")
    frame = load_openpoly_dataset(OPENPOLY_DIR)
    return frame, result


@lru_cache(maxsize=1)
def _sciencedb_audit() -> dict[str, Any]:
    audit = _read_json(SCIENCEDB_AUDIT)
    if audit.get("status") != "pass" or audit.get("sample_rows") != 643:
        raise MaterializationBlocked("ScienceDB PUE-643 审计计数漂移")
    derived = audit.get("derived_file", {})
    _verify_hash(SCIENCEDB_CSV, str(derived.get("sha256", "")))
    return audit


@lru_cache(maxsize=1)
def _kinetics_evidence() -> tuple[dict[str, Any], str]:
    audit = _read_json(KINETICS_AUDIT)
    counts = audit.get("counts", {})
    if counts.get("nonempty_nco_points") != KINETICS_GOLD_E_COUNT:
        raise MaterializationBlocked("无溶剂 PU 动力学点数漂移")
    hash_manifest = _read_json(KINETICS_HASHES)
    archive = next(
        (
            row
            for row in hash_manifest.get("本地文件", [])
            if row.get("文件名") == "Solvent_Free_Adhesives_Dataset_5-2.zip"
        ),
        None,
    )
    if archive is None:
        raise MaterializationBlocked("动力学来源哈希清单缺少 ZIP")
    _verify_hash(KINETICS_DIR / str(archive["文件名"]), str(archive["SHA256"]))
    return audit, str(archive["SHA256"])


def build_omg_candidate_rows(
    existing_canonical: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """生成 OMG reaction_idx=6 的 PU Gold-V 候选。

    ``existing_canonical`` 中的结构会被跳过；默认严格返回 100,584 行。
    """

    _omg_evidence()
    seen = set(existing_canonical or ())
    rows: list[dict[str, Any]] = []
    with OMG_CANDIDATE_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            reaction_id = int(source["reaction_id"])
            canonical, molecule = _canonical_psmiles(
                source["product"], f"OMG:{reaction_id}"
            )
            if canonical in seen:
                continue
            row = _candidate_row(
                candidate_id=f"omg-pu-{reaction_id}",
                source_id=SOURCE_OMG,
                source_record_id=f"omg-reaction:{reaction_id}",
                source_locator=(
                    "OMG_PU_反应候选_100584.csv#reaction_id=" f"{reaction_id}"
                ),
                preferred_name=f"OMG PU repeat-unit candidate {reaction_id}",
                raw_smiles=source["product"],
                license_spdx="GPL-3.0-or-later",
                data_origin="reaction_rule_generated",
                generation_rule="OMG-v1.0b-reaction_idx-6",
                force_tpu_core=True,
                parsed_structure=(canonical, molecule),
            )
            seen.add(row["canonical_smiles"])
            rows.append(row)
    if existing_canonical is None and len(rows) != OMG_CANDIDATE_COUNT:
        raise MaterializationBlocked(
            f"OMG 候选数漂移：expected={OMG_CANDIDATE_COUNT}; actual={len(rows)}"
        )
    return rows


def build_openpoly_candidate_rows(
    existing_canonical: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """生成 Open Polymer Challenge Gold-V 候选，并按 canonical 集去重。"""

    frame, _ = _openpoly_frame_and_audit()
    seen = set(existing_canonical or ())
    rows: list[dict[str, Any]] = []
    for source in frame.to_dict(orient="records"):
        row_id = str(source["row_id"])
        canonical, molecule = _canonical_psmiles(
            str(source["SMILES"]), f"OpenPoly:{row_id}"
        )
        if canonical in seen:
            continue
        row = _candidate_row(
            candidate_id=f"openpoly-{row_id.replace(':', '-')}",
            source_id=SOURCE_OPENPOLY,
            source_record_id=f"openpoly:{row_id}",
            source_locator=(
                f"OpenPolymerChallenge_官方赛后测试集_v1.zip!/"
                f"{source['source_group']}.csv#row={source['source_row']}"
            ),
            preferred_name=f"Open Polymer Challenge candidate {row_id}",
            raw_smiles=str(source["SMILES"]),
            license_spdx="MIT",
            data_origin="virtual",
            generation_rule="OpenPolymerChallenge-v1-official-test",
            force_tpu_core=False,
            parsed_structure=(canonical, molecule),
        )
        seen.add(row["canonical_smiles"])
        rows.append(row)
    if existing_canonical is None and len(rows) != OPENPOLY_CANDIDATE_COUNT:
        raise MaterializationBlocked(
            "OpenPoly 候选数漂移："
            f"expected={OPENPOLY_CANDIDATE_COUNT}; actual={len(rows)}"
        )
    return rows


def build_candidate_rows(
    existing_canonical: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """按 OMG→OpenPoly 顺序生成全批候选，并做跨来源 canonical 去重。"""

    seen = set(existing_canonical or ())
    omg = build_omg_candidate_rows(seen)
    seen.update(str(row["canonical_smiles"]) for row in omg)
    openpoly = build_openpoly_candidate_rows(seen)
    return [*omg, *openpoly]


def _method_family(method: str) -> str:
    if method == "RDKit":
        return "cheminformatics_descriptor"
    if method.startswith("graph-derived"):
        return "graph_descriptor"
    if method.startswith("GFN2-xTB"):
        return "GFN2-xTB"
    if method.startswith("revPBE"):
        return "DFT"
    if method.startswith("TDDFT"):
        return "TDDFT"
    if method.startswith("COSMO-SAC"):
        return "COSMO-SAC"
    return "published_computational_method"


def _global_polymer_key(canonical: str) -> str:
    """以固定长度哈希表示规范结构，避免在百万行长表重复存放超长键。"""

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"global_polymer_structure_{digest}"


def iter_omg_gold_c_rows() -> Iterator[dict[str, Any]]:
    """流式给出 OMG 1,191,900 个 Gold-C 数值记录。

    2,086 个 reaction_idx=6 PU 体系标为 TPU 高相关 admitted_reference；其余
    45,590 个可靠计算体系保留为 polymer-transfer conditional_reference。
    每次调用都会重新打开冻结 CSV，因此该函数可重复迭代，但每个返回的生成器
    本身是单次消费的。
    """

    field_specs, pu_ids = _omg_evidence()
    system_count = 0
    value_count = 0
    pu_systems_seen: set[int] = set()
    reaction_ids_seen: set[int] = set()
    for path, source_split in OMG_PROPERTY_FILES:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for source_row, source in enumerate(reader, start=2):
                reaction_id = int(source["reaction_id"])
                if reaction_id in reaction_ids_seen:
                    raise MaterializationBlocked(f"OMG reaction_id 重复：{reaction_id}")
                reaction_ids_seen.add(reaction_id)
                system_count += 1
                is_pu = reaction_id in pu_ids
                if is_pu:
                    pu_systems_seen.add(reaction_id)
                raw_structure = source["methyl_terminated_product"]
                canonical, _ = _canonical_psmiles(raw_structure, f"OMG-C:{reaction_id}")
                admission = "admitted_reference" if is_pu else "conditional_reference"
                role = (
                    "tpu_core_computational_reference"
                    if is_pu
                    else "polymer_transfer_computational_reference"
                )
                ceiling = 0.25 if is_pu else 0.10
                record_id = f"omg-property:{reaction_id}"
                locator = f"{path.name}#row={source_row};reaction_id={reaction_id}"
                for property_name, spec in field_specs.items():
                    value = float(source[property_name])
                    if not math.isfinite(value):
                        raise MaterializationBlocked(
                            f"OMG 非有限数：reaction_id={reaction_id}; property={property_name}"
                        )
                    value_count += 1
                    row = {
                        "source_id": SOURCE_OMG,
                        "source_record_id": record_id,
                        "observation_id": f"{record_id}:{property_name}",
                        "canonical_structure": canonical,
                        "system_identity": raw_structure,
                        "structure_identity_status": (
                            "rdkit_validated_methyl_terminated_constitutional_repeat_unit"
                        ),
                        "global_structure_family_key": _global_polymer_key(canonical),
                        "simulation_key": f"OMG:{source_split}:{reaction_id}",
                        "property_name": property_name,
                        "value": value,
                        "unit": str(spec["unit"]),
                        "unit_status": "parsed_from_published_field_dictionary",
                        "method_family": _method_family(str(spec["method"])),
                        "method_detail": str(spec["method"]),
                        "fidelity_level": "direct_computational_reference",
                        "temp": "",
                        "press": "",
                        "gold_admission_status": admission,
                        "property_admission_status": admission,
                        "source_validation_status": "official_release_hash_verified",
                        "record_role": role,
                        "potential_weight_ceiling": ceiling,
                        "current_weight_materialized": "false",
                        "training_weight": "",
                        "source_locator": f"{locator};field={property_name}",
                        "citation_keys": OMG_CITATIONS,
                    }
                    if tuple(row) != COMPUTATIONAL_RECORD_COLUMNS:
                        raise MaterializationBlocked("OMG Gold-C 字段顺序漂移")
                    yield row
    if system_count != OMG_SYSTEM_COUNT or value_count != OMG_GOLD_C_COUNT:
        raise MaterializationBlocked(
            "OMG Gold-C 计数漂移："
            f"systems={system_count}; values={value_count}"
        )
    if len(pu_systems_seen) != OMG_PU_SYSTEM_COUNT:
        raise MaterializationBlocked("OMG PU 高相关体系没有全部出现在全量计算表")


def build_openpoly_gold_c_rows() -> list[dict[str, Any]]:
    """物化 Open Polymer Challenge 的 4,524 个非缺失 MD 标签。"""

    frame, _ = _openpoly_frame_and_audit()
    rows: list[dict[str, Any]] = []
    for source in frame.to_dict(orient="records"):
        row_id = str(source["row_id"])
        canonical, molecule = _canonical_psmiles(
            str(source["SMILES"]), f"OpenPoly-C:{row_id}"
        )
        is_pu = molecule.HasSubstructMatch(CARBAMATE)
        admission = "admitted_reference" if is_pu else "conditional_reference"
        record_id = f"openpoly:{row_id}"
        for property_name, spec in OPENPOLY_PROPERTY_SPECS.items():
            raw_value = source[property_name]
            if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
                continue
            value = float(raw_value)
            if not math.isfinite(value):
                raise MaterializationBlocked(
                    f"OpenPoly 非有限数：row_id={row_id}; property={property_name}"
                )
            constrained_300k = property_name in {"Density", "Rg"}
            row = {
                "source_id": SOURCE_OPENPOLY,
                "source_record_id": record_id,
                "observation_id": f"{record_id}:{property_name}",
                "canonical_structure": canonical,
                "system_identity": str(source["SMILES"]),
                "structure_identity_status": "rdkit_validated_polymer_psmiles",
                "global_structure_family_key": _global_polymer_key(canonical),
                "simulation_key": (
                    f"OpenPoly:{spec['simulation_group']}:{row_id}"
                ),
                "property_name": property_name,
                "value": value,
                "unit": spec["unit"],
                "unit_status": "parsed_from_publication_method_table",
                "method_family": "MD",
                "method_detail": spec["method"],
                "fidelity_level": "direct_molecular_dynamics_reference",
                "temp": "300" if constrained_300k else "",
                "press": "1" if constrained_300k else "",
                "gold_admission_status": admission,
                "property_admission_status": admission,
                "source_validation_status": "official_release_hash_verified",
                "record_role": (
                    "tpu_core_computational_reference"
                    if is_pu
                    else "polymer_transfer_computational_reference"
                ),
                "potential_weight_ceiling": 0.25 if is_pu else 0.15,
                "current_weight_materialized": "false",
                "training_weight": "",
                "source_locator": (
                    f"OpenPolymerChallenge_官方赛后测试集_v1.zip!/"
                    f"{source['source_group']}.csv#row={source['source_row']};"
                    f"field={property_name}"
                ),
                "citation_keys": OPENPOLY_CITATIONS,
            }
            if tuple(row) != COMPUTATIONAL_RECORD_COLUMNS:
                raise MaterializationBlocked("OpenPoly Gold-C 字段顺序漂移")
            rows.append(row)
    if len(rows) != OPENPOLY_GOLD_C_COUNT:
        raise MaterializationBlocked(
            f"OpenPoly Gold-C 数量漂移：expected={OPENPOLY_GOLD_C_COUNT}; actual={len(rows)}"
        )
    return rows


def iter_gold_c_rows() -> Iterator[dict[str, Any]]:
    """按 OMG 全量→OpenPoly MD 的顺序流式返回本批 Gold-C。"""

    return chain(iter_omg_gold_c_rows(), iter(build_openpoly_gold_c_rows()))


def build_sciencedb_gold_e_rows() -> list[dict[str, Any]]:
    """只把 PUE-643 的三个实验目标作为 Gold-E，输入向量保存在上下文。"""

    audit = _sciencedb_audit()
    file_sha = str(audit["source_integrity"]["sha256"])
    rows: list[dict[str, Any]] = []
    with SCIENCEDB_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            sample_id = source["SSID"]
            context = {name: float(source[name]) for name in SCIENCEDB_INPUT_COLUMNS}
            context_json = json.dumps(
                context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            for property_name, meaning in SCIENCEDB_TARGETS.items():
                value = float(source[property_name])
                if not math.isfinite(value):
                    raise MaterializationBlocked(
                        f"ScienceDB 非有限目标：SSID={sample_id}; property={property_name}"
                    )
                row = {
                    "source_directory": "第十批实验_ScienceDB643",
                    "source_record_id": f"sciencedb-pue643:{sample_id}",
                    "observation_id": f"sciencedb-pue643:{sample_id}:{property_name}",
                    "formulation_id": sample_id,
                    "sample_id": sample_id,
                    "record_kind": "experimental_target_transformed",
                    "component_name": "PUE-643 formulation (identity encoded by published features)",
                    "component_role": "polyurethane_elastomer_formulation",
                    "property_name": property_name,
                    "value": value,
                    "unit": "published_log_transform_unit_unresolved",
                    "uncertainty_value": "",
                    "uncertainty_type": "",
                    "condition_name": "published_input_feature_vector",
                    "condition_value": context_json,
                    "condition_unit": "20_fields_mixed_standardized_or_coded",
                    "target_origin": "experimental",
                    "data_origin": "published_experimental_table",
                    "reduction_level": "published_sample_level_target",
                    "method_or_test_protocol": (
                        "published standardized/log-transformed PUE-643 table; "
                        "raw target unit and inverse-transform parameters absent from deposited CSV"
                    ),
                    "fidelity_level": "direct_experimental_transformed_reference",
                    "gold_admission_status": "conditional_reference",
                    "mapping_status": "sample_id_and_20_input_features_preserved",
                    "protocol_status": "published_protocol_partial_raw_units_unresolved",
                    "potential_weight_ceiling": 0.35,
                    "current_weight_materialized": "false",
                    "training_weight": "",
                    "split_group": f"PUE-643|{sample_id}",
                    "source_locator": f"PUE643_YM-TS-EB.csv#SSID={sample_id}",
                    "file_sha256": file_sha,
                    "license": "CC-BY-4.0",
                    "citation_keys": SCIENCEDB_CITATIONS,
                    "notes": (
                        f"{meaning}; input_context={context_json}; same PUE-643 source family, "
                        "not an independent experiment count; no training weight materialized"
                    ),
                }
                if tuple(row) != tuple(GOLD_E_RECORD_COLUMNS):
                    raise MaterializationBlocked("ScienceDB Gold-E 字段顺序漂移")
                rows.append(row)
    if len(rows) != SCIENCEDB_GOLD_E_COUNT:
        raise MaterializationBlocked(
            f"ScienceDB Gold-E 数量漂移：expected={SCIENCEDB_GOLD_E_COUNT}; actual={len(rows)}"
        )
    return rows


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def build_kinetics_gold_e_rows() -> list[dict[str, Any]]:
    """物化 171 个二丁胺滴定实测 %NCO 动力学点。"""

    audit, file_sha = _kinetics_evidence()
    conditions = {row["条件ID"]: row for row in _read_tsv(KINETICS_CONDITIONS)}
    measurements = _read_tsv(KINETICS_MEASUREMENTS)
    rows: list[dict[str, Any]] = []
    for source in measurements:
        condition = conditions.get(source["条件ID"])
        if condition is None:
            raise MaterializationBlocked(f"动力学条件无法连接：{source['条件ID']}")
        time_h = source["时间_h_原始"]
        admission = source["准入状态"]
        if admission not in {"admitted_reference", "conditional_reference"}:
            raise MaterializationBlocked(f"未知动力学准入状态：{admission}")
        condition_context = {
            "time_h": time_h,
            "previous_nonempty_time_h_context_only": source["前一非空时间_h_仅上下文"],
            "temperature_C": float(source["温度_C"]),
            "macrodiol_to_diisocyanate_molar_ratio": source["摩尔比"],
        }
        notes_context = {
            "macrodiol": condition["宏二醇化学身份"],
            "macrodiol_CAS": condition["宏二醇CAS"],
            "macrodiol_Mn_g_mol": condition["宏二醇Mn_g_mol"],
            "diisocyanate": condition["二异氰酸酯化学身份"],
            "diisocyanate_CAS": condition["二异氰酸酯CAS"],
            "diisocyanate_MW_g_mol": condition["二异氰酸酯分子量_g_mol"],
            "workbook_theoretical_initial_NCO_pct": source["工作簿理论初始NCO_pct"],
            "time_state": source["时间状态"],
            "zero_value_retained": source["是否零值"],
        }
        row = {
            "source_directory": "第十批实验_无溶剂PU反应动力学",
            "source_record_id": source["条件ID"],
            "observation_id": source["测量点ID"],
            "formulation_id": source["条件ID"],
            "sample_id": source["测量列ID"],
            "record_kind": "reaction_kinetics_measurement",
            "component_name": f"{source['宏二醇代码']}+{source['二异氰酸酯代码']}",
            "component_role": "macrodiol_plus_diisocyanate_reaction_system",
            "property_name": "NCO_content",
            "value": float(source["实测NCO_pct"]),
            "unit": "%",
            "uncertainty_value": "",
            "uncertainty_type": "",
            "condition_name": "time_h|temperature_C|molar_ratio",
            "condition_value": json.dumps(
                condition_context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "condition_unit": "h|degC|mol_ratio",
            "target_origin": "experimental",
            "data_origin": "experimental_dibutylamine_back_titration",
            "reduction_level": "individual_reported_titration_point",
            "method_or_test_protocol": (
                "modified ASTM D5155 dibutylamine back titration; "
                "%NCO = 0.42*(V_B-V_S)/m_S; solvent-free, dry nitrogen, mechanical stirring"
            ),
            "fidelity_level": "direct_experimental_measurement",
            "gold_admission_status": admission,
            "mapping_status": "reaction_components_and_condition_resolved",
            "protocol_status": (
                "published_protocol_resolved"
                if time_h != ""
                else "measurement_retained_missing_time_no_imputation"
            ),
            "potential_weight_ceiling": 0.65 if admission == "admitted_reference" else 0.35,
            "current_weight_materialized": "false",
            "training_weight": "",
            "split_group": source["拆分组"],
            "source_locator": source["来源位置"],
            "file_sha256": file_sha,
            "license": "CC-BY-4.0",
            "citation_keys": KINETICS_CITATIONS,
            "notes": json.dumps(
                notes_context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        if tuple(row) != tuple(GOLD_E_RECORD_COLUMNS):
            raise MaterializationBlocked("动力学 Gold-E 字段顺序漂移")
        rows.append(row)
    if len(rows) != KINETICS_GOLD_E_COUNT:
        raise MaterializationBlocked(
            f"动力学 Gold-E 数量漂移：expected={KINETICS_GOLD_E_COUNT}; actual={len(rows)}"
        )
    expected_admission = audit.get("measurement_admission_counts", {})
    actual_admission = {
        status: sum(row["gold_admission_status"] == status for row in rows)
        for status in ("admitted_reference", "conditional_reference")
    }
    if actual_admission != expected_admission:
        raise MaterializationBlocked(
            f"动力学准入计数漂移：expected={expected_admission}; actual={actual_admission}"
        )
    return rows


def build_gold_e_rows() -> list[dict[str, Any]]:
    """返回本批 2,100 个 Gold-E 实验目标/动力学观测。"""

    return [*build_sciencedb_gold_e_rows(), *build_kinetics_gold_e_rows()]


def audit_materialization_inputs() -> dict[str, Any]:
    """验证四批冻结输入，并返回不需展开百万行的确定性计数元数据。"""

    field_specs, pu_ids = _omg_evidence()
    _, openpoly_audit = _openpoly_frame_and_audit()
    sciencedb = _sciencedb_audit()
    kinetics, _ = _kinetics_evidence()
    return {
        "status": "pass",
        "training_weight_materialized": False,
        "candidate": {
            "omg": OMG_CANDIDATE_COUNT,
            "openpoly_before_cross_source_deduplication": OPENPOLY_CANDIDATE_COUNT,
        },
        "gold_c": {
            "omg_systems": OMG_SYSTEM_COUNT,
            "omg_properties_per_system": len(field_specs),
            "omg_values": OMG_GOLD_C_COUNT,
            "omg_tpu_high_relevance_systems": len(pu_ids),
            "omg_transfer_conditional_systems": OMG_SYSTEM_COUNT - len(pu_ids),
            "openpoly_values": int(openpoly_audit["observed_md_label_cell_count"]),
        },
        "gold_e": {
            "sciencedb_samples": int(sciencedb["sample_rows"]),
            "sciencedb_target_values": SCIENCEDB_GOLD_E_COUNT,
            "sciencedb_input_context_fields": len(SCIENCEDB_INPUT_COLUMNS),
            "kinetics_nco_measurements": int(kinetics["counts"]["nonempty_nco_points"]),
        },
        "schema": {
            "candidate_columns": list(CANDIDATE_COLUMNS),
            "computational_columns": list(COMPUTATIONAL_RECORD_COLUMNS),
            "experimental_columns": list(GOLD_E_RECORD_COLUMNS),
        },
    }


if __name__ == "__main__":
    print(json.dumps(audit_materialization_inputs(), ensure_ascii=False, indent=2))
