"""The live harness must not report a run that measured nothing as passing.

`run_golden_evals` takes an optional `run_agent`. Without it no case executes.
Reporting those cases as passed turns "nothing ran" into a green result, which
is more dangerous than a red one because nobody investigates a green build —
and this harness is intended to be wired into a scheduled job, where a silent
skip could go unnoticed indefinitely.

These tests run offline: no model, no API key.
"""

from __future__ import annotations

import asyncio

from tests.evals.live_harness import (
    GOLDEN_CASES,
    TraceResult,
    run_golden_evals,
)


def _report_without_runner():
    return asyncio.run(run_golden_evals(model=None))


class TestSkippedCasesAreNotPasses:
    def test_no_case_is_marked_passed_when_nothing_ran(self) -> None:
        report = _report_without_runner()
        assert report.passed == 0, (
            f"{report.passed} cases reported as passed without a run_agent; a "
            "skipped case must never count as a pass"
        )

    def test_every_case_is_marked_skipped(self) -> None:
        report = _report_without_runner()
        assert report.skipped == report.total
        assert report.ran == 0

    def test_pass_rate_is_not_a_perfect_score(self) -> None:
        """The specific regression: pass_rate used to be passed/total = 100%."""
        report = _report_without_runner()
        assert report.pass_rate == 0.0, (
            f"pass rate is {report.pass_rate:.0%} for a run that executed no "
            "cases; it must not read as a perfect score"
        )

    def test_render_says_plainly_that_nothing_ran(self) -> None:
        text = _report_without_runner().render()
        assert "no cases ran" in text.lower()
        assert "No cases executed" in text


class TestExecutedCasesStillReportNormally:
    """The fix must not break the path that actually measures something."""

    def test_a_passing_case_is_reported_as_passed(self) -> None:
        case = GOLDEN_CASES[0]

        async def _run(c, model):  # noqa: ANN001 - test double
            del c, model
            # A trace that satisfies nothing in particular; assertions may still
            # fail, so this asserts on the bookkeeping rather than the verdict.
            return TraceResult()

        report = asyncio.run(
            run_golden_evals(model=None, cases=[case], run_agent=_run)
        )
        assert report.total == 1
        assert report.ran == 1, "an executed case must not be counted as skipped"
        assert report.skipped == 0

    def test_pass_rate_is_measured_against_cases_that_ran(self) -> None:
        cases = GOLDEN_CASES[:2]

        async def _run(c, model):  # noqa: ANN001 - test double
            del c, model
            return TraceResult()

        report = asyncio.run(
            run_golden_evals(model=None, cases=cases, run_agent=_run)
        )
        assert report.ran == len(cases)
        assert 0.0 <= report.pass_rate <= 1.0
        assert report.pass_rate == (report.passed / report.ran)
