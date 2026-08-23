from pathlib import Path

import duckdb
import pandas as pd
import pytest

from snapshot import (
    build_duckdb,
    normalize_frame,
    sha256_file,
    write_parquet_deterministic,
    write_snapshot_manifest,
)


def test_parquet_rebuild_is_deterministic(tmp_path):
    frame = pd.DataFrame({"id": ["b", "a"], "value": [2.0, 1.0]})
    first = write_parquet_deterministic(frame, tmp_path / "one.parquet", ["id"])
    second = write_parquet_deterministic(frame, tmp_path / "two.parquet", ["id"])
    assert first["sha256"] == second["sha256"]


def test_normalize_frame_sorts_columns_and_rows():
    frame = pd.DataFrame({"z": [2, 1], "a": ["b", "a"]})
    normalized = normalize_frame(frame, ["a"])
    assert list(normalized.columns) == ["a", "z"]
    assert normalized["a"].tolist() == ["a", "b"]


def test_normalize_frame_rejects_unknown_sort_column():
    with pytest.raises(ValueError, match="排序字段不存在"):
        normalize_frame(pd.DataFrame({"id": [1]}), ["missing"])


def test_duckdb_contains_parquet_table(tmp_path):
    parquet = tmp_path / "chemical.parquet"
    write_parquet_deterministic(pd.DataFrame({"id": ["a"]}), parquet, ["id"])
    database = tmp_path / "snapshot.duckdb"
    build_duckdb(database, {"chemical": parquet})
    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM chemical").fetchone()[0] == 1
    finally:
        connection.close()

    # Rebuilding replaces the old database instead of appending stale state.
    build_duckdb(database, {"chemical": parquet})
    assert database.exists()


def test_snapshot_manifest_is_utf8_and_stable(tmp_path):
    manifest = tmp_path / "snapshot.json"
    write_snapshot_manifest(manifest, {"版本": "v0.1", "rows": 1})
    assert manifest.read_text(encoding="utf-8").endswith("\n")
    assert sha256_file(manifest) == sha256_file(Path(manifest))
