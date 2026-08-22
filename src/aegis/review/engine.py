"""Evidence-based code review over unified diffs.

Every finding carries the exact added line it matched -- no evidence, no
finding -- and severity reflects what the pattern can do in production, not
how suspicious it looks. Rules are deliberately deterministic: the same diff
always produces the same review.
"""

from __future__ import annotations

import re
import subprocess  # noqa: S404 - only used to shell out to gh when no token is set
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

Severity = str
VERDICT_FAIL = "fail"
VERDICT_WARN = "warn"
VERDICT_CLEAN = "clean"


@dataclass(frozen=True)
class ReviewFinding:
    """One flagged location, backed by the exact source line that triggered it."""

    rule_id: str
    severity: Severity
    path: str
    line: int
    message: str
    evidence: str
    remediation: str


@dataclass(frozen=True)
class DiffStats:
    files_changed: int
    additions: int
    deletions: int


@dataclass(frozen=True)
class ReviewResult:
    verdict: str
    stats: DiffStats
    findings: list[ReviewFinding]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    severity: Severity
    pattern: re.Pattern[str]
    message: str
    remediation: str
    paths: tuple[str, ...] | None = None


_RULES: tuple[Rule, ...] = (
    Rule("sec-aws-key", "high", re.compile(r"AKIA[0-9A-Z]{16}"),
         "AWS access key id committed to the repository.",
         "Revoke in IAM, purge history with git filter-repo, and load creds from env."),
    Rule("sec-private-key", "high",
         re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
         "A private key block was added to a source file.",
         "Rotate the key pair, remove the file, rewrite history; serve keys from a vault."),
    Rule("sec-provider-token", "high",
         re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|xox[baprs]-[0-9A-Za-z-]{10,}|sk-[A-Za-z0-9T_-]{32,})\b"),
         "A provider API token shape appears verbatim; treat as leaked and rotate.",
         "Revoke at the provider; inject via environment or CI secret instead of source."),
    Rule("sec-hardcoded-secret", "medium",
         re.compile(r"(?i)\b(password|passwd|api_key|apikey|secret)\b\s*[=:]\s*[\"'][^\"']{8,}[\"']"),
         "Hard-coded credential-shaped assignment; move to configuration or a secret store.",
         "Replace the literal with Settings()/os.environ and document it in .env.example."),
    Rule("sec-sql-fstring", "high",
         re.compile(r"execute\(\s*f[\"']|\.raw\(\s*f[\"']|text\(f[\"']"),
         "SQL composed from an f-string reaches execution unparameterized.",
         "Use bound parameters: text('... WHERE id = :id'), {'id': value}."),
    Rule("sec-eval", "high", re.compile(r"(?<![\w.])eval\("),
         "eval() on dynamic input executes attacker-controlled code paths.",
         "Use ast.literal_eval for data or json.loads for JSON; delete eval if possible."),
    Rule("sec-exec", "medium", re.compile(r"(?<![\w.])exec\("),
         "exec() on dynamic input is indistinguishable from remote code execution.",
         "Refactor to explicit functions or a dispatch table with allowlisted inputs."),
    Rule("sec-shell-true", "high", re.compile(r"shell\s*=\s*True"),
         "subprocess with shell=True interpolates arguments through a shell.",
         "Pass arguments as a list with shell=False, or shlex.join trusted fixed args."),
    Rule("sec-tls-disabled", "high", re.compile(r"verify\s*=\s*False"),
         "TLS certificate verification disabled; enables interception.",
         "Remove verify=False; trust a custom CA bundle rather than disabling checks."),
    Rule("sec-pickle", "medium", re.compile(r"pickle\.loads?\(|marshal\.loads\("),
         "Deserializing pickle/marshal data executes embedded bytecode.",
         "Exchange data as JSON; if pickle is required, only load signed internal blobs."),
    Rule("sec-cors-wildcard", "medium",
         re.compile(r"(?i)access-control-allow-origin[\"']?\s*[:,]\s*[\"']\*"),
         "Wildcard CORS origin exposes authenticated responses to any site.",
         "Echo an explicit origin allowlist; never pair wildcards with credentials."),
    Rule("test-skipped", "medium",
         re.compile(r"@pytest\.mark\.skip|\.skip\(\s*[\"']|\bit\.only\b|describe\.only\b"),
         "A test was skipped or focused; coverage silently narrows.",
         "Fix the flake, or file a tracked skip issue with an owner and expiry date."),
    Rule("debug-left-in", "low", re.compile(r"^\s*(console\.log\(|print\()"),
         "Debug output left in changed lines.",
         "Delete it or route through the structured logger at an appropriate level."),
    Rule("todo-introduced", "low", re.compile(r"\b(TODO|FIXME|HACK)\b"),
         "A TODO/FIXME marker was introduced; track it or resolve it.",
         "Open a ticket and reference its id in the comment, or resolve before merge."),
)


