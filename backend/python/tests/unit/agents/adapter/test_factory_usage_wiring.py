"""`PipesHubAgentFactory.create()` wraps the request's transports to report
into the request's `UsageLedger` only when the request opted into telemetry:
every agent's model calls then reach the `run_usage` frame, and a normal
request records nothing."""

from __future__ import annotations

import pytest

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
    async def test_the_request_transports_report_when_opted_in(self) -> None:
        from app.agents.agent_loop.usage_transport import UsageReportingTransport

        context, runtime = await _runtime(include_retrieval_context=True)

        for provider in runtime.transport_registry.providers():
            assert isinstance(runtime.transport_registry.resolve(provider), UsageReportingTransport)
        # The intent call ran inside create() on these transports.
        assert [s for s, _ in context.usage_ledger.entries] == ["single_shot:intent.parse_intent_and_route"]
        assert context.tool_state["usage_ledger"] is context.usage_ledger

    async def test_nothing_is_wired_for_a_normal_request(self) -> None:
        from app.agents.agent_loop.usage_transport import UsageReportingTransport

        context, runtime = await _runtime(include_retrieval_context=False)

        for provider in runtime.transport_registry.providers():
            assert not isinstance(runtime.transport_registry.resolve(provider), UsageReportingTransport)
        assert context.usage_ledger.entries == []
        assert context.tool_state.get("usage_ledger") is None
