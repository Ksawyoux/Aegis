"""Unit tests for the release coordinator that do not touch a real database.

Docker is not installed and usable on this development machine (the `docker`
CLI is present but its daemon never responds), so every Docker-dependent path
below is exercised through a monkeypatched ``subprocess.run`` rather than a
real, hanging call -- a real invocation would need the 30-second timeout
``preflight`` gives it before it can report failure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aegis.release import demo


def _demo_options(tmp_path: Path, **overrides: object) -> demo.DemoOptions:
    defaults: dict[str, object] = {
        "root": tmp_path,
        "database_mode": "external",
        "database_url": "postgresql+psycopg://aegis:aegis@127.0.0.1:5432/aegis_demo",
    }
    defaults.update(overrides)
    return demo.DemoOptions(**defaults)  # type: ignore[arg-type]


def _write_minimal_repo(root: Path, *, scenario_count: int = 5) -> None:
    (root / "uv.lock").write_text("", encoding="utf-8")
    (root / "alembic.ini").write_text("", encoding="utf-8")
    corpus = root / "corpus"
    (corpus / "scenarios").mkdir(parents=True)
    (corpus / "git").mkdir(parents=True)
    (corpus / "logs").mkdir(parents=True)
    (corpus / "services.yaml").write_text("[]\n", encoding="utf-8")
    (corpus / "git" / "export.json").write_text("{}", encoding="utf-8")
    (corpus / "logs" / "app.log").write_text("line\n", encoding="utf-8")
    for index in range(scenario_count):
        (corpus / "scenarios" / f"scenario-{index}.yaml").write_text(
            f"name: scenario-{index}\n"
            f"alert:\n  dedup_key: dedup-{index}\n"
            "expect: {}\n",
            encoding="utf-8",
        )


class TestRedactDatabaseUrl:
    def test_password_is_masked(self) -> None:
        redacted = demo.redact_database_url(
            "postgresql+psycopg://aegis:supersecret@127.0.0.1:5432/aegis"
        )
        assert "supersecret" not in redacted
        assert redacted == "postgresql+psycopg://aegis:***@127.0.0.1:5432/aegis"

    def test_url_without_a_password_is_returned_unchanged(self) -> None:
        url = "postgresql+psycopg://127.0.0.1:5432/aegis"
        assert demo.redact_database_url(url) == url


class TestPreflightCredentials:
    def test_missing_openai_key_fails_before_docker_or_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_minimal_repo(tmp_path)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
        called = {"docker": False}
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: called.__setitem__("docker", True) or _fake_ok()
        )

        with pytest.raises(demo.DemoError) as excinfo:
            demo.preflight(_demo_options(tmp_path))

        assert excinfo.value.stage == "prerequisites"
        assert "OPENAI_API_KEY" in str(excinfo.value)
        assert called["docker"] is False

    def test_whitespace_only_anthropic_key_is_treated_as_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_minimal_repo(tmp_path)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")

        with pytest.raises(demo.DemoError, match="ANTHROPIC_API_KEY"):
            demo.preflight(_demo_options(tmp_path))

    def test_placeholder_key_value_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_minimal_repo(tmp_path)
        monkeypatch.setenv("OPENAI_API_KEY", "changeme")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")

        with pytest.raises(demo.DemoError, match="OPENAI_API_KEY"):
            demo.preflight(_demo_options(tmp_path))


class TestPreflightScenarioManifests:
    def test_wrong_scenario_count_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_minimal_repo(tmp_path, scenario_count=1)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")

        with pytest.raises(demo.DemoError, match="scenario manifests"):
            demo.preflight(_demo_options(tmp_path))

    def test_duplicate_dedup_key_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_minimal_repo(tmp_path, scenario_count=5)
        (tmp_path / "corpus" / "scenarios" / "scenario-1.yaml").write_text(
            "name: scenario-1\nalert:\n  dedup_key: dedup-0\nexpect: {}\n", encoding="utf-8"
        )
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")

        with pytest.raises(demo.DemoError, match="duplicate scenario dedup_key"):
            demo.preflight(_demo_options(tmp_path))


class TestPreflightDatabaseMode:
    def test_external_mode_without_a_url_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_minimal_repo(tmp_path, scenario_count=5)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")

        with pytest.raises(demo.DemoError, match="AEGIS_DATABASE_URL"):
            demo.preflight(_demo_options(tmp_path, database_mode="external", database_url=""))

    def test_compose_mode_without_docker_names_the_external_alternative(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_minimal_repo(tmp_path, scenario_count=5)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_failure())

        with pytest.raises(demo.DemoError, match="AEGIS_DEMO_DB_MODE=external"):
            demo.preflight(_demo_options(tmp_path, database_mode="compose"))


class TestChildEnvironment:
    def test_slack_webhook_is_never_propagated_into_the_child(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AEGIS_SLACK_WEBHOOK_URL", "https://hooks.slack.example/real")
        from aegis.config import Settings

        options = _demo_options(tmp_path)
        environment = demo.build_child_environment(options, Settings())

        assert "AEGIS_SLACK_WEBHOOK_URL" not in environment
        assert environment["AEGIS_DEMO_MODE"] == "1"
        assert environment["AEGIS_REQUIRE_POSTGRES"] == "1"
        assert environment["AEGIS_REQUIRE_LIVE_EVAL"] == "1"
        assert environment["AEGIS_DATABASE_URL"] == options.database_url
        assert environment["AEGIS_CORPUS_DIR"] == str((tmp_path / "corpus").resolve())

    def test_credentials_are_never_embedded_in_a_demo_error_via_the_database_url(
        self, tmp_path: Path
    ) -> None:
        redacted = demo.redact_database_url(
            "postgresql+psycopg://aegis:swordfish@127.0.0.1:5432/aegis_demo"
        )
        error = demo.DemoError("database", f"could not reach {redacted}")
        assert "swordfish" not in str(error)


class TestStageFailureStopsImmediately:
    def test_a_failing_subprocess_raises_a_stage_labelled_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(
            argv: list[str], *, cwd: object, env: object, check: bool
        ) -> subprocess.CompletedProcess[str]:
            del cwd, env, check
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode=1)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(demo.DemoError) as excinfo:
            demo._run_stage(
                "ruff", ["uv", "run", "ruff", "check", "."], root=tmp_path, environment={}
            )

        assert excinfo.value.stage == "ruff"
        assert len(calls) == 1, "a later stage must never run after the failing one"

    def test_stage_runs_from_repository_root_regardless_of_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen_cwd: list[object] = []

        def fake_run(
            argv: list[str], *, cwd: object, env: object, check: bool
        ) -> subprocess.CompletedProcess[str]:
            del env, check
            seen_cwd.append(cwd)
            return subprocess.CompletedProcess(argv, returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        demo._run_stage("noop", ["true"], root=tmp_path, environment={})

        assert seen_cwd == [tmp_path]


class TestAssertDatabaseIsEmpty:
    def test_a_schema_reporting_no_project_tables_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeResult:
            def scalars(self) -> _FakeResult:
                return self

            def all(self) -> list[str]:
                return []

        class _FakeConnection:
            def __enter__(self) -> _FakeConnection:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

            def execute(self, *args: object, **kwargs: object) -> _FakeResult:
                return _FakeResult()

        class _FakeEngine:
            def connect(self) -> _FakeConnection:
                return _FakeConnection()

            def dispose(self) -> None:
                return None

        monkeypatch.setattr(demo, "_connect_engine", lambda url: _FakeEngine())
        demo.assert_database_is_empty("postgresql://fake")  # must not raise


def _fake_ok() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")


def _fake_failure() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode=1, stdout="", stderr="not found")
