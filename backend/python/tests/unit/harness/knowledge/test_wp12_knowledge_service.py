"""WP-12 KNOW-SVC: the stateless knowledge service behind the tool gateway (C3.17, C3.20, C3.22, C3.23).

- C3.17 / parity: ``KnowledgeService`` called with nothing but an explicit principal and scope answers exactly what
  the loop's search answers from its whole ``ChatState``, for the same actor; another actor gets only their own.
- KN-04 (C3.20): connector, KB, date and record-type filters reach retrieval or the listing server-side, through the
  gateway; an unknown source, date, record type or response format is an actionable error that searched nothing.
- KN-06 (C3.22): concise by default, ``response_format=detailed`` widens the result.
- KN-08 (C3.23): a result drawn from Slack, email or the web is marked untrusted with its sources, its outcome is
  labelled ``integrity: external``, and the session's taint feeds the flow rule (Rule of Two).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.agents.actions.knowledge_graph.ops.search_budget import (
    CONCISE,
    DETAILED,
    response_format_error,
    search_budget,
)
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.gateway.citations import JournalCitationSink
from app.harness.knowledge.knowledge_service import (
    KnowledgeBackends,
    KnowledgeCall,
    KnowledgeModel,
    KnowledgePrincipal,
    KnowledgeService,
    PinnedKnowledge,
    SearchRequest,
)
from app.harness.knowledge.provenance import (
    untrusted_in,
    untrusted_integrity,
    untrusted_sources,
)
from app.harness.labels import SessionSourceLedger
from app.modules.retrieval.context.targeted import (
    MAX_PASSAGES_PER_RECORD,
    passages_per_record,
    search_max_chars,
)
from harness_core.effects import EffectRunner, RequestEffectLog
from harness_core.effects.flow import FlowGuardedPolicy, FlowRule
from harness_core.gateway.endpoint import RunToolEndpoint
from harness_core.invoker import InvocationScope
from harness_core.journal.types import JournalEvent, NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import classify_tool_effect
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.ids import SeededIds
from harness_core.tools.base import Tag
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder
from tests.unit.harness.knowledge.knowledge_world import (
    DRIVE,
    KB,
    ORG,
    SLACK,
    FakeRetrieval,
    chat_state,
    rendering,
)

if TYPE_CHECKING:
    from harness_core.effects import ApprovalDecision, ToolEffect
    from harness_core.gateway.types import McpResult

SEARCH = "knowledgegraph__search"
LIST_FILES = "knowledgegraph__list_files"


def _explicit_call(user: str, retrieval: FakeRetrieval) -> tuple[KnowledgeService, KnowledgeCall]:
    """The service and a call that name only the principal and the pinned scope: no ``ChatState``."""
    knowledge = ({"connectorId": DRIVE}, {"connectorId": SLACK}, {"connectorId": KB, "type": "KB"})
    service = KnowledgeService(KnowledgeBackends(retrieval=retrieval, graph=AsyncMock(), config_service=MagicMock()))
    call = KnowledgeCall(
        KnowledgePrincipal(org_id=ORG, user_id=user, user_info={"userId": user, "orgId": ORG}),
        PinnedKnowledge(
            filters={"apps": [DRIVE, SLACK], "kb": [KB]}, agent_knowledge=knowledge, apps=(DRIVE, SLACK), kb=(KB,),
        ),
        KnowledgeModel(),
        session={},
    )
    return service, call


def _refs(state: dict[str, Any]) -> dict[str, str]:
    mapper = state.get("citation_ref_mapper")
    return dict(mapper.url_to_ref) if mapper is not None else {}


# ---- C3.17: the stateless service, in parity with the loop ----


async def test_c3_17_the_service_answers_as_the_loops_search_for_the_same_actor() -> None:
    """Parity: the loop's search over its whole ChatState and the service over an explicit principal and scope
    retrieve with the same user, org and filters, render the same text and label the same refs."""
    loop_retrieval, service_retrieval = FakeRetrieval(), FakeRetrieval()
    loop_state = chat_state("alice", loop_retrieval)
    service, call = _explicit_call("alice", service_retrieval)
    with rendering():
        loop = await execute_search(loop_state, "launch plan")  # type: ignore[arg-type]
        served = await service.search(call, SearchRequest(query="launch plan"))
    assert served == loop
    assert "ALPHA7319" in served and "SLACK4410" in served and "BRAVO5581" not in served
    assert _refs(dict(call.session)) == _refs(loop_state)
    strip = ("logger",)
    assert [{k: v for k, v in c.items() if k not in strip} for c in service_retrieval.calls] == [
        {k: v for k, v in c.items() if k not in strip} for c in loop_retrieval.calls
    ]


async def test_c3_17_another_actor_reads_only_their_own_records_through_the_same_service() -> None:
    """Same ACL: Bob's call never sees Alice's records, and Alice's never sees Bob's."""
    retrieval = FakeRetrieval()
    service, alice = _explicit_call("alice", retrieval)
    _, bob = _explicit_call("bob", retrieval)
    with rendering():
        as_bob = await service.search(bob, SearchRequest(query="launch plan"))
        as_alice = await service.search(alice, SearchRequest(query="launch plan"))
    assert "BRAVO5581" in as_bob and "ALPHA7319" not in as_bob and "SLACK4410" not in as_bob
    assert "BRAVO5581" not in as_alice
    assert [c["user_id"] for c in retrieval.calls] == ["bob", "alice"]


async def test_c3_17_a_session_cannot_widen_the_calls_principal_or_scope() -> None:
    """The call's principal and scope win over what the session holds: a session saying ``user_id: alice`` and
    an org-wide scope still searches as Bob, in Bob's pinned sources."""
    retrieval = FakeRetrieval()
    service, bob = _explicit_call("bob", retrieval)
    bob.session.update({"user_id": "alice", "filters": {"apps": [], "kb": []}, "is_placeholder_agent": True})
    with rendering():
        out = await service.search(bob, SearchRequest(query="launch plan"))
    assert "ALPHA7319" not in out
    (only,) = retrieval.calls
    assert only["user_id"] == "bob" and only["filter_groups"] == {"apps": [DRIVE, SLACK], "kb": [KB]}


