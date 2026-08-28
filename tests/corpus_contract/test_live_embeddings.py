"""Live OpenAI embedding acceptance for the committed postmortem corpus.

This is the only test that proves the embedding provider is *reachable* rather
than merely configured. ``/healthz`` deliberately makes no network call, so a
live authentication failure, rate limit, or dimension mismatch surfaces here or
nowhere.

It is also the only test that can catch an inverted ranking. pgvector's ``<=>``
is cosine *distance* -- 0 is identical -- and a fixture suite passes perfectly
with the comparison reversed, because fixture vectors are chosen to produce the
expected answer either way. Retrieval against real embeddings cannot be fooled
that way: a semantically related query must come back closer than an unrelated
one, and it will not if the sign is wrong.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.config import Settings
from aegis.db.models import PostmortemChunk
from aegis.embeddings.providers import OpenAIEmbeddings
from aegis.ingest.postmortems import ingest_postmortem
from aegis.mcp_server.queries import search_similar_postmortems

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; this test makes live, billed OpenAI calls",
)

_CORPUS = Path("corpus/postmortems")


def _ingest_live(engine: Engine) -> OpenAIEmbeddings:
    provider = OpenAIEmbeddings(Settings())
    sources = sorted(_CORPUS.glob("*.md"))
    assert sources, "the committed postmortem corpus is empty"
    for source in sources:
        with Session(engine) as session, session.begin():
            ingest_postmortem(session, path=source, provider=provider)
    return provider


def test_committed_postmortems_embed_to_the_frozen_dimension(migrated_engine: Engine) -> None:
    _ingest_live(migrated_engine)

    with Session(migrated_engine) as session:
        embeddings = session.scalars(select(PostmortemChunk.embedding)).all()

    assert embeddings, "no chunks were stored"
    for vector in embeddings:
        # vector(1024) is frozen in migration 1, so a provider returning its
        # native 1536 width fails at insert rather than degrading quietly.
        assert len(vector) == 1024
        assert all(value == value and abs(value) != float("inf") for value in vector)
        assert any(value != 0.0 for value in vector)


def test_a_related_signature_retrieves_its_postmortem_above_an_unrelated_one(
    migrated_engine: Engine,
) -> None:
    """Ranking must be by cosine distance, and it must be the right way round."""
    provider = _ingest_live(migrated_engine)

    with Session(migrated_engine) as session:
        hits = search_similar_postmortems(
            session,
            error_signature="container terminated by the out of memory killer, exit code 137",
            provider=provider,
        )

    assert hits, "a clearly related signature retrieved nothing above the similarity floor"
    assert hits[0].slug == "container-memory-limits", (
        f"nearest postmortem was {hits[0].slug!r}; an inverted <=> comparison ranks the "
        "least related document first and still returns a non-empty result"
    )
    slugs = [hit.slug for hit in hits]
    if "cache-policy-regression" in slugs:
        assert slugs.index("container-memory-limits") < slugs.index("cache-policy-regression")


def test_an_unrelated_signature_does_not_retrieve_a_confident_match(
    migrated_engine: Engine,
) -> None:
    """The similarity floor must actually exclude something.

    Without this the floor could be zero and every query would return the whole
    corpus, which reads as working retrieval right up until the agent cites an
    unrelated incident.
    """
    provider = _ingest_live(migrated_engine)

    with Session(migrated_engine) as session:
        hits = search_similar_postmortems(
            session,
            error_signature="the quick brown fox jumps over the lazy dog",
            provider=provider,
        )

    # A floor that excludes everything is not a working floor, so the related
    # query is asserted alongside it: all(...) over an empty list is true, and
    # would pass just as happily if retrieval were broken outright.
    with Session(migrated_engine) as session:
        related = search_similar_postmortems(
            session,
            error_signature="container terminated by the out of memory killer, exit code 137",
            provider=provider,
        )
    assert related, "retrieval returned nothing even for a clearly related signature"

    assert all(hit.similarity < 0.75 for hit in hits), (
        f"unrelated text matched too strongly: {[(h.slug, h.similarity) for h in hits]}"
    )
    if hits:
        assert max(hit.similarity for hit in hits) < max(hit.similarity for hit in related)
