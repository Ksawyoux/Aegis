from __future__ import annotations

import os
import subprocess
import sys

from aegis.ingest.templates import mask, template_hash


def test_uppercase_hex_masks_like_lowercase() -> None:
    assert mask("value deadbeef") == "value <HEX>"
    assert mask("value DEADBEEF") == "value <HEX>"
    assert template_hash("value deadbeef") == template_hash("value DEADBEEF")


def test_eight_digit_decimal_is_num_not_hex() -> None:
    assert mask("10000000") == "<NUM>"
    assert mask("deadbeef") == "<HEX>"


def test_literal_placeholder_is_escaped() -> None:
    assert mask("error code <NUM>") == "error code <ESCNUM>"
    assert template_hash("error code <NUM>") != template_hash("error code 123")


def test_negative_duration_absorbs_sign() -> None:
    assert mask("completed in -10ms") == "completed in <DUR>"


def test_embedded_0x_masks() -> None:
    assert mask("abc0xdead") == "abc<HEX>"


def test_quoted_string_absorbs_every_inner_token() -> None:
    assert mask('message="request 2026-08-20T10:00:00Z id=deadbeef took 10ms"') == (
        "message=<STR>"
    )


def test_200_and_504_share_template_hash() -> None:
    assert template_hash("GET /health returned 200") == template_hash("GET /health returned 504")


def test_hash_is_128_bits() -> None:
    value = template_hash("an example 123")

    assert len(value) == 32
    assert int(value, 16) >= 0


def test_hash_stable_in_subprocess() -> None:
    code = (
        "from aegis.ingest.templates import template_hash; "
        "print(template_hash('an example 123'))"
    )
    env = os.environ | {"PYTHONHASHSEED": "random"}
    first = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, env=env, text=True
    ).stdout.strip()
    second = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, env=env, text=True
    ).stdout.strip()

    assert first == second


def test_leftmost_span_wins() -> None:
    assert mask("http://example.test/path/123") == "<URL>"


def test_cosmetic_variation_shares_hash_but_changed_word_does_not() -> None:
    first = "2026-08-20T10:00:00Z job 550e8400-e29b-41d4-a716-446655440000 took 10ms: 200"
    second = "2026-08-21T10:01:02Z job 123e4567-e89b-12d3-a456-426614174000 took 20ms: 504"
    changed_word = "2026-08-21T10:01:02Z task 123e4567-e89b-12d3-a456-426614174000 took 20ms: 504"

    assert template_hash(first) == template_hash(second)
    assert template_hash(first) != template_hash(changed_word)


def test_escaped_quote_inside_string_is_consumed() -> None:
    """An escaped quote must not end the STR span early.

    A doubled-backslash escape alternation leaves ``\\"`` unconsumed, which ends
    the match at the wrong place and leaks string content into the template.
    """
    message = 'msg="say ' + chr(92) + '"hi' + chr(92) + '" ok" end'
    assert mask(message) == "msg=<STR> end"


def test_escaped_quote_content_does_not_change_the_hash() -> None:
    """Two messages differing only inside a quoted string share one template."""
    first = 'msg="alpha ' + chr(92) + '"one' + chr(92) + '"" end'
    second = 'msg="beta ' + chr(92) + '"two' + chr(92) + '"" end'
    assert template_hash(first) == template_hash(second)