async def test_c3_17_the_loops_tool_searches_through_the_service_into_the_runs_citation_state() -> None:
    retrieval = FakeRetrieval()
    state = chat_state("alice", retrieval)
    with rendering():
        out = await KnowledgeGraph(state).search(query="launch plan")  # type: ignore[arg-type]
    assert "ALPHA7319" in str(out)
    assert _refs(state), "the refs the result labels are in the run's own citation state"


# ---- through the tool gateway ----


class _Sink(JournalCitationSink):
    """The engine's citation sink over the run's state, as ``durable.py`` builds it."""

    def __init__(self, state: dict[str, Any]) -> None:
        journal = CitationJournal(SimpleNamespace(tool_state=state, include_retrieval_context=False))

        def citations_for(invocation_id: str) -> NewEvent | None:
            delta = journal.delta(invocation_id)
            return NewEvent(CITATIONS_EVENT, delta, critical=False) if delta is not None else None

        super().__init__(state, citations_for)


def _gateway(state: dict[str, Any]) -> tuple[RunToolEndpoint, RequestEffectLog]:
    registry = ToolRegistry()
    ToolsetBuilder(
        KnowledgeGraph(state), name="knowledgegraph", description="Knowledge", path_prefix="/tools/knowledgegraph"
    ).register_into(registry)
    effects = {n: classify_tool_effect(n, [Tag("category", "read")]) for n in (SEARCH, LIST_FILES)}
    log = RequestEffectLog()

    def allow(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "allow"

    endpoint = RunToolEndpoint(
        binding=BINDING,
        catalog=RegistryToolPort(registry, effects),
        effects=EffectRunner(log, allow),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=_Sink(state),
    )
    return endpoint, log


async def _call(endpoint: RunToolEndpoint, name: str, **arguments: Any) -> McpResult:  # noqa: ANN401
    with rendering():
        return await endpoint.call_tool(claims_for(BINDING), name, arguments)


async def test_parity_through_the_gateway_an_engine_reads_what_the_loop_reads() -> None:
    """An engine's ``knowledgegraph__search`` through the gateway answers the loop's text for the same actor."""
    loop_state = chat_state("alice", FakeRetrieval())
    with rendering():
        loop = await execute_search(loop_state, "launch plan")  # type: ignore[arg-type]
    endpoint, _ = _gateway(chat_state("alice", FakeRetrieval()))
    engine = await _call(endpoint, SEARCH, query="launch plan")
    assert not engine.is_error
    assert engine.text.endswith(str(loop))
    assert {c.ref for c in engine.citations} == {"ref1", "ref2", "ref3"}


# ---- KN-04 (C3.20): filters, server-side, through the gateway ----


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"source_ids": [SLACK]}, {"apps": [SLACK], "kb": []}),
        ({"source_ids": [KB]}, {"apps": [], "kb": [KB]}),
        ({"source_ids": [DRIVE, KB]}, {"apps": [DRIVE], "kb": [KB]}),
    ],
    ids=["connector", "kb", "connector-and-kb"],
)
async def test_kn04_source_filters_reach_retrieval_server_side(arguments: dict[str, Any], expected: dict[str, list[str]]) -> None:
    retrieval = FakeRetrieval()
    endpoint, _ = _gateway(chat_state("alice", retrieval))
    result = await _call(endpoint, SEARCH, query="plan", **arguments)
    assert not result.is_error, result.text
    assert retrieval.calls and all(c["filter_groups"] == expected for c in retrieval.calls)
    if expected["apps"] == [SLACK]:
        assert "SLACK4410" in result.text and "ALPHA7319" not in result.text and "KB5501" not in result.text


