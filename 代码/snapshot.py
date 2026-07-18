"""确定性 Parquet、DuckDB 与快照清单工具。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def normalize_frame(
    frame: pd.DataFrame, sort_by: Sequence[str] | None = None
) -> pd.DataFrame:
    normalized = frame.copy()
    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    keys = list(sort_by or [])
    if keys:
        missing = [key for key in keys if key not in normalized.columns]
        if missing:
            raise ValueError(f"排序字段不存在: {missing}")
        normalized = normalized.sort_values(keys, kind="mergesort", na_position="last")
    return normalized.reset_index(drop=True)


def write_parquet_deterministic(
    frame: pd.DataFrame,
    path: str | Path,
    sort_by: Sequence[str] | None = None,
) -> dict[str, object]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_frame(frame, sort_by)
    table = pa.Table.from_pandas(normalized, preserve_index=False)
    pq.write_table(
        table,
        target,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
        data_page_version="1.0",
    )
    return {
        "path": target.as_posix(),
        "rows": len(normalized),
        "columns": list(normalized.columns),
        "sha256": sha256_file(target),
        "size_bytes": target.stat().st_size,
    }


def build_duckdb(
    database_path: str | Path, tables: Mapping[str, str | Path]
) -> None:
    target = Path(database_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    connection = duckdb.connect(str(target))
    try:
        for table_name, parquet_path in sorted(tables.items()):
            safe_name = table_name.replace('"', '""')
            escaped_path = str(Path(parquet_path).resolve()).replace("'", "''")
            connection.execute(
                f'CREATE TABLE "{safe_name}" AS SELECT * FROM read_parquet(\'{escaped_path}\')'
            )
    finally:
        connection.close()


def write_snapshot_manifest(
    path: str | Path, payload: Mapping[str, object]
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target.write_text(text, encoding="utf-8", newline="\n")

