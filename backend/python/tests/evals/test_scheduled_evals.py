"""Offline checks for the scheduled answer-quality run.

Everything here runs without a model, a key or a network: case selection, the
cost arithmetic, the baseline comparison, and the guards that stop a run which
measured nothing from reporting success.
"""

from __future__ import annotations

import json

import pytest

from tests.evals.case_sets import (
    NIGHTLY_CASE_IDS,
    CaseSetError,
    UnknownCaseSet,
    check_sets,
    select_cases,
)
from tests.evals.cost import (
    PRICES_PER_MILLION_TOKENS,
    monthly_projection,
    price_for,
    run_cost,
)
from tests.evals.live_harness import (
    GOLDEN_CASES,
    CaseResult,
    EvalReport,
    GoldenCase,
    TraceResult,
)
from tests.evals.run_scheduled_evals import (
    build_result,
    compare_with_baseline,
    main,
    projection_line,
    render_summary,
)


class TestCaseSets:
    def test_nightly_is_a_subset_of_the_full_set(self) -> None:
        nightly = {c.id for c in select_cases("nightly")}
        full = {c.id for c in select_cases("full")}
        assert nightly
        assert nightly <= full

    def test_every_nightly_id_exists(self) -> None:
        known = {c.id for c in GOLDEN_CASES}
        assert set(NIGHTLY_CASE_IDS) <= known

    def test_a_nightly_id_that_no_longer_exists_is_refused(self) -> None:
        # A renamed case would otherwise silently shrink the nightly set.
        with pytest.raises(CaseSetError) as exc:
            check_sets([c for c in GOLDEN_CASES if c.id != NIGHTLY_CASE_IDS[0]])
        assert NIGHTLY_CASE_IDS[0] in str(exc.value)

    def test_an_unknown_set_name_says_what_is_available(self) -> None:
        with pytest.raises(UnknownCaseSet) as exc:
            select_cases("weekly")
        assert "nightly" in str(exc.value) and "full" in str(exc.value)

    def test_no_cases_at_all_is_an_error_not_an_empty_run(self) -> None:
        with pytest.raises(CaseSetError):
            check_sets([])


