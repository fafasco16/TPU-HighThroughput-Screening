"""SMiPoly 单体 CSV 到 chemical staging 的保守适配器。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ids import stable_id


SCHEMA_VERSION = "v0.1"
EXPECTED_COLUMNS = (
    "comID",
    "MolecularFormula",
    "MolecularWeight",
    "SMILES",
    "IUPACName",
)


def _require_provenance(source_id: str, source_file_id: str) -> None:
    for name, value in (("source_id", source_id), ("source_file_id", source_file_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")


def _read_csv(path: str | Path) -> pd.DataFrame:
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.suffix.lower() != ".csv":
        raise ValueError(f"SMiPoly source must be CSV: {source_path}")
    frame = pd.read_csv(source_path, encoding="utf-8-sig")
    if tuple(frame.columns) != EXPECTED_COLUMNS:
        raise ValueError(
            "SMiPoly header fingerprint mismatch: "
            f"expected {EXPECTED_COLUMNS!r}, got {tuple(frame.columns)!r}"
        )
    return frame


def adapt_smipoly(
    path: str | Path,
    *,
    source_id: str,
    source_file_id: str,
) -> pd.DataFrame:
    """Extract SMiPoly identities without inferring functionality or TPU roles."""

    _require_provenance(source_id, source_file_id)
    frame = _read_csv(path)
    if frame.empty:
        raise ValueError("SMiPoly source contains no records")

    for column in ("comID", "MolecularFormula", "SMILES"):
        values = frame[column]
        if values.isna().any() or values.astype(str).str.strip().eq("").any():
            raise ValueError(f"SMiPoly {column} contains blank values")
    if frame["comID"].duplicated().any():
        raise ValueError("SMiPoly comID must be unique")

    weights = pd.to_numeric(frame["MolecularWeight"], errors="coerce")
    if weights.isna().any() or not np.isfinite(weights.to_numpy()).all():
        raise ValueError("SMiPoly MolecularWeight must contain only finite numbers")
    if (weights <= 0).any():
        raise ValueError("SMiPoly MolecularWeight must be positive")

    raw_smiles = frame["SMILES"].astype(str)
    duplicate_count = raw_smiles.map(raw_smiles.value_counts())
    output = pd.DataFrame(
        {
            "record_id": [
                stable_id("chemical_record", source_file_id, com_id)
                for com_id in frame["comID"]
            ],
            "chemical_id": [
                stable_id("chemical", "smipoly_raw_smiles", smiles)
                for smiles in raw_smiles
            ],
            "source_id": source_id,
            "source_file_id": source_file_id,
            "source_locator": [
                f"csv:row={row_number};comID={com_id}"
                for row_number, com_id in enumerate(frame["comID"], start=2)
            ],
            "extraction_method": "pandas.read_csv:utf-8-sig:explicit_header_map",
            "fidelity": "virtual_library_seed",
            "schema_version": SCHEMA_VERSION,
            "source_record_id": frame["comID"].astype(str),
            "molecular_formula_raw": frame["MolecularFormula"].astype(str),
            "molecular_weight_raw": weights.astype(float),
            "molecular_weight_unit_raw": "g/mol",
            "raw_smiles": raw_smiles,
            "iupac_name_raw": frame["IUPACName"].where(frame["IUPACName"].notna(), None),
            "normalization_status": "raw_smiles_unvalidated",
            "duplicate_group": [
                stable_id("smiles_duplicate_group", smiles) for smiles in raw_smiles
            ],
            "duplicate_count": duplicate_count.astype(int),
            "role_status": "unclassified",
            "tpu_role": pd.Series(pd.NA, index=frame.index, dtype="string"),
            "functionality": pd.Series(pd.NA, index=frame.index, dtype="Float64"),
        }
    )
    return output


__all__ = ["EXPECTED_COLUMNS", "SCHEMA_VERSION", "adapt_smipoly"]
