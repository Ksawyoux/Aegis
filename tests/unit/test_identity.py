from __future__ import annotations

import os
import subprocess
import sys

import pytest

from aegis.ingest import identity
from aegis.ingest.identity import uid
from aegis.ingest.textnorm import (
    normalize_raw,
    normalize_service,
    normalize_source_file,
)


def test_uid_delimiter_collision_impossible() -> None:
    """Changing delimiter encoding cannot merge distinct log identities."""
    first = uid("log", file="a", offset=1, raw="2|raw")
    second = uid("log", file="a|1", offset=2, raw="raw")

    assert first != second


def test_uid_distinguishes_none_from_empty_string() -> None:
    """Replacing a missing value with an empty string changes an identity."""
    assert uid("example", value=None) != uid("example", value="")


def test_uid_distinguishes_int_from_str() -> None:
    """Changing a typed numeric field into text changes an identity."""
    assert uid("example", value=1) != uid("example", value="1")


def test_uid_rejects_unknown_type() -> None:
    """Unsupported values cannot acquire an identity through repr serialization."""
    with pytest.raises(TypeError):
        uid("example", value=object())


def test_uid_stable_across_process_restarts() -> None:
    """Process hash randomization cannot affect a canonical JSON identity."""
    code = "from aegis.ingest.identity import uid; print(uid('example', value={'b': 2, 'a': 1}))"
    env = os.environ | {"PYTHONHASHSEED": "random"}
    first = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, env=env, text=True
    ).stdout.strip()
    second = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, env=env, text=True
    ).stdout.strip()

    assert first == second


def test_uid_changes_with_version_bump(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normalization-contract version change invalidates old identities."""
    original = identity.uid("example", value="stable")

    monkeypatch.setattr(identity, "UID_VERSION", identity.UID_VERSION + 1)

    assert identity.uid("example", value="stable") != original


def test_nfc_equivalent_source_paths_produce_the_same_uid() -> None:
    """Different Unicode compositions of one source path identify one line."""
    composed = normalize_source_file("logs/caf\u00e9.jsonl")
    decomposed = normalize_source_file("logs/cafe\u0301.jsonl")

    assert uid("log", file=composed, offset=4, raw="record") == uid(
        "log", file=decomposed, offset=4, raw="record"
    )


def test_crlf_and_lf_raw_records_produce_the_same_uid() -> None:
    """A platform line ending cannot change an otherwise identical log identity."""
    assert normalize_raw(b"record\r\n") == normalize_raw(b"record\n")
    assert uid("log", file="logs/app", offset=0, raw=normalize_raw(b"record\r\n")) == uid(
        "log", file="logs/app", offset=0, raw=normalize_raw(b"record\n")
    )


def test_service_name_case_is_preserved_in_identity() -> None:
    """Distinct registry service names must not be case-folded into one uid."""
    assert normalize_service("Payments") != normalize_service("payments")
    assert uid("log", service=normalize_service("Payments")) != uid(
        "log", service=normalize_service("payments")
    )
