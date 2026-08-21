# ruff: noqa: E501
from __future__ import annotations

import math

import httpx
import pytest

from aegis.config import Settings
from aegis.embeddings import FixtureEmbeddings, OpenAIEmbeddings


def _vector(index: int = 0) -> list[float]:
    value = [0.0] * 1024
    value[index] = 1.0
    return value


def _provider(payload: object) -> OpenAIEmbeddings:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    return OpenAIEmbeddings(
        Settings(openai_api_key="test-key", openai_base_url="https://embeddings.test/v1"),
        client=httpx.Client(transport=transport),
    )


def test_openai_embeddings_orders_shuffled_data_by_index() -> None:
    provider = _provider({"data": [{"index": 1, "embedding": _vector(1)}, {"index": 0, "embedding": _vector(0)}]})

    assert provider.embed(["first", "second"]) == [_vector(0), _vector(1)]


@pytest.mark.parametrize(
    ("payload", "inputs"),
    [
        ({"data": [{"index": 0, "embedding": _vector()}]}, ["first", "second"]),
        ({"data": [{"index": 0, "embedding": [1.0]}]}, ["first"]),
        (
            {"data": [{"index": 0, "embedding": [math.nan, *_vector()[1:]]}]},
            ["first"],
        ),
    ],
)
def test_openai_embeddings_rejects_malformed_vectors(
    payload: object, inputs: list[str]
) -> None:
    with pytest.raises(ValueError):
        _provider(payload).embed(inputs)


def test_fixture_embeddings_requires_exact_text_and_returns_committed_neighbourhood() -> None:
    provider = FixtureEmbeddings({"known": _vector(7)})

    assert provider.embed(["known"]) == [_vector(7)]
    with pytest.raises(ValueError, match="exact text"):
        provider.embed(["unknown"])
