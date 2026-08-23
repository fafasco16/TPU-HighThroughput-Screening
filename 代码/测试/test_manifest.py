import csv
import hashlib
from pathlib import Path

import pytest
import yaml

from manifest import (
    MANIFEST_COLUMNS,
    build_manifest,
    build_manifest_from_config,
    sha256_file,
    write_manifest_csv,
)


def _metadata(**overrides):
    values = {
        "source_id": "ds_fixture",
        "doi": "10.0000/fixture",
        "url": "https://example.test/data",
        "accessed_at": "2026-07-18",
        "license_spdx": "CC-BY-4.0",
        "derivatives_allowed": True,
        "redistribution_allowed": True,
        "access_restriction": "open",
        "evidence_grade": "measured_raw",
        "material_scope": "linear_tpu",
        "status": "available",
        "notes": "fixture",
    }
    values.update(overrides)
    return values


def test_sha256_is_streamed_and_changes_when_one_byte_changes(tmp_path, monkeypatch):
    path = tmp_path / "大文件.bin"
    path.write_bytes((b"abc123" * 300_000) + b"A")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    def fail_read_bytes(_self):
        raise AssertionError("sha256_file must not use Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    first = sha256_file(path, chunk_size=1024)
    assert first == expected

    with path.open("r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"B")
    assert sha256_file(path, chunk_size=1024) != first


@pytest.mark.parametrize("chunk_size", [0, -1, True, 1.5])
def test_sha256_rejects_invalid_chunk_sizes(tmp_path, chunk_size):
    path = tmp_path / "file.bin"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="chunk_size"):
        sha256_file(path, chunk_size=chunk_size)


def test_manifest_paths_are_sorted_relative_posix_and_nested_git_is_pruned(tmp_path):
    project = tmp_path / "中文项目"
    raw = project / "数据/原始" / "外部数据"
    (raw / "子目录").mkdir(parents=True)
    (raw / "子目录" / "样品.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (raw / "根文件.txt").write_text("data", encoding="utf-8")
    (raw / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (raw / ".git" / "objects").mkdir(parents=True)
    (raw / ".git" / "objects" / "secret").write_bytes(b"git object")
    (raw / "nested" / ".GIT" / "refs").mkdir(parents=True)
    (raw / "nested" / ".GIT" / "refs" / "head").write_text("deadbeef")

    rows = build_manifest(project, raw, metadata=_metadata())

    assert rows
    assert all(list(row) == MANIFEST_COLUMNS for row in rows)
    paths = [row["raw_path"] for row in rows]
    assert paths == sorted(paths)
    assert paths == [
        "数据/原始/外部数据/.gitignore",
        "数据/原始/外部数据/子目录/样品.csv",
        "数据/原始/外部数据/根文件.txt",
    ]
    assert all(not Path(path).is_absolute() and "\\" not in path for path in paths)
    assert all("/.git/" not in path.casefold() for path in paths)


def test_manifest_is_stable_and_file_id_changes_with_content(tmp_path):
    project = tmp_path / "项目"
    raw = project / "数据"
    raw.mkdir(parents=True)
    sample = raw / "a.csv"
    sample.write_bytes(b"a")
    first = build_manifest(project, raw, metadata=_metadata())[0]
    second = build_manifest(project, raw, metadata=_metadata())[0]
    assert first == second
    assert first["source_file_id"].startswith("source_file_")

    sample.write_bytes(b"b")
    changed = build_manifest(project, raw, metadata=_metadata())[0]
    assert changed["sha256"] != first["sha256"]
    assert changed["source_file_id"] != first["source_file_id"]


def test_manifest_handles_a_single_file_and_safely_parses_flags(tmp_path):
    project = tmp_path / "项目"
    file_path = project / "数据/原始" / "基础数据" / "样本.csv"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("value\n1\n", encoding="utf-8")

    row = build_manifest(
        project,
        file_path,
        metadata=_metadata(
            derivatives_allowed="false",
            redistribution_allowed="yes",
        ),
    )[0]
    assert row["original_filename"] == "样本.csv"
    assert row["derivatives_allowed"] is False
    assert row["redistribution_allowed"] is True


def test_manifest_rejects_paths_outside_project_and_missing_source_id(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(ValueError, match="project_root"):
        build_manifest(project, outside, metadata=_metadata())

    inside = project / "inside.txt"
    inside.write_text("x")
    with pytest.raises(ValueError, match="source_id"):
        build_manifest(project, inside, metadata={})


def test_manifest_rejects_missing_or_ambiguous_inputs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(FileNotFoundError):
        build_manifest(project, project / "missing", metadata=_metadata())

    sample = project / "sample.txt"
    sample.write_text("x")
    with pytest.raises(ValueError, match="boolean"):
        build_manifest(
            project,
            sample,
            metadata=_metadata(derivatives_allowed="truthy"),
        )


def test_manifest_rejects_non_mapping_metadata_and_non_directory_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sample = project / "sample.txt"
    sample.write_text("x")
    with pytest.raises(ValueError, match="metadata"):
        build_manifest(project, sample, metadata=None)

    not_a_directory = tmp_path / "project.txt"
    not_a_directory.write_text("x")
    with pytest.raises(ValueError, match="directory"):
        build_manifest(not_a_directory, not_a_directory, metadata=_metadata())


def test_manifest_excludes_git_pointer_files_and_direct_git_directories(tmp_path):
    project = tmp_path / "project"
    raw = project / "raw"
    raw.mkdir(parents=True)
    (raw / ".git").write_text("gitdir: elsewhere")
    assert build_manifest(project, raw, metadata=_metadata()) == []

    (raw / ".git").unlink()
    git_directory = raw / ".git"
    git_directory.mkdir()
    (git_directory / "config").write_text("[core]")
    assert build_manifest(project, git_directory, metadata=_metadata()) == []


def test_build_manifest_from_config_supports_chinese_paths_and_writes_csv(tmp_path):
    project = tmp_path / "项目"
    data = project / "数据/原始" / "基础数据"
    data.mkdir(parents=True)
    (data / "样品.csv").write_text("value\n1\n", encoding="utf-8")
    config = project / "配置" / "数据源.yaml"
    config.parent.mkdir()
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": "v0.1",
                "sources": [
                    {
                        "path": "数据/原始/基础数据/样品.csv",
                        **_metadata(notes="中文路径"),
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    rows = build_manifest_from_config(project, config)
    assert len(rows) == 1
    assert rows[0]["notes"] == "中文路径"

    output = project / "配置/清单" / "来源清单.csv"
    write_manifest_csv(rows, output)
    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        written = list(csv.DictReader(stream))
    assert list(written[0]) == MANIFEST_COLUMNS
    assert written[0]["raw_path"].endswith("样品.csv")

    relative_config_rows = build_manifest_from_config(project, "配置/数据源.yaml")
    assert relative_config_rows == rows


@pytest.mark.parametrize(
    "config_data",
    [
        {},
        {"sources": "not-a-list"},
        {"sources": [{}]},
        {"sources": [{"path": "missing.csv", "source_id": "ds_missing"}]},
    ],
)
def test_build_manifest_from_config_rejects_invalid_registry(tmp_path, config_data):
    project = tmp_path / "project"
    project.mkdir()
    config = project / "config.yaml"
    config.write_text(
        yaml.safe_dump(config_data, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        build_manifest_from_config(project, config)


def test_registry_rejects_non_mapping_documents_entries_and_absolute_paths(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sample = project / "sample.txt"
    sample.write_text("x")
    invalid_documents = [
        ["not", "a", "mapping"],
        {"sources": ["not-a-mapping"]},
        {"sources": [{"path": str(sample.resolve()), **_metadata()}]},
    ]
    for index, document in enumerate(invalid_documents):
        config = project / f"invalid-{index}.yaml"
        config.write_text(yaml.safe_dump(document), encoding="utf-8")
        with pytest.raises(ValueError):
            build_manifest_from_config(project, config)


def test_write_manifest_rejects_rows_with_missing_columns(tmp_path):
    with pytest.raises(ValueError, match="missing columns"):
        write_manifest_csv([{"source_id": "ds_x"}], tmp_path / "manifest.csv")
