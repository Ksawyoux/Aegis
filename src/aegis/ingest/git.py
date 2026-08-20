"""Load and persist the committed Git evidence export.

Commits are immutable Git objects, whereas deployments share an identity across
their lifecycle.  Keeping those two policies separate makes an export replay
safe without silently accepting an invalid deployment transition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis.config import Settings
from aegis.db.models import Commit, Deployment
from aegis.ingest.identity import deployment_uid
from aegis.ingest.normalize import ServiceRegistry

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HUNK_HEADER_RE = re.compile(r"^@@", re.MULTILINE)
_DeploymentStatus = Literal["in_progress", "success", "failed", "rolled_back"]
_FileStatus = Literal["added", "modified", "removed", "renamed"]


def _as_utc(value: datetime) -> datetime:
    """Reject naive input and retain one deterministic timestamp representation."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


UTCDateTime: TypeAlias = datetime


class GitFileChange(BaseModel):
    """One changed file from the committed export."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    status: _FileStatus
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    hunks: str | None


class GitCommit(BaseModel):
    """The immutable portion of a Git commit used by Aegis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha: str
    author: str
    authored_at: UTCDateTime
    committed_at: UTCDateTime
    message: str
    pr_number: int | None
    files_changed: list[GitFileChange]

    @field_validator("sha")
    @classmethod
    def _valid_sha(cls, value: str) -> str:
        if not _SHA_RE.fullmatch(value):
            raise ValueError("sha must be a 40-character lowercase hexadecimal string")
        return value

    @field_validator("authored_at", "committed_at")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def _unique_file_paths(self) -> GitCommit:
        paths = [file.path for file in self.files_changed]
        if len(paths) != len(set(paths)):
            raise ValueError("files_changed paths must be unique within a commit")
        return self


class GitDeployment(BaseModel):
    """One mutable deployment lifecycle observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    commit_sha: str
    environment: str = Field(min_length=1)
    started_at: UTCDateTime
    finished_at: UTCDateTime | None
    status: _DeploymentStatus

    @field_validator("commit_sha")
    @classmethod
    def _valid_commit_sha(cls, value: str) -> str:
        if not _SHA_RE.fullmatch(value):
            raise ValueError("commit_sha must be a 40-character lowercase hexadecimal string")
        return value

    @field_validator("started_at", "finished_at")
    @classmethod
    def _aware_timestamp(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None


class GitExport(BaseModel):
    """The versioned, committed JSON export consumed by Git ingestion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repo: str = Field(min_length=1)
    commits: list[GitCommit]
    deploys: list[GitDeployment]


