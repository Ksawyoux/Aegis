"""``POST /webhooks/github`` -- raw-body HMAC verification and push/PR ingest.

This is the only path where genuinely live evidence enters v1.0 (Part 4 §5).
It adapts external GitHub payloads into the existing ``GitExport``/``GitCommit``
ingest path (``aegis.ingest.git``); it does not give the agent direct GitHub
access, and it does not change evidence identity, citation syntax, or the
number of MCP tools.

Coordination note: this router is deliberately self-contained rather than
importing ``get_settings``/``get_engine`` from ``aegis.api.app``, because
Part 3 owns that module and it does not exist in this worktree yet. Instead
``get_settings``/``get_engine`` below read ``request.app.state.settings`` /
``request.app.state.engine`` -- the natural place for a lifespan-created
engine to live regardless of which router asks for it. Part 3's
``create_app`` is expected to set both on ``app.state`` during its lifespan
and then ``include_router(router)`` this module; if it instead exposes its
own ``Depends(get_settings)`` / ``Depends(get_engine)`` callables, swapping
these two functions for those is the only reconciliation needed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from aegis.config import Settings
from aegis.db.models import Service
from aegis.ingest.git import GitExport, upsert_commits
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.textnorm import normalize_repo
from aegis.review.service import review_commit, review_pull_request

router = APIRouter()

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 2 * 1024 * 1024
_SUPPORTED_EVENTS = frozenset({"push", "pull_request"})


@dataclass(frozen=True)
class GitHubPushResult:
    delivery_id: str | None
    repo: str
    inserted: int
    unchanged: int


@dataclass(frozen=True)
class GitHubPullRequestResult:
    delivery_id: str | None
    repo: str
    pr_number: int
    updated_shas: tuple[str, ...]
    deferred_shas: tuple[str, ...]


class MalformedGitHubPayload(ValueError):
    """Required fields are missing, malformed, or the push array is truncated."""


class UnregisteredRepository(ValueError):
    """The payload's repository does not resolve to exactly one persisted service."""


class ConflictingPullRequestMetadata(ValueError):
    """A commit already carries a different, non-null PR number than the event."""


def get_settings(request: Request) -> Settings:
    """Read the application's configured settings from ``app.state``.

    Falls back to a fresh ``Settings()`` only if the hosting app never set
    ``app.state.settings`` -- production wiring is expected to set it in the
    lifespan handler alongside the engine.
    """
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else Settings()


def get_engine(request: Request) -> Engine:
    """Read the lifespan-created engine from ``app.state``."""
    engine = request.app.state.engine
    if not isinstance(engine, Engine):
        raise RuntimeError("app.state.engine is not configured")
    return engine


