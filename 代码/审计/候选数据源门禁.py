"""候选数据源的只读、失败关闭门禁。

本脚本只评审候选元数据，不联网、不下载、不写原始数据，也不创建训练集。
全量 ``配置/v0.2来源范围.yaml`` 是来源身份去重真值；当前49个独立贡献
来源仅是数据规模口径，不能用于判断一个候选是否真正新增。

运行：

    python 代码/审计/候选数据源门禁.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PROJECT_ROOT / "配置" / "候选数据源.yaml"
DEFAULT_SOURCE_SCOPE = PROJECT_ROOT / "配置" / "v0.2来源范围.yaml"

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
ZENODO_PATTERN = re.compile(r"^(?:zenodo:|zenodo\.)(\d+)$", re.IGNORECASE)
CHECKSUM_PATTERN = re.compile(r"^(md5|sha256):([0-9a-f]+)$")
ALLOWED_DEDUP_STATES = {
    "new",
    "existing_governance_source",
    "supplement_of_existing",
    "mirror_of_existing",
}
ALLOWED_RIGHTS_STATES = {
    "open_redistributable",
    "open_noncommercial",
    "restricted_internal_research",
    "unknown_fail_closed",
}
SCORE_KEYS = (
    "provenance",
    "rights",
    "file_access",
    "data_granularity",
    "tpu_relevance",
    "gap_value",
    "reproducibility",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML根节点必须是映射: {path}")
    return payload


def normalize_identifier(value: object) -> str:
    """把 DOI URL、DOI 前缀和 Zenodo 短标识规范到可比较形式。"""

    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    text = text.rstrip("/.,; ")
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    zenodo = ZENODO_PATTERN.fullmatch(text)
    if zenodo:
        return f"doi:10.5281/zenodo.{zenodo.group(1)}"
    if DOI_PATTERN.fullmatch(text):
        return f"doi:{text}"
    return text


def _identifier_fields(collection: str, row: dict[str, Any]) -> list[object]:
    values: list[object] = []
    if collection in {"sources", "scopes"}:
        values.append(row.get("canonical_identifier"))
    elif collection == "citations":
        for field in ("canonical_identifier", "doi", "identifier"):
            values.append(row.get(field))
    return [value for value in values if value not in (None, "")]


def build_existing_identifier_index(
    source_payload: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    """索引全量来源、范围和引用，而不是只索引数据规模总账。"""

    index: dict[str, list[dict[str, str]]] = {}
    for collection in ("sources", "scopes", "citations"):
        rows = source_payload.get(collection, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = str(
                row.get("source_key")
                or row.get("source_scope_key")
                or row.get("citation_key")
                or ""
            )
            for raw_identifier in _identifier_fields(collection, row):
                normalized = normalize_identifier(raw_identifier)
                if not normalized:
                    continue
                match = {
                    "collection": collection,
                    "identity": identity,
                    "source_key": str(row.get("source_key") or ""),
                    "scope_key": str(row.get("source_scope_key") or ""),
                    "raw_identifier": str(raw_identifier),
                }
                if match not in index.setdefault(normalized, []):
                    index[normalized].append(match)
    for matches in index.values():
        matches.sort(
            key=lambda row: (
                row["collection"],
                row["identity"],
                row["raw_identifier"],
            )
        )
    return dict(sorted(index.items()))


def _is_https_url(value: object) -> bool:
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname)


def _grade(score_total: int, thresholds: dict[str, Any]) -> str:
    a_min = int(thresholds.get("A", 28))
    b_min = int(thresholds.get("B", 21))
    if score_total >= a_min:
        return "A"
    if score_total >= b_min:
        return "B"
    return "C"


def evaluate_registry(
    registry_path: Path = DEFAULT_REGISTRY,
    source_scope_path: Path = DEFAULT_SOURCE_SCOPE,
) -> dict[str, Any]:
    registry = _load_yaml(Path(registry_path))
    source_payload = _load_yaml(Path(source_scope_path))
    existing_index = build_existing_identifier_index(source_payload)
    errors: list[str] = []

    def error(candidate_id: str, message: str) -> None:
        errors.append(f"{candidate_id}: {message}")

    if registry.get("schema_version") != "v0.2":
        errors.append("registry: schema_version必须为v0.2")
    if registry.get("registry_state") != "candidate_only":
        errors.append("registry: registry_state必须为candidate_only")
    for field, expected in (
        ("training_split_created", False),
        ("training_weight_materialized", False),
        ("model_ready_record_count", 0),
    ):
        if registry.get(field) != expected:
            errors.append(f"registry: {field}必须保持为{expected!r}")

    dimension_max = registry.get("score_dimensions", {})
    if not isinstance(dimension_max, dict) or set(dimension_max) != set(SCORE_KEYS):
        errors.append("registry: score_dimensions必须精确包含七项固定维度")
        dimension_max = {key: 5 for key in SCORE_KEYS}
    thresholds = registry.get("grade_thresholds", {})
    if thresholds != {"A": 28, "B": 21, "C": 0}:
        errors.append("registry: A/B/C阈值必须为28/21/0")
    allowed_rights = set(registry.get("allowed_rights_statuses_for_download", []))
    allowed_hosts = {
        str(host).strip().lower()
        for host in registry.get("allowed_download_hosts", [])
        if str(host).strip()
    }

    candidates = registry.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        errors.append("registry: candidates必须是非空列表")
        candidates = []

    seen_ids: set[str] = set()
    seen_canonical: set[str] = set()
    report_rows: list[dict[str, Any]] = []
    for position, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            error(f"row_{position}", "候选必须是映射")
            continue
        candidate_id = str(candidate.get("candidate_id") or f"row_{position}")
        if not re.fullmatch(r"[a-z0-9_]+", candidate_id):
            error(candidate_id, "candidate_id只允许小写字母、数字和下划线")
        if candidate_id in seen_ids:
            error(candidate_id, "candidate_id重复")
        seen_ids.add(candidate_id)

        canonical = normalize_identifier(candidate.get("canonical_identifier"))
        if not canonical:
            error(candidate_id, "canonical_identifier缺失")
        if canonical in seen_canonical:
            error(candidate_id, f"规范身份重复: {canonical}")
        seen_canonical.add(canonical)
        matches = existing_index.get(canonical, [])
        expected_dedup = "existing_governance_source" if matches else "new"

        dedup = candidate.get("dedup")
        if not isinstance(dedup, dict):
            error(candidate_id, "dedup必须是映射")
            dedup = {}
        declared_dedup = str(dedup.get("state") or "")
        if declared_dedup not in ALLOWED_DEDUP_STATES:
            error(candidate_id, f"未知dedup.state: {declared_dedup}")
        if declared_dedup != expected_dedup:
            error(
                candidate_id,
                f"应标记为{expected_dedup}而不是{declared_dedup or '<missing>'}",
            )
        independent = dedup.get("independent_source_contribution")
        expected_independent = expected_dedup == "new"
        if independent is not expected_independent:
            error(
                candidate_id,
                "independent_source_contribution与全量来源去重结果不一致",
            )
        if matches:
            actual_source_keys = {
                match["source_key"] for match in matches if match["source_key"]
            }
            actual_scope_keys = {
                match["scope_key"] for match in matches if match["scope_key"]
            }
            declared_source_keys = set(dedup.get("matched_source_keys") or [])
            declared_scope_keys = set(dedup.get("matched_scope_keys") or [])
            if actual_source_keys and not declared_source_keys.intersection(actual_source_keys):
                error(
                    candidate_id,
                    "existing_governance_source缺少实际matched_source_keys",
                )
            if actual_scope_keys and not (
                declared_scope_keys.intersection(actual_scope_keys)
                or declared_source_keys.intersection(actual_source_keys)
            ):
                error(
                    candidate_id,
                    "existing_governance_source缺少实际matched_scope_keys",
                )

        scores = candidate.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(SCORE_KEYS):
            error(candidate_id, "scores必须精确包含七项固定维度")
            scores = {key: 0 for key in SCORE_KEYS}
        checked_scores: dict[str, int] = {}
        for key in SCORE_KEYS:
            value = scores.get(key, 0)
            maximum = int(dimension_max.get(key, 5))
            if type(value) is not int or not 0 <= value <= maximum:
                error(candidate_id, f"scores.{key}必须是0到{maximum}的整数")
                value = 0
            checked_scores[key] = int(value)
        score_total = sum(checked_scores.values())
        grade = _grade(score_total, thresholds)

        rights = candidate.get("rights")
        if not isinstance(rights, dict):
            error(candidate_id, "rights必须是映射")
            rights = {}
        rights_status = str(rights.get("status") or "")
        if rights_status not in ALLOWED_RIGHTS_STATES:
            error(candidate_id, f"未知rights.status: {rights_status}")
        if not _is_https_url(rights.get("evidence_url")):
            error(candidate_id, "rights.evidence_url必须是HTTPS")
        if not _is_https_url(candidate.get("stable_url")):
            error(candidate_id, "stable_url必须是HTTPS")

        evidence_urls = candidate.get("evidence_urls", [])
        if not isinstance(evidence_urls, list) or not evidence_urls:
            error(candidate_id, "evidence_urls必须是非空列表")
            evidence_urls = []
        for evidence_url in evidence_urls:
            if not _is_https_url(evidence_url):
                error(candidate_id, f"非HTTPS证据URL: {evidence_url}")

        files = candidate.get("files", [])
        if not isinstance(files, list):
            error(candidate_id, "files必须是列表")
            files = []
        all_files_exact = bool(files)
        all_hosts_allowed = bool(files)
        seen_file_ids: set[str] = set()
        for file_row in files:
            if not isinstance(file_row, dict):
                error(candidate_id, "files元素必须是映射")
                all_files_exact = False
                all_hosts_allowed = False
                continue
            file_id = str(file_row.get("file_id") or "")
            if not file_id or file_id in seen_file_ids:
                error(candidate_id, f"file_id缺失或重复: {file_id!r}")
            seen_file_ids.add(file_id)
            size = file_row.get("size_bytes")
            if type(size) is not int or size <= 0:
                all_files_exact = False
            download_url = file_row.get("download_url")
            if not _is_https_url(download_url):
                error(candidate_id, f"文件{file_id}下载URL必须是HTTPS")
                all_hosts_allowed = False
            else:
                host = (urlsplit(str(download_url)).hostname or "").lower()
                if host not in allowed_hosts:
                    error(candidate_id, f"文件{file_id}主机未列入白名单: {host}")
                    all_hosts_allowed = False
            checksum = file_row.get("checksum")
            if checksum:
                match = CHECKSUM_PATTERN.fullmatch(str(checksum).lower())
                if not match or len(match.group(2)) != (32 if match.group(1) == "md5" else 64):
                    error(candidate_id, f"文件{file_id}校验和格式非法")

        vetoes = candidate.get("vetoes", [])
        if not isinstance(vetoes, list) or any(
            not isinstance(value, str) or not value for value in vetoes
        ):
            error(candidate_id, "vetoes必须是字符串列表")
            vetoes = ["invalid_vetoes"]
        download_eligible = bool(
            grade == "A"
            and expected_dedup == "new"
            and not vetoes
            and rights_status in allowed_rights
            and all_files_exact
            and all_hosts_allowed
        )
        report_rows.append(
            {
                "candidate_id": candidate_id,
                "canonical_identifier": canonical,
                "dedup_state": expected_dedup,
                "download_eligible": download_eligible,
                "grade": grade,
                "matched_governance_records": matches,
                "proposed_action": str(candidate.get("proposed_action") or ""),
                "rights_status": rights_status,
                "score_total": score_total,
                "scores": checked_scores,
                "vetoes": sorted(vetoes),
            }
        )

    report_rows.sort(key=lambda row: row["candidate_id"])
    errors.sort()
    grade_counts = {
        grade: sum(row["grade"] == grade for row in report_rows)
        for grade in ("A", "B", "C")
    }
    dedup_counts = {
        state: sum(row["dedup_state"] == state for row in report_rows)
        for state in ("new", "existing_governance_source")
    }
    return {
        "candidate_count": len(report_rows),
        "candidates": report_rows,
        "dedup_counts": dedup_counts,
        "download_eligible_count": sum(
            row["download_eligible"] for row in report_rows
        ),
        "error_count": len(errors),
        "errors": errors,
        "existing_identifier_count": len(existing_index),
        "grade_counts": grade_counts,
        "model_ready_record_count": registry.get("model_ready_record_count"),
        "registry_version": str(registry.get("registry_version") or ""),
        "training_split_created": registry.get("training_split_created"),
        "training_weight_materialized": registry.get(
            "training_weight_materialized"
        ),
        "valid": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--source-scope", type=Path, default=DEFAULT_SOURCE_SCOPE)
    parser.add_argument("--json", action="store_true", help="输出确定性JSON摘要")
    args = parser.parse_args(argv)
    report = evaluate_registry(args.registry, args.source_scope)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    if args.json:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            f"候选={report['candidate_count']} "
            f"可下载={report['download_eligible_count']} "
            f"错误={report['error_count']}"
        )
        for error_message in report["errors"]:
            print(f"- {error_message}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
