"""DQ PUE 326 行变换基准的标量 staging 适配器。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ids import stable_id


SCHEMA_VERSION = "v0.1"
LINEAGE_FAMILY = "pue643_family"
EXPECTED_COLUMNS = (
    "SSID",
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
    "logEB",
    "logYM",
    "logTS",
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
        raise ValueError(f"PUE source must be CSV: {source_path}")
    frame = pd.read_csv(source_path, encoding="utf-8-sig")
    if tuple(frame.columns) != EXPECTED_COLUMNS:
        raise ValueError(
            "PUE header fingerprint mismatch: "
            f"expected {EXPECTED_COLUMNS!r}, got {tuple(frame.columns)!r}"
        )
    return frame


def adapt_pue326(
    path: str | Path,
    *,
    source_id: str,
    source_file_id: str,
) -> pd.DataFrame:
    """Preserve transformed scalar fields and lock every SSID to its mother lineage."""

    _require_provenance(source_id, source_file_id)
    frame = _read_csv(path)
    if frame.empty:
        raise ValueError("PUE source contains no records")

    ssids = frame["SSID"]
    if ssids.isna().any() or ssids.astype(str).str.strip().eq("").any():
        raise ValueError("PUE SSID contains blank values")
    if ssids.duplicated().any():
        raise ValueError("PUE SSID must be unique within one source file")
    ssids = ssids.astype(str)

    for column in EXPECTED_COLUMNS[1:]:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any() or not np.isfinite(numeric.to_numpy()).all():
            raise ValueError(f"PUE {column} must contain only finite numeric values")
        frame[column] = numeric

    lineage_ids = [
        stable_id("lineage_record", LINEAGE_FAMILY, ssid) for ssid in ssids
    ]
    metadata = pd.DataFrame(
        {
            "record_id": [
                stable_id("pue_record", source_file_id, ssid) for ssid in ssids
            ],
            "source_id": source_id,
            "source_file_id": source_file_id,
            "source_locator": [
                f"csv:row={row_number};SSID={ssid}"
                for row_number, ssid in enumerate(ssids, start=2)
            ],
            "extraction_method": "pandas.read_csv:utf-8-sig:explicit_header_map",
            "fidelity": "measured_summary_transformed",
            "schema_version": SCHEMA_VERSION,
            "lineage_family": LINEAGE_FAMILY,
            "lineage_record_id": lineage_ids,
            "split_group": [
                stable_id("split_group", lineage_id) for lineage_id in lineage_ids
            ],
        }
    )
    return pd.concat([metadata, frame.reset_index(drop=True)], axis=1)


__all__ = [
    "EXPECTED_COLUMNS",
    "LINEAGE_FAMILY",
    "SCHEMA_VERSION",
    "adapt_pue326",
]
