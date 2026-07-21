from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import zipfile
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE = (
    PROJECT_ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十批计算_OMG"
)
RAW = BASE / "原始"
DERIVED = BASE / "派生"
MANIFEST = BASE / "来源清单.json"
FIELD_DICTIONARY = BASE / "计算字段字典.json"
AUDIT_OUTPUT = BASE / "审计结果.json"

EXPECTED_PROPERTY_FIELDS = [
    "methyl_terminated_product",
    "asphericity_Boltzmann_average",
    "eccentricity_Boltzmann_average",
    "inertial_shape_factor_Boltzmann_average",
    "radius_of_gyration_Boltzmann_average",
    "spherocity_Boltzmann_average",
    "molecular_weight_Boltzmann_average",
    "logP_Boltzmann_average",
    "qed_Boltzmann_average",
    "TPSA_Boltzmann_average",
    "normalized_monomer_phi_Boltzmann_average",
    "normalized_backbone_phi_Boltzmann_average",
    "HOMO_minus_1_Boltzmann_average",
    "HOMO_Boltzmann_average",
    "LUMO_Boltzmann_average",
    "LUMO_plus_1_Boltzmann_average",
    "dipole_moment_Boltzmann_average",
    "quadrupole_moment_Boltzmann_average",
    "polarizability_Boltzmann_average",
    "s1_energy_Boltzmann_average",
    "dominant_transition_energy_Boltzmann_average",
    "dominant_transition_oscillator_strength_Boltzmann_average",
    "t1_energy_Boltzmann_average",
    "chi_parameter_water_mean",
    "chi_parameter_ethanol_mean",
    "chi_parameter_chloroform_mean",
    "reaction_id",
]


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def count_csv_rows(handle) -> int:
    reader = csv.reader(handle)
    next(reader)
    return sum(1 for _ in reader)


def verify_sources(manifest: dict) -> list[dict]:
    results = []
    for source in manifest["sources"]:
        for item in source["files"]:
            path = BASE / item["relative_path"]
            if not path.is_file():
                raise FileNotFoundError(path)
            result = {
                "relative_path": item["relative_path"],
                "bytes": path.stat().st_size,
                "sha256": file_hash(path, "sha256"),
                "md5": file_hash(path, "md5"),
            }
            for key in ("bytes", "sha256", "md5"):
                if result[key] != item[key]:
                    raise ValueError(f"integrity mismatch for {path}: {key}")
            results.append(result)
    return results


def load_property_rows(paths: list[tuple[Path, str]]) -> tuple[dict[int, dict], Counter]:
    rows: dict[int, dict] = {}
    split_counts: Counter = Counter()
    for path, split in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != EXPECTED_PROPERTY_FIELDS:
                raise ValueError(f"unexpected property fields in {path}")
            for row in reader:
                reaction_id = int(row["reaction_id"])
                if reaction_id in rows:
                    raise ValueError(f"duplicate reaction_id across property files: {reaction_id}")
                for name in EXPECTED_PROPERTY_FIELDS[1:-1]:
                    value = float(row[name])
                    if not math.isfinite(value):
                        raise ValueError(f"non-finite {name} at reaction_id={reaction_id}")
                row["source_split"] = split
                rows[reaction_id] = row
                split_counts[split] += 1
    return rows, split_counts


