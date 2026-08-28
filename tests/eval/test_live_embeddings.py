"""Opt-in acceptance for the production OpenAI embedding space."""

from __future__ import annotations

import os

import pytest

from aegis.config import Settings
from aegis.embeddings import OpenAIEmbeddings

requires_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; live embedding acceptance is opt-in",
)


@requires_openai_key
def test_live_openai_embeddings_are_1024_dimensional_and_normalized() -> None:
    provider = OpenAIEmbeddings(Settings())
    vector = provider.embed(["database connection pool exhausted"])[0]

    assert len(vector) == 1024
    assert sum(component * component for component in vector) == pytest.approx(1.0, abs=1e-3)