async def test_kn04_a_date_filter_bounds_the_search_server_side() -> None:
    retrieval = FakeRetrieval()
    endpoint, _ = _gateway(chat_state("alice", retrieval))
    result = await _call(
        endpoint, SEARCH, query="plan", created_after="2026-03-01", created_before="2026-03-20", source_ids=[DRIVE]
    )
    assert not result.is_error, result.text
    # The search runs inside the dates; its one undated companion only names what lies outside them (outside_dates).
    bounded, *companions = [c["time_range"] for c in retrieval.calls]
    assert bounded == {"source_created_after_ms": 1772323200000, "source_created_before_ms": 1774051199999}
    assert companions == [None]


async def test_kn04_a_record_type_filter_reaches_the_listing_server_side() -> None:
    retrieval = FakeRetrieval()
    state = chat_state("alice", retrieval)
    endpoint, _ = _gateway(state)
    seen: list[dict[str, Any]] = []

    async def get_nodes(self: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        from app.connectors.sources.localKB.api.knowledge_hub_models import (
            KnowledgeHubNodesResponse,
        )

        seen.append(kwargs)
        return KnowledgeHubNodesResponse(success=True, items=[])

    from unittest.mock import patch

    with patch(
        "app.connectors.sources.localKB.handlers.knowledge_hub_service.KnowledgeHubService.get_nodes", get_nodes
    ), patch(
        "app.agents.actions.knowledge_graph.ops.scope.list_selected_nodes", return_value=None
    ) as _:
        result = await _call(endpoint, LIST_FILES, query="plan", record_types=["file", "TICKET"])
    assert not result.is_error, result.text
    assert seen and seen[0]["record_types"] == ["FILE", "TICKET"]


@pytest.mark.parametrize(
    ("name", "arguments", "names"),
    [
        (SEARCH, {"query": "plan", "source_ids": ["confluence-9"]}, ("confluence-9", DRIVE, SLACK, KB)),
        (SEARCH, {"query": "plan", "created_after": "last tuesday"}, ("created_after",)),
        (SEARCH, {"query": "plan", "response_format": "verbose"}, ("response_format", CONCISE, DETAILED)),
        (LIST_FILES, {"query": "plan", "record_types": ["SPREADSHEET"]}, ("SPREADSHEET", "FILE", "TICKET")),
    ],
    ids=["unknown-source", "bad-date", "bad-response-format", "unknown-record-type"],
)
async def test_kn04_an_invalid_filter_is_an_actionable_error_and_searches_nothing(
    name: str, arguments: dict[str, Any], names: tuple[str, ...]
) -> None:
    retrieval = FakeRetrieval()
    endpoint, _ = _gateway(chat_state("alice", retrieval))
    result = await _call(endpoint, name, **arguments)
    text = result.text
    assert result.is_error or '"status": "error"' in text, text
    for word in names:
        assert word in text, (word, text)
    assert retrieval.calls == []


# ---- KN-06 (C3.22): response_format ----


def test_kn06_concise_is_the_default_and_detailed_widens_the_result() -> None:
    concise = search_budget(None, targeted=True, limit=50, cap=64_000)
    assert concise == search_budget(CONCISE, targeted=True, limit=50, cap=64_000)
    assert (concise.max_chars, concise.per_record) == (search_max_chars(), passages_per_record(50))
    detailed = search_budget(DETAILED, targeted=True, limit=50, cap=64_000)
    assert detailed.max_chars == 64_000 > concise.max_chars
    assert detailed.per_record == min(MAX_PASSAGES_PER_RECORD, 2 * concise.per_record)
    assert response_format_error(None) is None and response_format_error(DETAILED) is None
    assert "concise, detailed" in json.loads(response_format_error("full") or "{}")["message"]


async def test_kn06_the_search_tool_offers_response_format_and_honours_detailed() -> None:
    from unittest.mock import patch

    endpoint, _ = _gateway(chat_state("alice", FakeRetrieval()))
    (schema,) = [t for t in endpoint.granted_tools() if t.name == SEARCH]
    prop = schema.input_schema["properties"]["response_format"]  # type: ignore[index]
    assert prop["enum"] == [CONCISE, DETAILED]  # type: ignore[index]
    seen: list[str | None] = []

    def spy(response_format: str | None, **kwargs: Any) -> Any:  # noqa: ANN401
        seen.append(response_format)
        return search_budget(response_format, **kwargs)

    with patch("app.agents.actions.knowledge_graph.ops.search.search_budget", spy):
        assert not (await _call(endpoint, SEARCH, query="plan", response_format=DETAILED)).is_error
        assert not (await _call(endpoint, SEARCH, query="plan again")).is_error
    assert seen == [DETAILED, None]


# ---- KN-08 (C3.23): untrusted provenance and the session taint ----


def test_kn08_outsider_written_sources_are_named_from_the_citation_evidence() -> None:
    data: dict[str, Any] = {
        "records": {
            "vr-s": {"connector_name": "SLACK"},
            "vr-a": {"connector_name": "DRIVE"},
            "vr-m": {"metadata": {"connector": "GMAIL"}},
        },
        "finalResults": [{"metadata": {"connector": "Connectors.WEB"}}, {"metadata": {"connector": "KB"}}],
    }
    assert untrusted_in(data) == ("Slack", "Gmail", "the web")
    assert untrusted_in({"records": {"vr-a": {"connector_name": "DRIVE"}}, "finalResults": []}) == ()
    assert untrusted_in({"records": {"vr-x": {"connector_name": "SOMETHING NEW"}}}) == ("an unknown source",)
    assert untrusted_in({"webRecords": [{"url": "https://example.com"}]}) == ("the web",)
    event = NewEvent(CITATIONS_EVENT, data, critical=False)
    assert untrusted_sources((event,)) == ("Slack", "Gmail", "the web")
    assert untrusted_integrity((event,)) == "external"
    assert untrusted_integrity((NewEvent(CITATIONS_EVENT, {"records": {}}, critical=False),)) is None


async def test_kn08_a_slack_result_through_the_gateway_is_marked_untrusted_and_labelled_external() -> None:
    endpoint, log = _gateway(chat_state("alice", FakeRetrieval()))
    slack = await _call(endpoint, SEARCH, query="standup", source_ids=[SLACK])
    assert slack.untrusted_sources == ("Slack",)
    assert slack.text.startswith("[Untrusted content: some of these results come from Slack")
    assert slack.structured()["provenance"] == {"integrity": "external", "untrustedSources": ["Slack"]}
    drive = await _call(endpoint, SEARCH, query="plan", source_ids=[DRIVE])
    assert drive.untrusted_sources == () and "Untrusted" not in drive.text and "provenance" not in drive.structured()
    assert [o.integrity for o in log.outcomes()] == ["external", "internal"]


def test_kn08_the_loops_port_labels_an_outsider_written_result_external() -> None:
    from harness_core.kernel.durable import ToolContext

    registry = ToolRegistry()
    state = chat_state("alice", FakeRetrieval())
    ToolsetBuilder(
        KnowledgeGraph(state), name="knowledgegraph", description="Knowledge", path_prefix="/tools/knowledgegraph"
    ).register_into(registry)
    journal = CitationJournal(SimpleNamespace(tool_state=state, include_retrieval_context=False))

    async def present(call: ToolCall, result: Any) -> tuple[NewEvent, ...]:  # noqa: ANN401
        delta = journal.delta(call.id)
        return (NewEvent(CITATIONS_EVENT, delta, critical=False),) if delta else ()

    port = RegistryToolPort(
        registry,
        {SEARCH: classify_tool_effect(SEARCH, [Tag("category", "read")])},
        present=present,
        rate=untrusted_integrity,
    )
    ctx = ToolContext("run1", ORG, "t1.0", "a1", "org-1:run1:t1.0")

    async def run(source: str) -> str:
        with rendering():
            execution = await port.execute(ToolCall(id=f"c-{source}", name=SEARCH, arguments={"query": "q", "source_ids": [source]}), ctx)
        return execution.integrity

    import asyncio

    assert asyncio.run(run(DRIVE)) == "internal"
    assert asyncio.run(run(SLACK)) == "external"


def _event(seq: int, type_: str, data: dict[str, Any], run_id: str = "run-1") -> JournalEvent:
    return JournalEvent(
        org_id=ORG, session_id="session-1", stream_id="session-1", seq=seq, run_id=run_id, type=type_,
        critical=False, ts="2026-10-27T00:00:00Z", writer="worker", data=data,
    )


def test_kn08_the_session_taint_is_set_by_an_untrusted_read_and_feeds_the_flow_rule() -> None:
    """The Rule of Two: with a flow rule that asks only above private, a send after a private read runs, and the
    same send after the session also read Slack asks a person."""
    from harness_core.contracts.generated.sink import SinkDescriptor
    from harness_core.effects.types import ToolEffect

    ledger = SessionSourceLedger()
    ledger.apply([
        _event(1, "run.admitted", {}),
        _event(2, CITATIONS_EVENT, {"records": {"vr-a": {"recordId": "rec-a", "connector_name": "DRIVE"}}}),
    ])
    assert ledger.untrusted_read("run-1") is False
    send = ToolEffect("gmail@default/send", "write", True, SinkDescriptor(egress="external", args_leave_boundary=True))
    call = ToolCall(id="c1", name="gmail__send", arguments={"to": "x@outside.example"})

    def allow(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "allow"

    policy = FlowGuardedPolicy(allow, FlowRule("ask", above="private"), ledger, "run-1")
    assert policy(call, send) == "allow"
    ledger.apply([_event(3, CITATIONS_EVENT, {"records": {"vr-s": {"recordId": "rec-s", "connector_name": "SLACK"}}})])
    assert ledger.untrusted_read("run-1") is True
    assert policy(call, send) == "ask"


def test_kn08_an_external_tool_outcome_taints_the_session_too() -> None:
    ledger = SessionSourceLedger()
    ledger.apply([
        _event(1, "run.admitted", {}),
        _event(2, "tool.outcome", {"invocationId": "i1", "status": "ok", "labels": {"integrity": "internal"}}),
    ])
    assert ledger.untrusted_read("run-1") is False
    ledger.apply([_event(3, "tool.outcome", {"invocationId": "i2", "status": "ok", "labels": {"integrity": "external"}})])
    assert ledger.untrusted_read("run-1") is True
