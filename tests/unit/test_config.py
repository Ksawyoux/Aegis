from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from aegis.config import Settings


def _clear_openai_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "AEGIS_OPENAI_API_KEY",
        "AEGIS_OPENAI_BASE_URL",
        "AEGIS_EMBEDDING_MODEL",
        "AEGIS_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _settings_without_dotenv() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_loads_cwd_dotenv_and_ignores_unrecognized_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_openai_environment(monkeypatch)
    (tmp_path / ".env").write_text(
        "AEGIS_DATABASE_URL=postgresql+psycopg://dotenv@example/aegis\n"
        "OPENAI_API_KEY=sk-test-from-dotenv\n"
        "UNRECOGNIZED_LOCAL_SETTING=ignored\n"
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.database_url == "postgresql+psycopg://dotenv@example/aegis"
    assert settings.openai_api_key == SecretStr("sk-test-from-dotenv")


def test_settings_reads_unprefixed_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openai_environment(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-unprefixed")

    settings = _settings_without_dotenv()

    assert settings.openai_api_key == SecretStr("sk-test-unprefixed")


def test_settings_reads_prefixed_openai_api_key_as_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openai_environment(monkeypatch)
    monkeypatch.setenv("AEGIS_OPENAI_API_KEY", "sk-test-prefixed")

    settings = _settings_without_dotenv()

    assert settings.openai_api_key == SecretStr("sk-test-prefixed")


def test_openai_api_key_is_masked_in_settings_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openai_environment(monkeypatch)
    fake_key = "sk-test-not-a-real-key"
    settings = Settings(
        openai_api_key=SecretStr(fake_key),
        _env_file=None,  # type: ignore[call-arg]
    )

    assert fake_key not in repr(settings)
    dumped = settings.model_dump()
    assert fake_key not in repr(dumped)
    assert isinstance(dumped["openai_api_key"], SecretStr)
    assert str(dumped["openai_api_key"]) == "**********"
    dumped_json = settings.model_dump_json()
    assert fake_key not in dumped_json
    assert "**********" in dumped_json


def test_openai_configuration_defaults_are_part_2_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openai_environment(monkeypatch)

    settings = _settings_without_dotenv()

    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.embedding_model == "text-embedding-3-small"


def test_ollama_is_not_a_v1_dependency() -> None:
    """Revision 3 of Part 2 replaced Ollama with OpenAI; the stale field is gone.

    A legacy ``AEGIS_OLLAMA_BASE_URL`` in an operator's shell must remain
    harmless -- settings ignore unrelated environment values -- rather than
    resurrecting a field nothing reads.
    """
    assert "ollama_base_url" not in Settings.model_fields


def test_a_blank_credential_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` templates ship the key name with an empty value.

    An empty string is not None, so every ``is None`` guard passes and the blank
    value reaches the wire, surfacing as `LocalProtocolError: Illegal header
    value b'Bearer '` from inside the HTTP client instead of "no API key
    configured".
    """
    _clear_openai_environment(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AEGIS_SLACK_WEBHOOK_URL", "   ")
    monkeypatch.setenv("AEGIS_GITHUB_WEBHOOK_SECRET", "")

    settings = _settings_without_dotenv()

    assert settings.openai_api_key is None
    assert settings.slack_webhook_url is None
    assert settings.github_webhook_secret is None


def test_a_real_credential_survives_the_blank_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_openai_environment(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-real")

    settings = _settings_without_dotenv()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-real"
