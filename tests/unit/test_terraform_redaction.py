"""Sensitive Terraform values must never reach the database in plaintext.

``attribute_diff`` is persisted and later handed to the agent as evidence, so a
leak here is a credential written to storage and then quoted into a summary.
The dictionary path was covered; lists were not, and Terraform marks
sensitivity element-wise inside them.
"""

from __future__ import annotations

import json
from typing import Any

from aegis.ingest.terraform import _diff, _merge_sensitive

_SECRET = "AKIA-NOT-A-REAL-CREDENTIAL"


def _attribute_diff(
    before: Any, after: Any, before_sensitive: Any, after_sensitive: Any
) -> dict[str, Any]:
    return _diff(before, after, None, _merge_sensitive(before_sensitive, after_sensitive))


def test_a_secret_inside_a_list_of_objects_is_redacted() -> None:
    diff = _attribute_diff(
        {"rules": [{"token": f"old-{_SECRET}"}]},
        {"rules": [{"token": f"new-{_SECRET}"}]},
        {"rules": [{"token": True}]},
        {"rules": [{"token": True}]},
    )

    assert _SECRET not in json.dumps(diff)
    assert diff["rules"] == {"before": "<redacted>", "after": "<redacted>"}


def test_a_secret_in_a_bare_list_is_redacted() -> None:
    diff = _attribute_diff({"k": [_SECRET]}, {"k": ["rotated"]}, {"k": [True]}, {"k": [True]})

    assert _SECRET not in json.dumps(diff)


def test_a_secret_nested_below_a_list_is_redacted() -> None:
    diff = _attribute_diff(
        {"a": {"b": [{"c": _SECRET}]}},
        {"a": {"b": [{"c": "rotated"}]}},
        {"a": {"b": [{"c": True}]}},
        {"a": {"b": [{"c": True}]}},
    )

    assert _SECRET not in json.dumps(diff)


def test_a_list_marked_sensitive_on_only_one_side_is_still_redacted() -> None:
    """A delete has ``after: null``, so only ``before_sensitive`` is populated."""
    diff = _attribute_diff({"k": [_SECRET]}, None, {"k": [True]}, None)

    assert _SECRET not in json.dumps(diff)


def test_an_ordinary_list_is_not_redacted() -> None:
    """Over-redaction would destroy the evidence the tool exists to surface."""
    diff = _attribute_diff({"ports": [80]}, {"ports": [443]}, None, None)

    assert diff["ports"] == {"before": [80], "after": [443]}


def test_an_unchanged_sensitive_list_produces_no_diff_entry() -> None:
    diff = _attribute_diff({"k": [_SECRET]}, {"k": [_SECRET]}, {"k": [True]}, {"k": [True]})

    assert diff == {}
