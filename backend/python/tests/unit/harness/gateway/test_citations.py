"""CI-1..CI-3: the run's citation state through the gateway, and the fold of what it recorded."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, override

import pytest

from app.harness.citations import EVENT, CitationJournal
from app.harness.gateway.citations import JournalCitationSink
from app.harness.presentation import PipesHubPresenter
from app.utils.chat_helpers import CitationRefMapper
from app.utils.citations import normalize_citations_and_chunks_for_agent
from harness_core.effects import EffectRunner, RequestEffectLog, ToolExecution
from harness_core.gateway import RunToolEndpoint
from harness_core.invoker import InvocationScope
from harness_core.journal import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext, classify_tool_effect
from harness_core.testing.contracts.gateway import CitationSinkContract
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.ids import SeededIds
from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from harness_core.effects import EffectCall
    from harness_core.gateway.ports import CitationSink
    from harness_core.journal.types import NewEvent

URLS = ("https://kb.example/record/r1/block/0", "https://kb.example/record/r1/block/1")


def _context() -> Any:  # noqa: ANN401 - the legacy AgentContext shape the tools see
    return SimpleNamespace(tool_state={}, root_agent_spec=None, conversation_id=None)


def _label(state: dict[str, Any], url: str) -> str:
    """What a knowledge tool does with a block it returns (``retrieval.py``)."""
    mapper = state.get("citation_ref_mapper") or CitationRefMapper()
    state["citation_ref_mapper"] = mapper
    return mapper.get_or_create_ref(url)


class JournalCitations:
    def __init__(self) -> None:
        context = _context()
        self.state: dict[str, Any] = context.tool_state
        presenter = PipesHubPresenter(context, ToolRegistry(), citations=CitationJournal(context))
        self.sink: CitationSink = JournalCitationSink(self.state, presenter.citations_for)

    def label(self, url: str) -> str:
        return _label(self.state, url)


class TestJournalCitationSink(CitationSinkContract):
    """CI-2, durable."""

    @pytest.fixture
    def citation_harness(self) -> JournalCitations:
        return JournalCitations()


def test_ci2_the_delta_is_a_citations_event_journaled_once() -> None:
    harness = JournalCitations()
    harness.label(URLS[0])
    (event,) = harness.sink.record("inv-1")
    assert event.type == EVENT and not event.critical
    assert event.data == {"callId": "inv-1", "refs": [{"url": URLS[0], "ref": "ref1"}]}
    assert harness.sink.record("inv-2") == ()


class KnowledgeSearch(Tool):
    """A knowledge tool as the platform's retrieval tool behaves toward citation state."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    @property
    @override
    def name(self) -> str:
        return "search"

    @property
    @override
    def short_description(self) -> str:
        return "Search company knowledge."

    @property
    @override
    def description(self) -> str:
        return "Search company knowledge."

    @property
    @override
    def path(self) -> str:
        return "/tools/knowledge/search"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="query", required=True)]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        refs = [_label(self._state, url) for url in URLS]
        blocks = self._state.setdefault("final_results", [])
        for index, url in enumerate(URLS):
            blocks.append({"virtual_record_id": "vr-1", "block_index": index, "block_web_url": url, "content": f"block {index}"})
        self._state["virtual_record_id_to_result"] = {"vr-1": {"id": "r1", "record_name": "Q3 report"}}
        return ToolOutput(success=True, data=f"Revenue grew [{refs[0]}]; churn fell [{refs[1]}].")


class Recording(RequestEffectLog):
    def __init__(self) -> None:
        super().__init__()
        self.outcome_events: list[NewEvent] = []

    @override
    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None:
        self.outcome_events.extend(execution.events)
        await super().outcome(call, execution)


def _world(*, present: bool) -> tuple[Any, PipesHubPresenter, RegistryToolPort]:
    context = _context()
    registry = ToolRegistry()
    registry.register_tool(KnowledgeSearch(context.tool_state))
    presenter = PipesHubPresenter(context, registry, citations=CitationJournal(context))
    effect = classify_tool_effect("search", [Tag("category", "read")])
    port = RegistryToolPort(registry, {"search": effect}, present=presenter.result if present else None)
    return context, presenter, port


def _citation_events(events: list[NewEvent] | tuple[NewEvent, ...]) -> list[dict[str, Any]]:
    return [{k: v for k, v in dict(e.data).items() if k != "callId"} for e in events if e.type == EVENT]


async def test_ci3_endpoint_citations_fold_like_a_native_calls() -> None:
    # Native: the kernel's tool port, the presenter journaling with the outcome.
    _, _, native_port = _world(present=True)
    native = await native_port.execute(
        ToolCall(id="t1.0", name="search", arguments={"q": "q3"}),
        ToolContext("run-1", "org-1", "t1.0", "run-1.a2", "org-1:run-1:t1.0"),
    )
    # Through the gateway: same tool, the sink records the delta with the outcome.
    context, presenter, port = _world(present=False)
    log = Recording()
    endpoint = RunToolEndpoint(
        binding=BINDING,
        catalog=port,
        effects=EffectRunner(log),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=JournalCitationSink(context.tool_state, presenter.citations_for),
    )
    result = await endpoint.call_tool(claims_for(BINDING), "search", {"q": "q3"})

    assert result.text == native.content
    assert [(c.ref, c.url) for c in result.citations] == [("ref1", URLS[0]), ("ref2", URLS[1])]
    assert _citation_events(log.outcome_events) == _citation_events(native.events)
    assert len(_citation_events(log.outcome_events)) == 1

    # The parent's fold: a fresh state restored from what was journaled resolves the same refs.
    recorded = [
        JournalEvent(
            org_id="org-1", session_id="session-1", stream_id="session-1", seq=7, run_id="run-1",
            attempt_id="run-1.a2", fence=2, type=e.type, schema_version=1, critical=False,
            ts="2026-10-03T12:00:00Z", writer="worker", visibility="owner", data=dict(e.data),
        )
        for e in log.outcome_events
        if e.type == EVENT
    ]
    parent = _context()
    await CitationJournal(parent).restore(recorded)
    state = parent.tool_state
    assert state["citation_ref_mapper"].ref_to_url == {"ref1": URLS[0], "ref2": URLS[1]}

    # The answer keeps the engine's refs and drops one it invented.
    answer = f"Revenue grew [Q3]({'ref1'}) and churn fell [Q3]({'ref2'}); margins [x](ref9)."
    _, citations = normalize_citations_and_chunks_for_agent(
        answer,
        state["final_results"],
        state["virtual_record_id_to_result"],
        ref_to_url=state["citation_ref_mapper"].ref_to_url,
    )
    assert [c["content"] for c in citations] == ["block 0", "block 1"]


async def test_ci3_a_recorded_replay_carries_the_same_citations() -> None:
    context, presenter, port = _world(present=False)
    endpoint = RunToolEndpoint(
        binding=BINDING,
        catalog=port,
        effects=EffectRunner(RequestEffectLog()),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=JournalCitationSink(context.tool_state, presenter.citations_for),
    )
    first = await endpoint.call_tool(claims_for(BINDING), "search", {"q": "q3"})
    assert [c.ref for c in first.citations] == ["ref1", "ref2"]
