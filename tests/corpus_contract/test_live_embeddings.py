"""Live OpenAI embedding acceptance for the committed postmortem corpus (Part 4 §3.5).

This is the one test in the suite that proves the embedding *provider* is
reachable, rather than merely configured -- ``/healthz`` deliberately does not
make a network call (Part 4 §0.2), so this is where a live rate limit,
authentication failure, or dimension mismatch actually surfaces. Under
``AEGIS_REQUIRE_LIVE_EVAL=1`` it must not fall back to fixture vectors or skip
after an authentication failure; ``tests/eval/conftest.py`` turns any skip
under ``tests/corpus_contract/`` into a failed session when that flag is set,
so a silent skip here still fails ``make demo`` even though this module
itself only ever calls ``pytest.skip``.

Part 2 owns the postmortem corpus and its embedding provider
(``aegis.ingest.embeddings`` or equivalent), and it has not landed in this
tree: there are no postmortem fixtures and no embedding call site to invoke.
The import below is therefore lazy and the absence is reported as a skip with
an explicit reason naming the missing dependency, not a stub implementation.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; this test makes live, billed OpenAI calls",
)


def test_committed_postmortems_embed_and_retrieve_above_the_similarity_floor() -> None:
    """Embed the corpus live, then verify known error signatures retrieve it.

    1. Embed every committed postmortem chunk with the real OpenAI API.
    2. Every response index is accounted for, and every vector is finite,
       non-zero, and exactly 1024 dimensions (the frozen ``vector(1024)``
       column, per Part 2 revision 3's Matryoshka truncation).
    3. Query each known error signature and verify its intended postmortem is
       retrieved above the configured similarity floor.
    """
    import importlib.util  # noqa: PLC0415

    if importlib.util.find_spec("aegis.ingest.embeddings") is None:
        pytest.skip(
            "aegis.ingest.embeddings (Part 2's postmortem embedding provider) is not "
            "present in this tree; Part 2 has not landed here. This test cannot exercise "
            "live OpenAI retrieval until it does."
        )
        return

    pytest.skip(
        "Part 2's postmortem corpus fixtures are not present in this tree; there is "
        "nothing to embed yet even though the provider module was importable."
    )
