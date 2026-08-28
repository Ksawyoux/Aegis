"""Markdown postmortem ingest and transactional embedding replacement."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aegis.db.models import Postmortem, PostmortemChunk
from aegis.embeddings import EmbeddingProvider


class PostmortemIngestError(ValueError):
    pass


def ingest_postmortem(
    session: Session, *, path: Path, provider: EmbeddingProvider, token_cap: int = 8191
) -> None:
    front, body = _front_matter(path.read_text(encoding="utf-8"))
    slug = path.stem
    title = _required_string(front, "title")
    services = front.get("services")
    if not isinstance(services, list) or not all(isinstance(item, str) for item in services):
        raise PostmortemIngestError("services must be a list of strings")
    occurred_at = _parse_datetime(front.get("occurred_at"))
    content_sha = hashlib.sha256((path.read_text(encoding="utf-8")).encode()).hexdigest()
    chunks, resolution = _chunks(body, token_cap)
    existing = session.scalar(select(Postmortem).where(Postmortem.slug == slug).with_for_update())
    if (
        existing is not None
        and existing.content_sha == content_sha
        and existing.model_fingerprint == provider.model_fingerprint
    ):
        return
    vectors = provider.embed([content for _, content in chunks])
    if len(vectors) != len(chunks):
        raise PostmortemIngestError("provider returned wrong embedding count")
    if existing is None:
        existing = Postmortem(
            slug=slug,
            title=title,
            occurred_at=occurred_at,
            services=services,
            body_md=body,
            resolution_md=resolution,
            content_sha=content_sha,
            model_fingerprint=provider.model_fingerprint,
        )
        session.add(existing)
        session.flush()
    else:
        session.execute(delete(PostmortemChunk).where(PostmortemChunk.postmortem_id == existing.id))
        session.flush()
        existing.title, existing.occurred_at, existing.services = title, occurred_at, services
        existing.body_md, existing.resolution_md = body, resolution
        existing.content_sha, existing.model_fingerprint = content_sha, provider.model_fingerprint
    for ordinal, ((kind, content), embedding) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            PostmortemChunk(
                postmortem_id=existing.id,
                ordinal=ordinal,
                kind=kind,
                content=content,
                embedding=embedding,
            )
        )


def _front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise PostmortemIngestError("postmortem requires YAML front matter")
    _, raw, body = text.split("---\n", 2)
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise PostmortemIngestError("front matter must be an object")
    return value, body


def _chunks(body: str, cap: int) -> tuple[list[tuple[str, str]], str | None]:
    sections = re.split(r"(?=^## )", body, flags=re.MULTILINE)
    resolution_sections = [section for section in sections if section.startswith("## Resolution")]
    if len(resolution_sections) > 1:
        raise PostmortemIngestError("only one Resolution heading is allowed")
    chunks: list[tuple[str, str]] = []
    resolution = resolution_sections[0] if resolution_sections else None
    if resolution is not None and len(resolution.split()) > cap:
        raise PostmortemIngestError("Resolution exceeds token cap")
    for section in sections:
        kind = "resolution" if section.startswith("## Resolution") else "section"
        paragraphs = section.split("\n\n")
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip()
            if len(candidate.split()) > cap and current:
                chunks.append((kind, current))
                current = paragraph
            else:
                current = candidate
        if current:
            chunks.append((kind, current))
    return chunks, resolution


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise PostmortemIngestError(f"{key} must be a string")
    return item


def _parse_datetime(value: object) -> datetime | None:
    """Accept the shapes YAML actually produces for an ISO-8601 front-matter value.

    An unquoted ``occurred_at: 2026-08-19T14:00:00Z`` is resolved by the YAML
    timestamp type into a ``datetime``, never a string, so requiring a string
    rejects the way a postmortem is naturally written.  A bare ``date`` is
    rejected rather than widened to midnight, because silently inventing a time
    would place the document in a rollup bucket nobody chose.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        raise PostmortemIngestError("occurred_at must include a time, not only a date")
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise PostmortemIngestError("occurred_at must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PostmortemIngestError("occurred_at must have a timezone")
    return parsed
