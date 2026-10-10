"""`handoff_comparison.py` end to end with scripted models and no network.

The real factory, `Agent.stream()`, `TerminalAnswerStreamer` and
`AnswerFinalizer` run; only the transports are scripted. The coding delegate
is called with and without `final=true`, so the arms must differ by exactly
the root agent's second model pass.
"""

from __future__ import annotations

import os

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agents.agent_loop.evals.handoff_comparison import (
    CredentialsError,
    JudgeRequest,
    JudgeVerdict,
    Report,
    load_env_file,
    render_markdown,
    require_credentials,
    run_comparison,
)
from app.agents.agent_loop.evals.handoff_queries import (
    HANDOFF_QUERIES,
    HandoffQuery,
    select_queries,
)
from tests.unit.agent_loop_lib.agent.test_cut_off_reply_continuation import (
    _TokenStream,
    _turn,
)
from tests.unit.agents.adapter.conftest import FakeChatModel
from tests.unit.agents.adapter.test_cut_off_answer_saved_whole import _tool_turn

_CODE = next(q for q in HANDOFF_QUERIES if q.id == "fib_sum")
_CHAT = next(q for q in HANDOFF_QUERIES if q.id == "chitchat")
_ANSWER = "The sum of the first 25 Fibonacci numbers is 121392."


def _delegate_call(**arguments: object) -> ToolCall:
    return ToolCall(id="d1", name="coding_agent", arguments={"goal": "sum fibonacci", **arguments})


def _transport_for(query: HandoffQuery, arm: str) -> _TokenStream:
    if query is _CHAT:
        return _TokenStream([_turn("Doing well, thanks for asking.")])
    if arm == "handoff":
        return _TokenStream([_tool_turn("", _delegate_call(final=True)), _turn(_ANSWER)])
    return _TokenStream([
        _tool_turn("", _delegate_call()), _turn("Code says 121392."), _turn(_ANSWER),
    ])


async def _judge(request: JudgeRequest) -> JudgeVerdict:
    return JudgeVerdict(preference="A", reason="first one", completeness_a=5, completeness_b=4)


@pytest.fixture(autouse=True)
def _no_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


async def _report(runs: int = 1) -> Report:
    return await run_comparison(
        (_CODE, _CHAT), llm=FakeChatModel(), provider_id="scripted", model_name="claude-sonnet-5",
        runs=runs, judge=_judge, judge_model="claude-sonnet-5", transport_for=_transport_for,
    )


class TestRunComparison:
    async def test_handoff_skips_the_root_agents_second_model_pass(self) -> None:
        report = await _report()

        by_key = {(r.query_id, r.arm): r for r in report.records}
        assert [r.errors for r in report.records] == [[]] * 4
        delegate, handoff = by_key["fib_sum", "delegate"], by_key["fib_sum", "handoff"]
        assert delegate.model_calls == handoff.model_calls + 1
        assert handoff.calls_by_kind["stream"] == 2
        assert handoff.final_requested and handoff.answered_by == "coding_agent"
        assert not delegate.final_requested and delegate.answered_by is None
        assert handoff.answer == delegate.answer == _ANSWER

    async def test_a_control_query_costs_the_same_in_both_arms(self) -> None:
        report = await _report()

        control = [r for r in report.records if r.query_id == "chitchat"]
        assert {r.model_calls for r in control} == {2}
        assert not any(r.final_requested for r in control)

    async def test_time_to_answer_is_recorded_in_order(self) -> None:
        report = await _report()

        for record in report.records:
            assert record.first_answer_s is not None and record.last_answer_s is not None
            assert 0 < record.first_answer_s <= record.last_answer_s <= record.wall_s

    async def test_report_shape_and_judge_mapping(self) -> None:
        report = await _report(runs=2)

        assert len(report.records) == 8
        assert report.by_arm["handoff"].runs == 4 and report.by_arm["handoff"].final_requested_rate == 0.5
        assert report.by_category["handoff_candidate"]["delegate"].model_calls.median == 4
        assert {row.query_id: row.matches_expectation for row in report.final_choice} == {
            "fib_sum": 1.0, "chitchat": 1.0,
        }
        assert report.final_agreement == 1.0
        assert report.judge.pairs == 4
        assert report.judge.handoff_wins + report.judge.delegate_wins == 4
        assert all(set(p.completeness) == {"delegate", "handoff"} for p in report.judged)
        assert "handoff" in render_markdown(report)
        assert report.model_validate_json(report.model_dump_json()) == report


class TestInputs:
    def test_missing_credentials_name_the_variable_not_a_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(CredentialsError, match="ANTHROPIC_API_KEY"):
            require_credentials("anthropic")

    def test_env_file_fills_gaps_without_overriding(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_ORG_ID", "from-env")
        env_file = tmp_path / ".env"
        env_file.write_text("# c\nexport OPENAI_API_KEY='sk-test'\nOPENAI_ORG_ID=from-file\n")

        load_env_file(str(env_file))

        assert require_credentials("openai").provider_id == "openAI"
        assert os.environ["OPENAI_ORG_ID"] == "from-env"

    def test_unknown_query_id_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown query"):
            select_queries(["nope"])
