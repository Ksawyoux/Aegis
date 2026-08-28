"""Strict demo-mode enforcement for the paid evaluation suite (Part 4 §3.2, §9).

``AEGIS_REQUIRE_LIVE_EVAL=1`` is what ``make demo`` sets. Under that flag, the
false-green path where ``pytest`` exits zero with every paid assertion skipped
must be closed: missing credentials fail collection outright, and any skipped
test under ``tests/eval`` or the live embedding acceptance test in
``tests/corpus_contract`` fails the whole session at the end, even though each
individual test still reports as "skipped" in the summary line. Ordinary
developer runs (no ``AEGIS_REQUIRE_LIVE_EVAL``) are unaffected: this module's
hooks are no-ops unless the strict flag is set.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests.eval.harness import (
    EXPECTED_SCENARIO_COUNT,
    SCENARIO_DIR,
    load_evaluation_cases,
    strict_live_eval_required,
)

if TYPE_CHECKING:
    from _pytest.config import Config
    from _pytest.reports import TestReport
    from _pytest.terminal import TerminalReporter

_REQUIRED_CREDENTIAL_ENV_NAMES = ("OPENAI_API_KEY",)
_STRICT_PATH_PREFIXES = ("tests/eval/", "tests/corpus_contract/")

_skip_reports: list[TestReport] = []


def pytest_configure(config: Config) -> None:
    """Fail collection outright under strict mode rather than skip quietly.

    A ``pytest.skip`` inside a fixture still lets the session exit zero; only
    stopping before collection completes turns a missing credential or an
    incomplete scenario corpus into an unambiguous failure (Part 4 §3.2).
    """
    del config
    if not strict_live_eval_required():
        return

    missing = [
        name
        for name in _REQUIRED_CREDENTIAL_ENV_NAMES
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        pytest.exit(
            f"AEGIS_REQUIRE_LIVE_EVAL=1 but missing/empty credentials: {', '.join(missing)}. "
            "The five agent evaluations cannot run and skipped evaluations do not satisfy "
            "make demo.",
            returncode=1,
        )

    cases = load_evaluation_cases()
    if len(cases) != EXPECTED_SCENARIO_COUNT:
        pytest.exit(
            f"AEGIS_REQUIRE_LIVE_EVAL=1 requires exactly {EXPECTED_SCENARIO_COUNT} scenario "
            f"manifests under {SCENARIO_DIR}; found {len(cases)}.",
            returncode=1,
        )


def pytest_runtest_logreport(report: TestReport) -> None:
    if strict_live_eval_required() and report.when == "call" and report.skipped:
        _skip_reports.append(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Turn any skipped required evaluation into a failed session (Part 4 §3.2).

    Individual skipped tests still print as "skipped" in the summary -- pytest
    has no clean way to relabel a report after the fact -- but the session's
    final exit status is forced non-zero, which is what ``make demo`` and CI
    actually gate on.
    """
    del exitstatus
    if not strict_live_eval_required():
        return

    offending = [
        report.nodeid
        for report in _skip_reports
        if report.nodeid.startswith(_STRICT_PATH_PREFIXES)
    ]
    if offending:
        reporter: TerminalReporter | None = session.config.pluginmanager.get_plugin(
            "terminalreporter"
        )
        if reporter is not None:
            reporter.write_line(
                "AEGIS_REQUIRE_LIVE_EVAL=1: the following required evaluations were "
                f"skipped, which fails make demo: {offending}",
                red=True,
            )
        session.exitstatus = 1
