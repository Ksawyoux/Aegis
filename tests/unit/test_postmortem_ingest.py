# ruff: noqa: E501
from __future__ import annotations

import pytest

from aegis.ingest.postmortems import PostmortemIngestError, _chunks, _front_matter


def test_front_matter_and_sections_preserve_resolution_as_a_distinct_chunk() -> None:
    front, body = _front_matter(
        "---\ntitle: Pool exhaustion\noccurred_at: 2026-08-19T14:00:00Z\nservices: [payments-api]\n---\n"
        "## Symptoms\npool exhausted\n\n## Resolution\nraise pool size\n"
    )
    chunks, resolution = _chunks(body, 100)

    assert front["title"] == "Pool exhaustion"
    assert chunks == [("section", "## Symptoms\npool exhausted"), ("resolution", "## Resolution\nraise pool size")]
    assert resolution == "## Resolution\nraise pool size\n"


@pytest.mark.parametrize(
    "body",
    [
        "## Resolution\na\n\n## Resolution\nb",
        "## Resolution\n" + "word " * 4,
    ],
)
def test_invalid_resolution_shape_is_rejected(body: str) -> None:
    with pytest.raises(PostmortemIngestError):
        _chunks(body, 3)