def verify_github_signature(*, body: bytes, supplied: str | None, secret: str) -> bool:
    """Verify ``X-Hub-Signature-256`` over the exact raw request bytes.

    Never parses and re-serializes JSON before comparing: whitespace, escape
    spellings, and object-key order are part of the signed byte sequence, and
    re-serialization would silently accept a payload GitHub never actually
    signed.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, supplied or "")


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    engine: Engine = Depends(get_engine),
) -> Response:
    # 1-2: a missing or blank secret must refuse unverified payloads, not
    # accept them -- the failure mode that looks like success.
    secret = (settings.github_webhook_secret or "").strip()
    if not secret:
        return JSONResponse(status_code=503, content={"detail": "webhook secret is not configured"})

    # 3: reject an advertised oversized body before reading it.
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > _MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "payload too large"})
        except ValueError:
            pass  # a malformed header is not this check's job to reject

    # 4: read the raw bytes exactly once.
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "payload too large"})

    # 5-7: verify over those exact bytes before anything else happens.
    supplied = request.headers.get("x-hub-signature-256")
    if not verify_github_signature(body=body, supplied=supplied, secret=secret):
        return JSONResponse(status_code=401, content={"detail": "invalid signature"})

    # 8-9: an authenticated but unsupported event (including ping) is
    # acknowledged without ever parsing or ingesting its payload.
    event = request.headers.get("x-github-event", "")
    delivery_id = request.headers.get("x-github-delivery")
    if event not in _SUPPORTED_EVENTS:
        logger.info(
            "github webhook: acknowledged unsupported event=%s delivery=%s", event, delivery_id
        )
        return Response(status_code=202)

    # 10: only now, on authenticated bytes, parse JSON.
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"detail": "invalid JSON"})
    if not isinstance(parsed, dict):
        return JSONResponse(status_code=400, content={"detail": "payload must be a JSON object"})

    try:
        if event == "push":
            push_result = _handle_push(engine, parsed, delivery_id=delivery_id)
            logger.info(
                "github webhook: push repo=%s inserted=%d unchanged=%d delivery=%s",
                push_result.repo,
                push_result.inserted,
                push_result.unchanged,
                delivery_id,
            )
            head_sha = parsed.get("after")
            if isinstance(head_sha, str) and _valid_sha(head_sha):
                background.add_task(
                    review_commit, engine, repo=push_result.repo, sha=head_sha,
                    service_name=_service_name_for_repo(engine, push_result.repo),
                )
            return JSONResponse(
                status_code=202,
                content={
                    "delivery_id": push_result.delivery_id,
                    "repo": push_result.repo,
                    "inserted": push_result.inserted,
                    "unchanged": push_result.unchanged,
                },
            )

        pr_result = _handle_pull_request(engine, parsed, delivery_id=delivery_id)
        logger.info(
            "github webhook: pull_request repo=%s pr=%d updated=%d deferred=%d delivery=%s",
            pr_result.repo,
            pr_result.pr_number,
            len(pr_result.updated_shas),
            len(pr_result.deferred_shas),
            delivery_id,
        )
        action = parsed.get("action")
        head_sha = (parsed.get("pull_request") or {}).get("head", {}).get("sha")
        reviewable_pr = (
            action in ("opened", "synchronize")
            and isinstance(head_sha, str)
            and _valid_sha(head_sha) is not None
        )
        if reviewable_pr:
            background.add_task(
                review_pull_request, engine, repo=pr_result.repo,
                pr_number=pr_result.pr_number, head_sha=head_sha,
                service_name=_service_name_for_repo(engine, pr_result.repo),
            )
        return JSONResponse(
            status_code=202,
            content={
                "delivery_id": pr_result.delivery_id,
                "repo": pr_result.repo,
                "pr_number": pr_result.pr_number,
                "updated_shas": list(pr_result.updated_shas),
                "deferred_shas": list(pr_result.deferred_shas),
            },
        )
    except (MalformedGitHubPayload, ValidationError, UnregisteredRepository) as error:
        return JSONResponse(status_code=422, content={"detail": str(error)})
    except ConflictingPullRequestMetadata as error:
        return JSONResponse(status_code=409, content={"detail": str(error)})


# --------------------------------------------------------------------------
# Push adapter (Part 4 §5.4)
# --------------------------------------------------------------------------


def github_push_export(payload: dict[str, Any]) -> GitExport:
    """Adapt a signed GitHub push payload into the existing ``GitExport`` shape.

    A push carries changed paths but never patch content, so every file is
    built with ``hunks=None`` -- ``files_changed_for_commit`` (Part 4 §0.3)
    then labels the omission ``"webhook"``, never ``"budget"``. ``deploys``
    is always empty: a push is commit evidence, not deployment evidence.
    """
    repository = payload.get("repository")
    if not isinstance(repository, dict) or not isinstance(repository.get("full_name"), str):
        raise MalformedGitHubPayload("push payload is missing repository.full_name")
    try:
        repo = normalize_repo(repository["full_name"])
    except (TypeError, ValueError) as error:
        raise MalformedGitHubPayload(f"invalid repository.full_name: {error}") from error

    raw_commits = payload.get("commits")
    if not isinstance(raw_commits, list):
        raise MalformedGitHubPayload("push payload is missing a commits array")

    _reject_truncated_push(payload, raw_commits)

    commits = [_map_push_commit(entry) for entry in raw_commits]
    return GitExport.model_validate({"repo": repo, "commits": commits, "deploys": []})


def _reject_truncated_push(payload: dict[str, Any], commits: list[Any]) -> None:
    """Best-effort truncation guard using ``head_commit`` when present.

    GitHub's push webhook payload has no explicit "total commits" counter to
    compare against the array length directly; ``head_commit`` is the one
    field that lets a truncated array be detected -- if it is present and the
    array's last entry does not match it, the array does not contain the
    actual head and claiming full ingest would be false.
    """
    head_commit = payload.get("head_commit")
    if not isinstance(head_commit, dict) or not commits:
        return
    head_id = head_commit.get("id")
    last_id = commits[-1].get("id") if isinstance(commits[-1], dict) else None
    if head_id is not None and last_id is not None and head_id != last_id:
        raise MalformedGitHubPayload(
            "push payload's commits array does not include head_commit; refusing a "
            "truncated push rather than silently claiming full ingest"
        )


def _map_push_commit(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise MalformedGitHubPayload("push commit entry must be an object")
    sha = entry.get("id")
    timestamp = entry.get("timestamp")
    message = entry.get("message")
    author = entry.get("author")
    if not isinstance(sha, str) or not isinstance(timestamp, str) or not isinstance(message, str):
        raise MalformedGitHubPayload(f"push commit is missing required fields: {entry!r}")
    author_name = author.get("name") if isinstance(author, dict) else None
    if not isinstance(author_name, str):
        raise MalformedGitHubPayload(f"push commit is missing author.name: {entry!r}")

    files_changed = _files_changed_for_push_commit(entry)
    return {
        "sha": sha,
        "author": author_name,
        "authored_at": timestamp,
        "committed_at": timestamp,
        "message": message,
        "pr_number": None,
        "files_changed": files_changed,
    }


def _files_changed_for_push_commit(entry: dict[str, Any]) -> list[dict[str, Any]]:
    seen_paths: set[str] = set()
    files: list[dict[str, Any]] = []
    for status, key in (("added", "added"), ("modified", "modified"), ("removed", "removed")):
        for path in _string_list(entry.get(key), key):
            if not path:
                raise MalformedGitHubPayload("push commit contains an empty changed path")
            if path in seen_paths:
                raise MalformedGitHubPayload(
                    f"path {path!r} appears in more than one of added/modified/removed"
                )
            seen_paths.add(path)
            files.append(
                {
                    "path": path,
                    "status": status,
                    "additions": 0,
                    "deletions": 0,
                    "hunks": None,
                }
            )
    return files


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MalformedGitHubPayload(f"push commit field {field!r} must be a list of strings")
    return value


def _handle_push(
    engine: Engine, payload: dict[str, Any], *, delivery_id: str | None
) -> GitHubPushResult:
    export = github_push_export(payload)
    with Session(engine) as session, session.begin():
        registry = _registry_from_session(session)
        resolution = registry.resolve_service(repo=export.repo)
        if not resolution.resolved:
            raise UnregisteredRepository(
                f"repository {export.repo!r} is not registered to exactly one persisted service"
            )
        counts = upsert_commits(session, export, registry)
    # The response is not returned until this transaction has committed
    # (the `with` block above exits before this line), so an immediate MCP
    # query for this commit sees it.
    return GitHubPushResult(
        delivery_id=delivery_id,
        repo=export.repo,
        inserted=counts.inserted,
        unchanged=counts.unchanged,
    )


# --------------------------------------------------------------------------
# Pull-request enrichment adapter (Part 4 §5.5)
# --------------------------------------------------------------------------

_SHA40 = frozenset("0123456789abcdef")


def _valid_sha(value: Any) -> str | None:
    if isinstance(value, str) and len(value) == 40 and set(value) <= _SHA40:
        return value
    return None


def enrich_pull_request(
    session: Session, payload: dict[str, Any], registry: ServiceRegistry
) -> GitHubPullRequestResult:
    """Enrich an already-ingested commit's nullable PR number.

    Never fabricates a commit: a ``pull_request`` payload carries no changed
    paths or patch text, so a truthful new row cannot be built from it. Every
    conflict rolls back the whole enrichment rather than partially applying.
    """
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    if not isinstance(repository, dict) or not isinstance(repository.get("full_name"), str):
        raise MalformedGitHubPayload("pull_request payload is missing repository.full_name")
    if not isinstance(pull_request, dict):
        raise MalformedGitHubPayload("pull_request payload is missing pull_request")
    pr_number = pull_request.get("number")
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
        raise MalformedGitHubPayload(f"invalid pull_request.number: {pr_number!r}")

    try:
        repo = normalize_repo(repository["full_name"])
    except (TypeError, ValueError) as error:
        raise MalformedGitHubPayload(f"invalid repository.full_name: {error}") from error

    resolution = registry.resolve_service(repo=repo)
    if not resolution.resolved:
        raise UnregisteredRepository(
            f"repository {repo!r} is not registered to a persisted service"
        )

    head = pull_request.get("head")
    head_sha = _valid_sha(head.get("sha")) if isinstance(head, dict) else None
    merge_sha = _valid_sha(pull_request.get("merge_commit_sha"))
    shas = [sha for sha in (head_sha, merge_sha) if sha is not None]
    ordered_shas = list(dict.fromkeys(shas))

    updated: list[str] = []
    deferred: list[str] = []
    for sha in ordered_shas:
        # A plain ``scalar_one_or_none()`` over ``pr_number`` cannot tell "no
        # commit row" from "commit row with a null pr_number" -- both come
        # back as Python ``None``. Fetching row presence separately from the
        # column value is what makes that distinction possible.
        row = session.execute(
            text(
                """
                SELECT c.pr_number
                FROM commits c JOIN services s ON s.id = c.service_id
                WHERE c.sha = :sha AND s.repo = :repo
                """
            ),
            {"sha": sha, "repo": repo},
        ).mappings().one_or_none()
        if row is None:
            deferred.append(sha)
            continue
        existing_pr_number = row["pr_number"]
        if existing_pr_number is not None and existing_pr_number != pr_number:
            raise ConflictingPullRequestMetadata(
                f"commit {sha} already carries pr_number={existing_pr_number}, "
                f"incoming event carries {pr_number}"
            )
        session.execute(
            text(
                """
                UPDATE commits AS c
                   SET pr_number = :pr_number
                  FROM services AS s
                 WHERE c.sha = :sha
                   AND c.service_id = s.id
                   AND s.repo = :repo
                   AND (c.pr_number IS NULL OR c.pr_number = :pr_number)
                """
            ),
            {"sha": sha, "repo": repo, "pr_number": pr_number},
        )
        updated.append(sha)

    return GitHubPullRequestResult(
        delivery_id=None,
        repo=repo,
        pr_number=pr_number,
        updated_shas=tuple(updated),
        deferred_shas=tuple(deferred),
    )


def _handle_pull_request(
    engine: Engine, payload: dict[str, Any], *, delivery_id: str | None
) -> GitHubPullRequestResult:
    with Session(engine) as session, session.begin():
        registry = _registry_from_session(session)
        result = enrich_pull_request(session, payload, registry)
    return GitHubPullRequestResult(
        delivery_id=delivery_id,
        repo=result.repo,
        pr_number=result.pr_number,
        updated_shas=result.updated_shas,
        deferred_shas=result.deferred_shas,
    )


def _registry_from_session(session: Session) -> ServiceRegistry:
    return ServiceRegistry.load(session.scalars(select(Service).order_by(Service.name)).all())


def _service_name_for_repo(engine: Engine, repo: str) -> str | None:
    """Resolve the service name for a registered repo; None when unregistered."""
    with Session(engine) as session:
        service = session.scalar(select(Service).where(Service.repo == repo))
        return service.name if service is not None else None


__all__ = [
    "ConflictingPullRequestMetadata",
    "GitHubPullRequestResult",
    "GitHubPushResult",
    "MalformedGitHubPayload",
    "UnregisteredRepository",
    "enrich_pull_request",
    "get_engine",
    "get_settings",
    "github_push_export",
    "github_webhook",
    "router",
    "verify_github_signature",
]