@dataclass(frozen=True)
class UpsertCounts:
    """The mutually exclusive outcomes of one upsert batch."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


class IllegalDeploymentTransition(ValueError):
    """A lifecycle observation attempts to move a deployment backwards or sideways."""

    def __init__(self, uid: str, from_status: str, to_status: str) -> None:
        self.uid = uid
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"illegal deployment transition for {uid}: {from_status} -> {to_status}")


def load_git_export(path: Path) -> GitExport:
    """Parse and validate a committed Git export JSON document."""
    return GitExport.model_validate_json(path.read_bytes())


def upsert_commits(
    session: Session,
    export: GitExport,
    registry: ServiceRegistry,
    *,
    settings: Settings | None = None,
) -> UpsertCounts:
    """Insert immutable commits, counting existing SHAs as unchanged.

    ``settings`` is optional to preserve the public three-argument call shape
    while allowing the hunk budget to come from an explicit runtime Settings
    object rather than a module-level default.
    """
    service_id = _service_id_for_export(export, registry)
    active_settings = settings or Settings()
    _validate_hunk_settings(active_settings)
    inserted = 0
    unchanged = 0

    for commit in export.commits:
        result = session.execute(
            insert(Commit)
            .values(
                sha=commit.sha,
                service_id=service_id,
                authored_at=commit.authored_at,
                committed_at=commit.committed_at,
                message=commit.message,
                author=commit.author,
                pr_number=commit.pr_number,
                files_changed=files_changed_for_commit(commit, active_settings),
                additions=sum(file.additions for file in commit.files_changed),
                deletions=sum(file.deletions for file in commit.files_changed),
            )
            .on_conflict_do_nothing(index_elements=[Commit.sha])
            .returning(Commit.sha)
        )
        if result.scalar_one_or_none() is None:
            unchanged += 1
        else:
            inserted += 1

    return UpsertCounts(inserted=inserted, unchanged=unchanged)


def upsert_deployments(
    session: Session,
    export: GitExport,
    registry: ServiceRegistry,
    settings: Settings,
) -> UpsertCounts:
    """Apply explicit deployment lifecycle transitions inside the caller transaction."""
    # Deployment timestamps deliberately come only from the export, never a commit.
    service_id = _service_id_for_export(export, registry)
    inserted = 0
    updated = 0
    unchanged = 0

    for deployment in export.deploys:
        uid = deployment_uid(
            commit_sha=deployment.commit_sha,
            environment=deployment.environment,
            started_at=deployment.started_at,
        )
        existing = session.scalar(
            select(Deployment).where(Deployment.uid == uid).with_for_update()
        )
        if existing is None:
            session.execute(
                insert(Deployment).values(
                    uid=uid,
                    service_id=service_id,
                    commit_sha=deployment.commit_sha,
                    environment=deployment.environment,
                    started_at=deployment.started_at,
                    finished_at=deployment.finished_at,
                    status=deployment.status,
                )
            )
            inserted += 1
            continue

        if existing.status == deployment.status and existing.finished_at == deployment.finished_at:
            unchanged += 1
            continue

        if _is_permitted_transition(existing.status, deployment.status):
            session.execute(
                update(Deployment)
                .where(Deployment.id == existing.id)
                .values(status=deployment.status, finished_at=deployment.finished_at)
            )
            updated += 1
            continue

        raise IllegalDeploymentTransition(uid, existing.status, deployment.status)

    return UpsertCounts(inserted=inserted, updated=updated, unchanged=unchanged)


def files_changed_for_commit(commit: GitCommit, settings: Settings) -> list[dict[str, object]]:
    """Return deterministic JSONB file evidence after applying whole-file hunk caps."""
    _validate_hunk_settings(settings)
    ranked = sorted(
        commit.files_changed,
        key=lambda file: (-(file.additions + file.deletions), file.path),
    )
    hunk_paths = {file.path for file in ranked[: settings.hunk_max_files]}
    files: list[dict[str, object]] = []
    for file in commit.files_changed:
        hunks = file.hunks
        omitted: str | None = None
        if file.path not in hunk_paths or _exceeds_hunk_budget(hunks, settings):
            hunks = None
            omitted = "budget"
        elif hunks is None:
            omitted = "webhook"
        files.append(
            {
                "path": file.path,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "hunks": hunks,
                "hunks_omitted": omitted,
            }
        )
    return sorted(files, key=lambda file: str(file["path"]))


def _exceeds_hunk_budget(hunks: str | None, settings: Settings) -> bool:
    if hunks is None:
        return False
    return (
        len(_HUNK_HEADER_RE.findall(hunks)) > settings.hunk_max_hunks_per_file
        or len(hunks.splitlines()) > settings.hunk_max_lines_per_file
    )


def _validate_hunk_settings(settings: Settings) -> None:
    values = (
        settings.hunk_max_files,
        settings.hunk_max_hunks_per_file,
        settings.hunk_max_lines_per_file,
    )
    if any(value < 0 for value in values):
        raise ValueError("hunk budget settings must be non-negative")


def _service_id_for_export(export: GitExport, registry: ServiceRegistry) -> int:
    resolution = registry.resolve_service(repo=export.repo)
    service = resolution.service
    service_id = getattr(service, "id", None)
    if service is None or isinstance(service_id, bool) or not isinstance(service_id, int):
        raise ValueError(f"git export repo {export.repo!r} does not resolve to a persisted service")
    return service_id


def _is_permitted_transition(from_status: str, to_status: str) -> bool:
    return (from_status == "in_progress" and to_status in {"success", "failed", "rolled_back"}) or (
        from_status == "success" and to_status == "rolled_back"
    )
