"""`PipesHubAgentFactory.create()` wires the request's `UsageLedger` into the
intent call and the runtime only when the request opted into telemetry:
those calls then reach the `run_usage` frame, and a normal request records
nothing."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.core.responses import TokenUsage
from app.agents.agent_loop.factory import PipesHubAgentFactory
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Skills would await real graph calls on make_context's MagicMock provider.
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    monkeypatch.delenv("OPIK_URL_OVERRIDE", raising=False)


async def _runtime(*, include_retrieval_context: bool):  # noqa: ANN202
    context = make_context(llm=FakeChatModel(), include_retrieval_context=include_retrieval_context)
    _agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
        context, context.llm, "react", query="hello",
    )
    return context, runtime


class TestUsageLedgerWiring:
    async def test_child_calls_reach_the_request_ledger_when_opted_in(self) -> None:
        context, runtime = await _runtime(include_retrieval_context=True)

        runtime.usage_sink("sub_agent:researcher", TokenUsage(input_tokens=5, output_tokens=1))

        sources = [source for source, _usage in context.usage_ledger.entries]
        # The intent call ran inside create(), before the runtime existed.
        assert sources == ["single_shot:intent.parse_intent_and_route", "sub_agent:researcher"]
        assert context.tool_state["usage_ledger"] is context.usage_ledger

    async def test_nothing_is_wired_for_a_normal_request(self) -> None:
        context, runtime = await _runtime(include_retrieval_context=False)

        assert runtime.usage_sink is None
        assert context.tool_state.get("usage_ledger") is None
