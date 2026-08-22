"""Rule-engine unit tests over synthetic unified diffs."""

from __future__ import annotations

from aegis.review.engine import analyze_unified_diff

CLEAN_DIFF = """diff --git a/src/app.py b/src/app.py
index aaa..bbb 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@
 import json
 
+def add(a: int, b: int) -> int:
+    return a + b
 print = None
"""


def test_clean_code_produces_clean_verdict_with_no_findings() -> None:
    result = analyze_unified_diff(CLEAN_DIFF)

    assert result.verdict == "clean"
    assert result.findings == []
    assert result.stats.additions == 2
    assert result.stats.files_changed == 1


SECRET_DIFF = (
    "diff --git a/config.py b/config.py\n"
    "--- a/config.py\n"
    "+++ b/config.py\n"
    "@@ -10,3 +10,6 @@\n"
    "+AWS_ACCESS_KEY = \"AKIAIOSFODNN7EXAMPLE\"\n"
    "+token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n"
    "+result = eval(user_input)\n"
)


def test_secrets_and_eval_fail_the_review_with_line_evidence() -> None:
    result = analyze_unified_diff(SECRET_DIFF)

    assert result.verdict == "fail"
    rules = {finding.rule_id for finding in result.findings}
    assert {"sec-aws-key", "sec-provider-token", "sec-eval"} <= rules
    aws = next(f for f in result.findings if f.rule_id == "sec-aws-key")
    assert aws.path == "config.py"
    assert aws.line == 10
    assert "AKIAIOSFODNN7EXAMPLE" in aws.evidence
    assert "Revoke" in aws.remediation


MIXED_DIFF = (
    "diff --git a/tests/test_x.py b/tests/test_x.py\n"
    "--- a/tests/test_x.py\n"
    "+++ b/tests/test_x.py\n"
    "@@ -1,2 +1,4 @@\n"
    "+@pytest.mark.skip(reason=\"flaky\")\n"
    "+console.log('debugging');\n"
    "+const ok = 1;\n"
)


def test_skipped_test_warns_and_debug_log_stays_low() -> None:
    result = analyze_unified_diff(MIXED_DIFF)

    assert result.verdict == "warn"
    severities = {finding.rule_id: finding.severity for finding in result.findings}
    assert severities["test-skipped"] == "medium"
    assert severities["debug-left-in"] == "low"


def test_deleted_and_context_lines_never_produce_findings() -> None:
    diff = (
        "diff --git a/old.py b/old.py\n"
        "--- a/old.py\n"
        "+++ b/old.py\n"
        "@@ -1,3 +1,2 @@\n"
        "-AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        "-eval(x)\n"
        " context stays untouched\n"
    )

    result = analyze_unified_diff(diff)

    assert result.findings == []
    assert result.stats.deletions == 2
