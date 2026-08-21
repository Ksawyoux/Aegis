"""Validated embedding-provider implementations."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import httpx

from aegis.config import Settings


class EmbeddingProvider(Protocol):
    dim: int
    model_fingerprint: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FixtureEmbeddings:
    """Deterministic committed vectors keyed by exact input text."""

    def __init__(
        self, vectors: Mapping[str, Sequence[float]], *, fingerprint: str = "fixture:1024"
    ) -> None:
        self._vectors = {key: list(value) for key, value in vectors.items()}
        self.dim = len(next(iter(self._vectors.values()))) if self._vectors else 1024
        self.model_fingerprint = fingerprint
        for vector in self._vectors.values():
            _validate_vector(vector, self.dim)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            return [list(self._vectors[text]) for text in texts]
        except KeyError as error:
            raise ValueError(f"fixture has no vector for exact text {error.args[0]!r}") from error


class OpenAIEmbeddings:
    """OpenAI ``text-embedding-3-small`` provider with response validation."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if settings.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")
        self._settings = settings
        self._client = client or httpx.Client(timeout=60.0)
        self.dim: int = settings.embedding_dim
        self.model_fingerprint = f"openai:{settings.embedding_model}:{self.dim}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if any(len(text.split()) > 8191 for text in texts):
            raise ValueError("embedding input exceeds 8191 token limit")
        result: list[list[float]] = []
        for offset in range(0, len(texts), 128):
            result.extend(self._batch(texts[offset : offset + 128]))
        return result

    def _batch(self, texts: Sequence[str]) -> list[list[float]]:
        key = self._settings.openai_api_key
        assert key is not None
        for attempt in range(3):
            try:
                response = self._client.post(
                    self._settings.openai_base_url.rstrip("/") + "/embeddings",
                    headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                    json={
                        "model": self._settings.embedding_model,
                        "input": list(texts),
                        "dimensions": self.dim,
                        "encoding_format": "float",
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise _RetryableResponse()
                response.raise_for_status()
                return self._parse(response.json(), len(texts))
            except (httpx.TransportError, _RetryableResponse):
                if attempt == 2:
                    raise
                time.sleep(2**attempt / 10)
        raise AssertionError("unreachable")

    def _parse(self, payload: Any, count: int) -> list[list[float]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("malformed embeddings response")
        rows = payload["data"]
        indexed: dict[int, list[float]] = {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("index"), int)
                or not isinstance(row.get("embedding"), list)
            ):
                raise ValueError("malformed embeddings response")
            vector = row["embedding"]
            if not all(
                isinstance(item, int | float) and not isinstance(item, bool) for item in vector
            ):
                raise ValueError("embedding has non-numeric component")
            indexed[row["index"]] = [float(item) for item in vector]
        if set(indexed) != set(range(count)):
            raise ValueError("embeddings response indices do not match inputs")
        ordered = [indexed[index] for index in range(count)]
        for vector in ordered:
            _validate_vector(vector, self.dim)
        return ordered


class _RetryableResponse(Exception):
    pass


def _validate_vector(vector: Sequence[float], dim: int) -> None:
    if len(vector) != dim or not all(math.isfinite(item) for item in vector):
        raise ValueError("invalid embedding vector")
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0 or not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise ValueError("embedding vector must be non-zero unit-normalized")
