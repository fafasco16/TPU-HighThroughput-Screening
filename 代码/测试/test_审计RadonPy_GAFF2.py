import pytest

import 审计RadonPy_GAFF2 as audit


def test_alternate_message_summary_counts_total_unique_and_stable_hash():
    text = "\n".join(
        [
            "RadonPy debug info: Using alternate bond type c,n instead of c,ns",
            "unrelated",
            "RadonPy debug info: Using alternate bond type c,n instead of c,ns",
            "RadonPy debug info: Using alternate angle type n,c,o instead of ns,c,o",
        ]
    )
    result = audit.summarize_alternate_messages(text)
    assert result["alternate_parameter_line_count"] == 3
    assert result["alternate_parameter_unique_count"] == 2
    assert len(result["alternate_parameter_unique_sha256"]) == 64


def test_audit_requires_radonpy_when_not_installed():
    if audit.radonpy_available():
        pytest.skip("本环境已安装RadonPy")
    with pytest.raises(RuntimeError, match="RadonPy"):
        audit.audit_graph_table(None)