def analyze_unified_diff(diff_text: str) -> ReviewResult:
    """Run every rule over added lines of one unified diff."""
    findings: list[ReviewFinding] = []
    path = ""
    new_line = 0
    additions = 0
    deletions = 0
    files: set[str] = set()

    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[len("+++ b/"):]
            continue
        if raw.startswith("--- ") or raw.startswith("+++ "):
            continue
        if raw.startswith("diff --git"):
            match = re.match(r'diff --git a/(.*) b/(.*)$', raw)
            if match and not path:
                path = match.group(2)
            continue
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if raw.startswith("+"):
            additions += 1
            files.add(path)
            content = raw[1:]
            for rule in _RULES:
                if rule.paths is not None and not path.endswith(rule.paths):
                    continue
                hit = rule.pattern.search(content)
                if hit:
                    findings.append(
                        ReviewFinding(
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            path=path,
                            line=new_line,
                            message=rule.message,
                            evidence=content.strip()[:200],
                            remediation=rule.remediation,
                        )
                    )
            new_line += 1
        elif raw.startswith("-"):
            deletions += 1

    severities = {finding.severity for finding in findings}
    if "high" in severities:
        verdict = VERDICT_FAIL
    elif "medium" in severities:
        verdict = VERDICT_WARN
    else:
        verdict = VERDICT_CLEAN
    return ReviewResult(
        verdict=verdict,
        stats=DiffStats(files_changed=len(files), additions=additions, deletions=deletions),
        findings=findings,
    )


def _github_token(settings_token: str | None) -> str | None:
    """Prefer an explicit GITHUB_TOKEN, else fall back to the gh CLI keyring."""
    if settings_token:
        return settings_token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = result.stdout.strip()
    return token or None


def fetch_commit_patch(
    owner: str, repo: str, sha: str, *, token: str | None = None, client: httpx.Client | None = None
) -> str:
    """Fetch the unified diff of one commit via the GitHub commits API."""
    return _fetch_patch(f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}", token, client)


def fetch_pr_patch(
    owner: str,
    repo: str,
    number: int,
    *,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Fetch the combined unified diff of one pull request."""
    return _fetch_patch(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}", token, client
    )


def _fetch_patch(url: str, token: str | None, client: httpx.Client | None) -> str:
    headers = {"Accept": "application/vnd.github.diff"}
    active = token or _github_token(None)
    if active:
        headers["Authorization"] = f"Bearer {active}"
    owned = client is None
    active_client = client or httpx.Client(timeout=30.0)
    try:
        response = active_client.get(url, headers=headers)
        response.raise_for_status()
        return response.text
    finally:
        if owned:
            active_client.close()


def findings_to_json(findings: Sequence[ReviewFinding]) -> list[dict[str, Any]]:
    return [
        {
            "rule_id": finding.rule_id,
            "severity": finding.severity,
            "path": finding.path,
            "line": finding.line,
            "message": finding.message,
            "evidence": finding.evidence,
            "remediation": finding.remediation,
        }
        for finding in findings
    ]


__all__ = [
    "DiffStats",
    "ReviewFinding",
    "ReviewResult",
    "analyze_unified_diff",
    "fetch_commit_patch",
    "fetch_pr_patch",
    "findings_to_json",
]
