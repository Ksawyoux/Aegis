"""Best-effort background reviews of landed commits and pull requests.

A review failure must never fail the ingest response that scheduled it: the
webhook's contract with GitHub is about evidence ingestion, and the review is
an added observation layered on top. Every function here swallows its own
errors into logs after persisting nothing -- a missing review is visible in
the dashboard as an absence, not as a broken webhook.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from aegis.db.models import CodeReview, Service
from aegis.review.engine import analyze_unified_diff, fetch_commit_patch, fetch_pr_patch

LOGGER = logging.getLogger(__name__)


def split_owner_repo(repo: str) -> tuple[str, str]:
    """Split ``owner/name`` (already normalized) into API path components."""
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise ValueError(f"repo must use owner/name form, got {repo!r}")
    return owner, name


def _persist_review(
    session: Session,
    *,
    sha: str,
    source: str,
    pr_number: int | None,
    service_id: int | None,
    verdict: str,
    files_changed: int,
    additions: int,
    deletions: int,
    findings: list[dict[str, object]],
) -> None:
    """Upsert one review keyed by sha: re-reviewing a sha replaces its verdict."""
    statement = (
        pg_insert(CodeReview)
        .values(
            sha=sha,
            source=source,
            pr_number=pr_number,
            service_id=service_id,
            verdict=verdict,
            files_changed=files_changed,
            additions=additions,
            deletions=deletions,
            findings=findings,
        )
        .on_conflict_do_update(
            index_elements=[CodeReview.sha],
            set_={
                "verdict": verdict,
                "files_changed": files_changed,
                "additions": additions,
                "deletions": deletions,
                "findings": findings,
            },
        )
    )
    session.execute(statement)


def _service_id_by_name(session: Session, name: str | None) -> int | None:
    if not name:
        return None
    return session.scalar(select(Service.id).where(Service.name == name))


def review_commit(engine: Engine, *, repo: str, sha: str, service_name: str | None) -> None:
    """Review one landed commit and persist its findings. Never raises."""
    try:
        owner, name = split_owner_repo(repo)
        result = analyze_unified_diff(fetch_commit_patch(owner, name, sha))
        findings = [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "path": f.path,
                "line": f.line,
                "message": f.message,
                "evidence": f.evidence,
            }
            for f in result.findings
        ]
        with Session(engine) as session, session.begin():
            _persist_review(
                session,
                sha=sha,
                source="push",
                pr_number=None,
                service_id=_service_id_by_name(session, service_name),
                verdict=result.verdict,
                files_changed=result.stats.files_changed,
                additions=result.stats.additions,
                deletions=result.stats.deletions,
                findings=findings,
            )
        LOGGER.info(
            "review push %s: verdict=%s findings=%d", sha[:10], result.verdict, len(findings)
        )
    except Exception:  # noqa: BLE001 - best-effort by contract
        LOGGER.exception("background review failed for push %s", sha)


def review_pull_request(
    engine: Engine, *, repo: str, pr_number: int, head_sha: str, service_name: str | None
) -> None:
    """Review one PR's combined diff and persist under its head sha. Never raises."""
    try:
        owner, name = split_owner_repo(repo)
        result = analyze_unified_diff(fetch_pr_patch(owner, name, pr_number))
        findings = [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "path": f.path,
                "line": f.line,
                "message": f.message,
                "evidence": f.evidence,
            }
            for f in result.findings
        ]
        with Session(engine) as session, session.begin():
            _persist_review(
                session,
                sha=head_sha,
                source="pull_request",
                pr_number=pr_number,
                service_id=_service_id_by_name(session, service_name),
                verdict=result.verdict,
                files_changed=result.stats.files_changed,
                additions=result.stats.additions,
                deletions=result.stats.deletions,
                findings=findings,
            )
        LOGGER.info(
            "review pr#%d %s: verdict=%s findings=%d",
            pr_number,
            head_sha[:10],
            result.verdict,
            len(findings),
        )
    except Exception:  # noqa: BLE001 - best-effort by contract
        LOGGER.exception("background review failed for pr #%d", pr_number)


__all__ = ["review_commit", "review_pull_request", "split_owner_repo"]
