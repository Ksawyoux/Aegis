from __future__ import annotations

from aegis.api.webhooks import derive_dedup_key


def test_provider_key_wins_over_fingerprint() -> None:
    assert derive_dedup_key(
        {
            "event": {"data": {"id": "provider-1"}},
            "dedup_key": "ignored",
            "service": "api",
            "alert_name": "errors",
            "fired_at": "2026-08-20T10:00:00Z",
        }
    ) == ("provider-1", "provider")


def test_fingerprint_changes_across_five_minute_buckets() -> None:
    common = {"service": "api", "alert_name": "errors"}
    first = derive_dedup_key({**common, "fired_at": "2026-08-20T10:04:00Z"})
    second = derive_dedup_key({**common, "fired_at": "2026-08-20T10:05:00Z"})
    assert first[1] == "fingerprint"
    assert first[0] != second[0]