def audit() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    field_dictionary = json.loads(FIELD_DICTIONARY.read_text(encoding="utf-8"))
    if len(field_dictionary["fields"]) != 25:
        raise ValueError("field dictionary must contain 25 property fields")
    integrity = verify_sources(manifest)

    properties, split_counts = load_property_rows(
        [
            (RAW / "OMG_train_batch_3_chemprop_with_reaction_id.csv", "active_learning_round_3"),
            (RAW / "test_chemprop_with_reaction_id.csv", "stratified_test"),
        ]
    )
    if len(properties) != 47676:
        raise ValueError(f"expected 47676 unique property rows, got {len(properties)}")

    archive_path = RAW / "OMG_monomers_CRU.zip"
    candidate_path = DERIVED / "OMG_PU_反应候选_100584.csv"
    property_path = DERIVED / "OMG_PU_计算属性_2086.csv"
    candidate_tmp = candidate_path.with_suffix(candidate_path.suffix + ".tmp")
    property_tmp = property_path.with_suffix(property_path.suffix + ".tmp")
    DERIVED.mkdir(parents=True, exist_ok=True)

    candidate_fields = [
        "reaction_id", "reaction_idx", "reaction_class", "reactant_1", "reactant_2", "product",
        "record_fidelity", "is_experimental", "source_dataset_doi", "subset_rule",
    ]
    property_fields = [
        "reaction_id", "reaction_idx", "reaction_class", "reactant_1", "reactant_2", "product",
        "methyl_terminated_product", "record_fidelity", "label_origin", "is_experimental",
        "calculation_method_summary", "source_split", "source_dataset_doi", "source_article_doi",
    ] + EXPECTED_PROPERTY_FIELDS[1:-1]

    polymer_count = 0
    pu_count = 0
    joined_ids: set[int] = set()
    pu_property_count = 0
    pu_products: set[str] = set()
    pu_reactant_1: set[str] = set()
    pu_reactant_2: set[str] = set()
    pu_property_products: set[str] = set()
    pu_methyl_products: list[str] = []

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        if {"OMG_monomers.csv", "OMG_polymers.csv"} - names:
            raise ValueError("required OMG CSV members are missing from archive")
        with archive.open("OMG_monomers.csv") as binary:
            import io
            with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
                monomer_count = count_csv_rows(text)

        with (
            archive.open("OMG_polymers.csv") as binary,
            candidate_tmp.open("w", encoding="utf-8", newline="") as candidate_handle,
            property_tmp.open("w", encoding="utf-8", newline="") as property_handle,
        ):
            import io
            text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text)
            if reader.fieldnames != ["reaction_idx", "reactant_1", "reactant_2", "product"]:
                raise ValueError("unexpected OMG_polymers.csv header")
            candidate_writer = csv.DictWriter(candidate_handle, fieldnames=candidate_fields)
            property_writer = csv.DictWriter(property_handle, fieldnames=property_fields)
            candidate_writer.writeheader()
            property_writer.writeheader()

            for reaction_id, polymer in enumerate(reader):
                polymer_count += 1
                property_row = properties.get(reaction_id)
                if property_row is not None:
                    joined_ids.add(reaction_id)
                if polymer["reaction_idx"] != "6":
                    continue
                pu_count += 1
                pu_products.add(polymer["product"])
                pu_reactant_1.add(polymer["reactant_1"])
                pu_reactant_2.add(polymer["reactant_2"])
                candidate_writer.writerow(
                    {
                        "reaction_id": reaction_id,
                        "reaction_idx": 6,
                        "reaction_class": "[step_growth]_[di_isocyanate]_[di_ol]",
                        "reactant_1": polymer["reactant_1"],
                        "reactant_2": polymer["reactant_2"],
                        "product": polymer["product"],
                        "record_fidelity": "virtual_reaction_template_candidate",
                        "is_experimental": "false",
                        "source_dataset_doi": "10.5281/zenodo.7556992",
                        "subset_rule": "reaction_idx == 6",
                    }
                )
                if property_row is None:
                    continue
                pu_property_count += 1
                pu_property_products.add(polymer["product"])
                pu_methyl_products.append(property_row["methyl_terminated_product"])
                output = {
                    "reaction_id": reaction_id,
                    "reaction_idx": 6,
                    "reaction_class": "[step_growth]_[di_isocyanate]_[di_ol]",
                    "reactant_1": polymer["reactant_1"],
                    "reactant_2": polymer["reactant_2"],
                    "product": polymer["product"],
                    "methyl_terminated_product": property_row["methyl_terminated_product"],
                    "record_fidelity": "direct_computational_reference",
                    "label_origin": "computed_not_experimental_not_model_prediction",
                    "is_experimental": "false",
                    "calculation_method_summary": "RDKit; GFN2-xTB conformers; revPBE-D3/def2-SVP DFT/TDDFT with CPCM epsilon=2.4; COSMO-SAC",
                    "source_split": property_row["source_split"],
                    "source_dataset_doi": "10.5281/zenodo.13863778",
                    "source_article_doi": "10.1039/D4SC08617A",
                }
                output.update({name: property_row[name] for name in EXPECTED_PROPERTY_FIELDS[1:-1]})
                property_writer.writerow(output)
            text.detach()

    expected = {"monomers": 77281, "polymers": 12886131, "pu": 100584, "pu_properties": 2086}
    actual = {
        "monomers": monomer_count,
        "polymers": polymer_count,
        "pu": pu_count,
        "pu_properties": pu_property_count,
    }
    if actual != expected:
        raise ValueError(f"row-count audit failed: expected={expected}, actual={actual}")
    if len(joined_ids) != len(properties):
        missing = sorted(set(properties) - joined_ids)[:10]
        raise ValueError(f"property reaction_id join is incomplete; examples={missing}")

    os.replace(candidate_tmp, candidate_path)
    os.replace(property_tmp, property_path)
    result = {
        "status": "pass",
        "source_integrity": integrity,
        "raw_row_counts": {
            "OMG_monomers.csv": monomer_count,
            "OMG_polymers.csv": polymer_count,
            "active_learning_round_3": split_counts["active_learning_round_3"],
            "stratified_test": split_counts["stratified_test"],
            "computed_property_total": len(properties),
        },
        "pu_subset": {
            "rule": "reaction_idx == 6",
            "official_mapping": "[step_growth]_[di_isocyanate]_[di_ol]",
            "rows": pu_count,
            "unique_products": len(pu_products),
            "unique_reactant_1": len(pu_reactant_1),
            "unique_reactant_2": len(pu_reactant_2),
            "interpretation": "virtual synthetically accessible reaction-template candidates",
        },
        "reaction_id_join": {
            "property_rows": len(properties),
            "joined_rows": len(joined_ids),
            "coverage": len(joined_ids) / len(properties),
            "pu_computed_rows": pu_property_count,
            "pu_unique_repeat_unit_products": len(pu_property_products),
            "pu_unique_methyl_terminated_products": len(set(pu_methyl_products)),
            "pu_duplicate_methyl_terminated_rows": len(pu_methyl_products) - len(set(pu_methyl_products)),
        },
        "label_policy": {
            "candidate_table": "virtual_reaction_template_candidate",
            "property_table": "direct_computational_reference",
            "is_experimental": False,
            "is_12m_ml_prediction": False,
        },
        "derived_files": [
            {"relative_path": str(candidate_path.relative_to(BASE)), "rows": pu_count, "sha256": file_hash(candidate_path)},
            {"relative_path": str(property_path.relative_to(BASE)), "rows": pu_property_count, "sha256": file_hash(property_path)},
        ],
        "field_dictionary": FIELD_DICTIONARY.name,
    }
    AUDIT_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
