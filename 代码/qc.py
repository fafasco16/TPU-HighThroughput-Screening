"""数据库质量规则与结构化异常输出。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from licensing import may_publish


@dataclass(frozen=True)
class QualityIssue:
    rule_id: str
    severity: str
    table_name: str
    record_id: str
    message: str


def _record_id(row: pd.Series, keys: Sequence[str]) -> str:
    return "|".join(str(row.get(key, "")) for key in keys)


def check_required_columns(
    frame: pd.DataFrame, table_name: str, required: Iterable[str]
) -> list[QualityIssue]:
    missing = [column for column in required if column not in frame.columns]
    return [
        QualityIssue(
            "schema.required_column",
            "error",
            table_name,
            "",
            f"缺少必需字段: {column}",
        )
        for column in missing
    ]


def check_primary_key(
    frame: pd.DataFrame, table_name: str, keys: Sequence[str]
) -> list[QualityIssue]:
    if any(key not in frame.columns for key in keys):
        return check_required_columns(frame, table_name, keys)
    issues: list[QualityIssue] = []
    null_mask = frame[list(keys)].isna().any(axis=1)
    for _, row in frame.loc[null_mask].iterrows():
        issues.append(
            QualityIssue(
                "integrity.null_primary_key",
                "error",
                table_name,
                _record_id(row, keys),
                "主键包含空值",
            )
        )
    duplicate_mask = frame.duplicated(subset=list(keys), keep=False)
    for _, row in frame.loc[duplicate_mask].iterrows():
        issues.append(
            QualityIssue(
                "integrity.duplicate_primary_key",
                "error",
                table_name,
                _record_id(row, keys),
                "主键重复",
            )
        )
    return issues


def check_foreign_key(
    child: pd.DataFrame,
    parent: pd.DataFrame,
    child_table: str,
    child_column: str,
    parent_column: str,
) -> list[QualityIssue]:
    required = check_required_columns(child, child_table, [child_column])
    required += check_required_columns(parent, "parent", [parent_column])
    if required:
        return required
    parent_values = set(parent[parent_column].dropna())
    orphan_mask = child[child_column].notna() & ~child[child_column].isin(parent_values)
    return [
        QualityIssue(
            "integrity.orphan_foreign_key",
            "error",
            child_table,
            str(value),
            f"{child_column} 在父表中不存在",
        )
        for value in child.loc[orphan_mask, child_column]
    ]


def check_provenance(
    frame: pd.DataFrame, table_name: str
) -> list[QualityIssue]:
    columns = ["source_id", "source_file_id", "source_locator"]
    required = check_required_columns(frame, table_name, columns)
    if required:
        return required
    mask = frame[columns].isna().any(axis=1) | frame[columns].astype(str).eq("").any(axis=1)
    return [
        QualityIssue(
            "provenance.missing",
            "error",
            table_name,
            str(index),
            "来源追溯字段不完整",
        )
        for index in frame.index[mask]
    ]


def check_finite_values(
    frame: pd.DataFrame, table_name: str, columns: Sequence[str]
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for column in columns:
        if column not in frame.columns:
            issues.extend(check_required_columns(frame, table_name, [column]))
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        invalid = frame[column].notna() & ~np.isfinite(numeric)
        for index in frame.index[invalid]:
            issues.append(
                QualityIssue(
                    "value.non_finite",
                    "error",
                    table_name,
                    str(index),
                    f"{column} 不是有限数值",
                )
            )
    return issues


def check_lineage_split(
    frame: pd.DataFrame,
    table_name: str,
    lineage_column: str = "lineage_record_id",
    split_column: str = "split_group",
) -> list[QualityIssue]:
    required = check_required_columns(
        frame, table_name, [lineage_column, split_column]
    )
    if required:
        return required
    counts = frame.dropna(subset=[lineage_column]).groupby(lineage_column)[split_column].nunique()
    return [
        QualityIssue(
            "leakage.lineage_cross_split",
            "error",
            table_name,
            str(lineage_id),
            "同一母记录出现在多个拆分组",
        )
        for lineage_id in counts[counts > 1].index
    ]


def check_public_release(frame: pd.DataFrame, table_name: str) -> list[QualityIssue]:
    columns = [
        "license_spdx",
        "derivatives_allowed",
        "redistribution_allowed",
        "access_restriction",
        "source_status",
        "material_scope",
        "may_publish",
    ]
    required = check_required_columns(frame, table_name, columns)
    if required:
        return required
    license_gate = pd.Series(
        [
            may_publish(license_id, derivatives, redistribution)
            for license_id, derivatives, redistribution in zip(
                frame["license_spdx"],
                frame["derivatives_allowed"],
                frame["redistribution_allowed"],
                strict=True,
            )
        ],
        index=frame.index,
        dtype=bool,
    )
    allowed = (
        frame["may_publish"].eq(True)  # noqa: E712
        & license_gate
        & frame["source_status"].astype(str).str.strip().str.casefold().eq("available")
        & frame["access_restriction"].astype(str).str.strip().str.casefold().eq("open")
        & frame["material_scope"].notna()
        & frame["material_scope"].astype(str).str.strip().ne("")
    )
    blocked = ~allowed
    return [
        QualityIssue(
            "license.public_release_blocked",
            "error",
            table_name,
            str(index),
            "公开视图包含不允许发布的记录",
        )
        for index in frame.index[blocked]
    ]


def check_unresolved_units(
    frame: pd.DataFrame,
    table_name: str,
    *,
    status_column: str = "unit_status",
) -> list[QualityIssue]:
    """Emit one aggregate warning when source evidence leaves units unresolved."""

    required = check_required_columns(frame, table_name, [status_column])
    if required:
        return required
    unresolved = frame[status_column].astype(str).str.strip().str.casefold().eq(
        "unresolved"
    )
    count = int(unresolved.sum())
    if not count:
        return []
    return [
        QualityIssue(
            "unit.unresolved",
            "warning",
            table_name,
            f"count={count}",
            f"{count} 条记录缺少足够单位证据；许可可发布不等于模型就绪",
        )
    ]


def issues_frame(issues: Iterable[QualityIssue]) -> pd.DataFrame:
    columns = ["rule_id", "severity", "table_name", "record_id", "message"]
    records = [asdict(issue) for issue in issues]
    return pd.DataFrame(records, columns=columns)


def has_errors(issues: Iterable[QualityIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)