class TestCost:
    def test_a_listed_model_is_priced_from_its_tokens(self) -> None:
        cost = run_cost("gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
        in_price, out_price = PRICES_PER_MILLION_TOKENS["gpt-4o-mini"]
        assert cost.usd == pytest.approx(in_price + out_price)
        assert cost.known

    def test_a_dated_model_name_matches_its_family(self) -> None:
        assert price_for("gpt-4o-mini-2026-01-01") == PRICES_PER_MILLION_TOKENS["gpt-4o-mini"]

    def test_the_longest_matching_prefix_wins(self) -> None:
        # "gpt-4o" is also a prefix of "gpt-4o-mini-..."; the more specific
        # price is the right one.
        assert price_for("gpt-4o-mini-x") != PRICES_PER_MILLION_TOKENS["gpt-4o"]

    def test_an_unlisted_model_is_not_priced_at_zero(self) -> None:
        cost = run_cost("some-new-model", 1000, 1000)
        assert cost.usd is None
        assert "no price listed" in cost.note
        assert "some-new-model" in cost.render()

    def test_no_tokens_means_no_price_rather_than_free(self) -> None:
        # Zero dollars would read as "this run was free", when the truth is
        # that nothing called the model.
        cost = run_cost("gpt-4o-mini", 0, 0)
        assert cost.usd is None
        assert "no token usage" in cost.note

    def test_monthly_projection_uses_an_average_month(self) -> None:
        assert monthly_projection(1.0, 0.0) == pytest.approx(30.4)
        assert monthly_projection(0.0, 1.0) == pytest.approx(4.3)

    def test_projection_line_matches_the_schedule(self) -> None:
        cost = run_cost("gpt-4o-mini", 1_000_000, 0)
        assert "a month" in projection_line("nightly", cost)
        assert "every week" in projection_line("full", cost)

    def test_no_projection_without_a_price(self) -> None:
        assert projection_line("nightly", run_cost("unlisted", 10, 10)) == ""


def _report(passed: int, ran: int, *, skipped: int = 0) -> EvalReport:
    results = [
        *(CaseResult(f"C-pass-{i}", True, [], TraceResult()) for i in range(passed)),
        *(
            CaseResult(f"C-fail-{i}", False, ["first_tool_is_x"], TraceResult())
            for i in range(ran - passed)
        ),
        *(
            CaseResult(f"C-skip-{i}", False, ["no run_agent supplied"], TraceResult(), True)
            for i in range(skipped)
        ),
    ]
    return EvalReport(model_name="gpt-4o-mini", total=len(results), passed=passed, results=results)


class _Tally:
    input_tokens = 1000
    output_tokens = 500
    requests = 3


def _result(
    passed: int,
    ran: int,
    *,
    case_set: str = "nightly",
    model: str = "gpt-4o-mini",
    tier: str = "small",
) -> dict:
    from datetime import datetime, timezone

    return build_result(
        report=_report(passed, ran),
        case_set=case_set,
        provider="openai",
        model=model,
        tier=tier,
        cost=run_cost(model, 1000, 500),
        tally=_Tally(),
        started_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )


class TestComparison:
    def test_a_fall_in_pass_rate_is_flagged(self) -> None:
        comparison = compare_with_baseline(_result(4, 4), _result(2, 4))
        assert comparison.regressed
        assert not comparison.mismatches

    def test_a_steady_pass_rate_is_not_flagged(self) -> None:
        assert not compare_with_baseline(_result(4, 4), _result(4, 4)).regressed

    def test_an_improvement_is_not_flagged(self) -> None:
        assert not compare_with_baseline(_result(2, 4), _result(4, 4)).regressed

    def test_a_different_model_is_not_judged(self) -> None:
        # A cheaper model failing more cases is expected, not a regression.
        comparison = compare_with_baseline(_result(4, 4), _result(1, 4, model="gpt-4o"))
        assert comparison.mismatches
        assert not comparison.regressed

    def test_a_different_case_set_is_not_judged(self) -> None:
        comparison = compare_with_baseline(_result(4, 4), _result(1, 4, case_set="full"))
        assert comparison.mismatches
        assert not comparison.regressed


class TestSummary:
    def test_the_summary_names_the_failing_cases(self) -> None:
        result = _result(1, 3)
        text = render_summary(result, run_cost("gpt-4o-mini", 1000, 500), None, "No baseline yet.")
        assert "1 of 3 cases passed" in text
        assert "C-fail-0" in text
        assert "first_tool_is_x" in text

    def test_the_summary_says_when_there_is_no_baseline(self) -> None:
        text = render_summary(_result(2, 2), run_cost("gpt-4o-mini", 10, 10), None, "No baseline yet.")
        assert "No baseline yet." in text


class TestRunGuards:
    def test_a_run_with_no_model_exits_non_zero(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("TEST_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("EVAL_PROVIDER", "openai")
        code = main(["--set", "nightly", "--output", str(tmp_path / "out.json")])
        assert code == 2

    def test_a_run_where_no_case_executed_exits_non_zero(self, tmp_path, monkeypatch) -> None:
        # The harness marks every case skipped without a runner. A pass rate
        # over zero executed cases must not read as success.
        import tests.evals.run_scheduled_evals as module

        async def _fake_run(args: object) -> tuple[dict, object, str]:
            from datetime import datetime, timezone

            report = _report(0, 0, skipped=2)
            cost = run_cost("gpt-4o-mini", 0, 0)
            result = build_result(
                report=report,
                case_set="nightly",
                provider="openai",
                model="gpt-4o-mini",
                tier="small",
                cost=cost,
                tally=_Tally(),
                started_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            )
            return result, cost, report.render()

        monkeypatch.setattr(module, "_run", _fake_run)
        out = tmp_path / "out.json"
        code = main(["--set", "nightly", "--output", str(out)])
        assert code == 2
        assert json.loads(out.read_text())["metrics"]["cases_ran"] == 0

    def test_a_placeholder_baseline_is_not_compared(self, tmp_path, monkeypatch) -> None:
        import tests.evals.run_scheduled_evals as module

        async def _fake_run(args: object) -> tuple[dict, object, str]:
            from datetime import datetime, timezone

            report = _report(2, 2)
            cost = run_cost("gpt-4o-mini", 1000, 500)
            result = build_result(
                report=report, case_set="nightly", provider="openai", model="gpt-4o-mini",
                tier="small", cost=cost, tally=_Tally(),
                started_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            )
            return result, cost, report.render()

        monkeypatch.setattr(module, "_run", _fake_run)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"placeholder": True, "note": "nothing yet"}))
        summary = tmp_path / "summary.md"
        code = main([
            "--set", "nightly", "--output", str(tmp_path / "out.json"),
            "--baseline", str(baseline), "--summary", str(summary),
            "--fail-on-regression",
        ])
        assert code == 0
        assert "nothing yet" in summary.read_text()

    def test_a_regression_fails_only_when_asked(self, tmp_path, monkeypatch) -> None:
        import tests.evals.run_scheduled_evals as module

        async def _fake_run(args: object) -> tuple[dict, object, str]:
            from datetime import datetime, timezone

            report = _report(1, 4)
            cost = run_cost("gpt-4o-mini", 1000, 500)
            result = build_result(
                report=report, case_set="nightly", provider="openai", model="gpt-4o-mini",
                tier="small", cost=cost, tally=_Tally(),
                started_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            )
            return result, cost, report.render()

        monkeypatch.setattr(module, "_run", _fake_run)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(_result(4, 4)))
        args = ["--set", "nightly", "--output", str(tmp_path / "out.json"), "--baseline", str(baseline)]
        assert main(args) == 0
        assert main([*args, "--fail-on-regression"]) == 1


class TestStubTools:
    def test_a_stub_wears_the_real_tool_name(self) -> None:
        from tests.evals.live_runner import StubTool, card_for_tool

        tool = StubTool(card_for_tool("knowledgegraph__search"))
        assert tool.name == "knowledgegraph__search"
        assert "changes nothing" not in tool.description.lower()

    async def test_a_stub_returns_a_fixed_result(self) -> None:
        from tests.evals.live_runner import StubTool, card_for_tool

        output = await StubTool(card_for_tool("jira_search_issues")).execute(jql="x")
        assert output.success
        assert output.data["result"] == card_for_tool("jira_search_issues").result
        # The arguments come back too: a case that checks what the model asked
        # for has nowhere else to read it.
        assert output.data["arguments"] == {"jql": "x"}

    def test_a_stub_keeps_the_real_parameters_and_tags(self) -> None:
        from tests.evals.live_runner import StubTool, card_for_tool

        write = StubTool(card_for_tool("jira_transition_issue"))
        assert {p.name for p in write.parameters} == {"issue_key", "transition"}
        assert any(t.key == "risk" for t in write.tags)

    def test_the_terminal_tool_is_always_granted(self) -> None:
        """Without it AgentResult.confidence is always None."""
        from tests.evals.live_runner import FINAL_ANSWER_TOOL, registry_for

        case = GoldenCase(
            id="C-x", description="", query="q",
            granted_tools=["knowledgegraph__search"], assertions=[],
        )
        assert FINAL_ANSWER_TOOL in registry_for(case).names()
