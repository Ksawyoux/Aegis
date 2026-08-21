"""Response-mapping tests for the OpenAI embedding provider.

The provider must pair each vector with its input by the response's ``index``
field. Pairing by position is the same class of defect as matching tool results
by list order instead of ``tool_use_id``: it is invisible whenever the provider
happens to answer in order, which is almost always.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from aegis.config import Settings
from aegis.embeddings.providers import OpenAIEmbeddings

_UNIT_A = [1.0] + [0.0] * 1023
_UNIT_B = [0.0] * 1023 + [1.0]


def _provider(payload: dict[str, Any]) -> OpenAIEmbeddings:
    response = Mock()
    response.status_code = 200
    response.raise_for_status = Mock()
    response.json = Mock(return_value=payload)
    client = Mock()
    client.post = Mock(return_value=response)
    return OpenAIEmbeddings(Settings(openai_api_key=SecretStr("sk-test")), client=client)


def test_vectors_are_paired_by_index_not_by_position() -> None:
    """A shuffled response must still map each vector to the right input.

    An already-ordered fixture passes whether or not the code reads ``index``,
    so the response is returned deliberately out of order.
    """
    provider = _provider(
        {"data": [{"embedding": _UNIT_B, "index": 1}, {"embedding": _UNIT_A, "index": 0}]}
    )

    vectors = provider.embed(["first", "second"])

    assert vectors[0] == _UNIT_A
    assert vectors[1] == _UNIT_B


def test_a_repeated_index_is_rejected_rather_than_overwriting_a_vector() -> None:
    """Indices [0, 0] satisfy a set comparison while losing one input entirely.

    Without an explicit check the second row overwrites the first, so input 0
    is silently embedded as input 1 and every similarity downstream is computed
    against the wrong text.
    """
    provider = _provider(
        {"data": [{"embedding": _UNIT_A, "index": 0}, {"embedding": _UNIT_B, "index": 0}]}
    )

    with pytest.raises(ValueError, match="repeated an index"):
        provider.embed(["first", "second"])


def test_a_row_count_mismatch_is_rejected() -> None:
    provider = _provider(
        {
            "data": [
                {"embedding": _UNIT_A, "index": 0},
                {"embedding": _UNIT_B, "index": 0},
                {"embedding": _UNIT_B, "index": 1},
            ]
        }
    )

    with pytest.raises(ValueError, match="different number of rows"):
        provider.embed(["first", "second"])


def test_a_missing_index_is_rejected() -> None:
    provider = _provider(
        {"data": [{"embedding": _UNIT_A, "index": 0}, {"embedding": _UNIT_B, "index": 5}]}
    )

    with pytest.raises(ValueError, match="indices do not match"):
        provider.embed(["first", "second"])
