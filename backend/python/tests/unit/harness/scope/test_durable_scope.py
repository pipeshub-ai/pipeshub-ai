"""SYNC4 (MAINSTUDY §7.3, tests 1-6): main's turn scope (#4255) holds on every durable harness path.

A run is pinned with the selection the request named (``x.pipeshub.knowledge.records`` and its kin) and, for a
saved agent, the agent's per-source limits. Every attempt resolves the pin with main's ``prepare_turn_scope`` (agent
assembly through ``_resolve_turn_filters``), so the tools read the same ``state["filters"]`` today's loop builds,
and every read by id goes through main's ``check_access``. Nothing resolved is pinned: a revocation applies to the
next attempt.

The graph is ``tests/support/scope_world.py``: the backend's rows are faked, the access check is the interface's.

Written test-first, before ``origin/main`` was merged: red until S1-S6 and S12 land (the pin has no selection,
both assemblies build ``filters`` by hand, attachments trust the client's ``recordId``).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.scope import ids_outside_scope
from app.agents.run_definition import ASSISTANT_AGENT_KEY
from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import build_tool_environment
from app.modules.retrieval.selection_scope import (
    SELECTION_EMPTY_MESSAGE,
    SELECTION_TOO_LARGE_MESSAGE,
)
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver.refusal import RunRefused
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.support import scope_world as sw
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator



@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    from app.modules.retrieval import selection_scope

    selection_scope._memo.clear()  # a revocation must not hide behind another test's 30 s memo
    with w.deployment():
        yield


def _services(agent: dict[str, Any] | None = None) -> tuple[w.Services, sw.ScopeWorld]:
    services = w.Services(agent=agent if agent is not None else {})
    world = sw.ScopeWorld.build()
    world.install(services.graph)
    return services, world


def _pinned(spec: RunSpec, **knowledge: Any) -> RunSpec:  # noqa: ANN401
    """``spec`` with the knowledge pin Node writes for a request that selected below app level."""
    spec = spec.model_copy(deep=True)
    ext = copy.deepcopy((spec.extensions or {})["x.pipeshub"])
    ext["knowledge"].update(knowledge)
    spec.extensions = {**(spec.extensions or {}), "x.pipeshub": ext}
    return spec


def _assistant(**knowledge: Any) -> RunSpec:  # noqa: ANN401
    spec = w.pinned_spec(
        tool_ids=[], kb=[], agent=False, chat={"mode": "internal_search", "internalSearch": True}, strict_scope=False,
    )
    return _pinned(spec, **knowledge)


def _agent(**knowledge: Any) -> RunSpec:  # noqa: ANN401
    return _pinned(w.pinned_spec(tool_ids=[], kb=[], apps=[]), **knowledge)


async def _state(services: w.Services, spec: RunSpec, **claim: Any) -> dict[str, Any]:  # noqa: ANN401
    run = RunContext.from_claim(w.claim(spec, chat_mode="internal_search" if "chat" in _ext(spec) else "quick", **claim))
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    env = await build_tool_environment(run, services, await w.model_factory(port)(run, services))
    return env.context.tool_state


def _ext(spec: RunSpec) -> dict[str, Any]:
    return (spec.extensions or {})["x.pipeshub"]


async def _outside(services: w.Services, state: dict[str, Any], *ids: str) -> list[str]:
    return await ids_outside_scope(state, services.graph, sw.USER, sw.ORG, list(ids))


AGENT_A = {"connectorId": sw.APP_A, "type": "CONNECTOR"}


# 1. The durable loop runs inside the selection (assistant and agent).


async def test_1_the_assistant_searches_and_reads_only_inside_the_selection() -> None:
    services, _ = _services()
    state = await _state(services, _assistant(records=[sw.FOLDER]))
    filters = state["filters"]
    assert filters["records"] == [sw.FOLDER]
    assert filters["strictScope"] is True
    assert filters["selectionApps"] == [sw.APP_A]
    assert await _outside(services, state, sw.REC_IN, sw.REC_OUT, sw.REC_B) == [sw.REC_OUT, sw.REC_B]


async def test_1_an_agent_searches_and_reads_only_inside_the_selection() -> None:
    services, _ = _services(w.agent_doc(knowledge=[AGENT_A], toolsets=[], mcpServers=[]))
    state = await _state(services, _agent(apps=[], records=[sw.FOLDER]))
    filters = state["filters"]
    assert filters["records"] == [sw.FOLDER]
    assert filters["strictScope"] is True
    assert filters["selectionApps"] == [sw.APP_A]
    # The agent's own sources bound it too (main's _resolve_turn_filters binds them on every turn).
    assert filters["allowedApps"] == [sw.APP_A]
    assert await _outside(services, state, sw.REC_IN, sw.REC_OUT) == [sw.REC_OUT]


async def test_1_a_selection_the_user_cannot_read_refuses_the_run_with_mains_code() -> None:
    services, world = _services()
    world.revoke(sw.FOLDER)
    with pytest.raises(RunRefused) as refused:
        await _state(services, _assistant(records=[sw.FOLDER]))
    assert refused.value.error_type == "selection_empty"
    # main's words: the chat shows the worker's message for a code it has no text of its own for.
    assert str(refused.value) == SELECTION_EMPTY_MESSAGE


def _agent_mode(*, web: bool, **knowledge: Any) -> RunSpec:  # noqa: ANN401
    """The built-in assistant's Agent mode: Node pins the system assistant definition, and the web tool only while
    the request leaves web search on (``agentCapabilities.webSearch``)."""
    spec = _agent(**knowledge)
    _ext(spec)["agent"] = {"agentKey": ASSISTANT_AGENT_KEY, "definitionVersion": 1, "mode": "quick"}
    return _with_web(spec) if web else spec


WEB_TOOL = "web@duckduckgo/web_search"


def _with_web(spec: RunSpec) -> RunSpec:
    raw = dump_contract(spec)
    raw["grants"]["tools"] = [{"toolId": WEB_TOOL, "descriptorDigest": w.DIGEST}]
    return validate_contract(RunSpec, raw)


async def _agent_mode_state(services: w.Services, spec: RunSpec) -> dict[str, Any]:
    assistant = {"knowledge": [AGENT_A], "toolsets": [], "mcpServers": []}
    with patch("app.api.routes.agent.get_assistant_agent", AsyncMock(return_value=(assistant, {}))), \
         patch("app.api.routes.chatbot.load_system_prompts", AsyncMock(return_value={})):
        return await _state(services, spec)


async def test_1_agent_mode_without_web_search_refuses_an_unreadable_selection_with_mains_code() -> None:
    """Main's bridge refuses a turn that can only search over a selection the user cannot read (#4255)."""
    services, world = _services()
    world.revoke(sw.FOLDER)
    with pytest.raises(RunRefused) as refused:
        await _agent_mode_state(services, _agent_mode(web=False, apps=[], records=[sw.FOLDER]))
    assert refused.value.error_type == "selection_empty"
    assert str(refused.value) == SELECTION_EMPTY_MESSAGE


async def test_1_agent_mode_with_web_search_runs_an_unreadable_selection_bounded_to_it() -> None:
    """Main runs this turn: the web may still answer, and the search tool says the selection has nothing."""
    services, world = _services()
    world.revoke(sw.FOLDER)
    state = await _agent_mode_state(services, _agent_mode(web=True, apps=[], records=[sw.FOLDER]))
    assert state["filters"]["records"] == [sw.FOLDER]
    assert state["filters"]["strictScope"] is True
    assert state["has_knowledge"] is True
    assert await _outside(services, state, sw.REC_IN, sw.REC_OUT) == [sw.REC_IN, sw.REC_OUT]


async def test_1_agent_mode_reads_only_inside_a_readable_selection() -> None:
    services, _ = _services()
    state = await _agent_mode_state(services, _agent_mode(web=False, apps=[], records=[sw.FOLDER]))
    assert state["filters"]["selectionApps"] == [sw.APP_A]
    assert await _outside(services, state, sw.REC_IN, sw.REC_OUT) == [sw.REC_OUT]


async def test_1_a_selection_too_large_refuses_the_run_with_mains_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "1")
    services, _ = _services(w.agent_doc(knowledge=[AGENT_A], toolsets=[], mcpServers=[]))
    with pytest.raises(RunRefused) as refused:
        await _state(services, _agent(apps=[], recordGroups=[sw.GRP_1, sw.GRP_2]))
    assert refused.value.error_type == "selection_too_large"
    assert str(refused.value) == SELECTION_TOO_LARGE_MESSAGE


async def test_1_a_fetch_by_id_outside_the_selection_is_a_typed_refusal_naming_the_id() -> None:
    """The model is told which id it may not read, in main's words, which never say whether it exists (QA27);
    the person's stream carries the typed code, whose words it shows instead of the detail."""
    from types import SimpleNamespace

    from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record

    services, _ = _services()
    state = await _state(services, _assistant(records=[sw.REC_IN]))
    context = SimpleNamespace(
        org_id=sw.ORG, user_id=sw.USER, graph_provider=services.graph, full_records_fetched=set(),
        tool_state=state, is_multimodal_llm=False, context_length=128_000, query="", retrieval_service=None,
    )
    output, _ = await execute_fetch_record(
        context=context, virtual_records={}, citation_ref_mapper=None, record_ids=[sw.REC_OUT],
    )
    assert output.success is False
    assert output.error_code == "not_found"
    assert output.error.startswith(
        f"None of the requested records were available.\n\nNote: '{sw.REC_OUT}': not available"
    )


# 2. A saved agent's source limit bounds reads by id, with no selection.


async def test_2_an_agents_source_limit_bounds_reads_by_id_without_a_selection() -> None:
    limited = {**AGENT_A, "filters": {"recordGroups": [sw.GRP_2], "records": []}}
    services, _ = _services(w.agent_doc(knowledge=[limited], toolsets=[], mcpServers=[]))
    state = await _state(
        services,
        _agent(apps=[sw.APP_A], sourceLimits=[{"connectorId": sw.APP_A, "recordGroups": [sw.GRP_2], "records": []}]),
    )
    assert state["filters"]["allowedRecordGroups"] == [sw.GRP_2]
    assert await _outside(services, state, sw.REC_G2, sw.REC_IN) == [sw.REC_IN]


async def test_s12_an_owner_widening_a_limit_mid_run_never_widens_the_run() -> None:
    """The live agent now holds the whole source; the run was admitted with it limited to grp-2."""
    services, _ = _services(w.agent_doc(knowledge=[AGENT_A], toolsets=[], mcpServers=[]))
    state = await _state(
        services,
        _agent(apps=[sw.APP_A], sourceLimits=[{"connectorId": sw.APP_A, "recordGroups": [sw.GRP_2], "records": []}]),
    )
    assert state["filters"]["allowedRecordGroups"] == [sw.GRP_2]
    assert await _outside(services, state, sw.REC_IN) == [sw.REC_IN]


async def test_s12_an_owner_narrowing_a_limit_mid_run_applies_at_the_next_attempt() -> None:
    narrowed = {**AGENT_A, "filters": {"recordGroups": [sw.GRP_2], "records": []}}
    services, _ = _services(w.agent_doc(knowledge=[narrowed], toolsets=[], mcpServers=[]))
    state = await _state(
        services,
        _agent(apps=[sw.APP_A], sourceLimits=[{"connectorId": sw.APP_A, "recordGroups": [sw.GRP_1, sw.GRP_2], "records": []}]),
    )
    assert state["filters"]["allowedRecordGroups"] == [sw.GRP_2]


# 3. An engine run through the gateway gets the same environment.


async def test_3_an_engine_run_reads_by_id_only_inside_the_selection() -> None:
    services, _ = _services(w.agent_doc(knowledge=[AGENT_A], toolsets=[], mcpServers=[]))
    spec = _pinned(
        w.pinned_spec(tool_ids=[], kb=[], apps=[], executor={"kind": "engine", "engine": {
            "kind": "claude_code", "version": "1", "binaryDigest": w.DIGEST, "profileDigest": w.DIGEST,
        }}),
        records=[sw.FOLDER],
    )
    state = await _state(services, spec)
    assert state["filters"]["records"] == [sw.FOLDER]
    assert await _outside(services, state, sw.REC_IN, sw.REC_OUT) == [sw.REC_OUT]


# 4. A child run keeps its parent's selection (Node writes the child's pin: scope-pin.json `children`).


async def test_4_a_child_pinned_with_its_parents_selection_reads_only_inside_it() -> None:
    services, _ = _services(w.agent_doc(knowledge=[AGENT_A], toolsets=[], mcpServers=[]))
    child = _pinned(_agent(), records=[sw.FOLDER])
    ext = _ext(child)
    ext["subAgent"] = {"role": "researcher", "toolNames": []}
    state = await _state(services, child)
    assert state["filters"]["records"] == [sw.FOLDER]
    assert await _outside(services, state, sw.REC_OUT) == [sw.REC_OUT]


# 5. Resume after a revocation: the pin is resolved again on every attempt.


async def test_5_a_resumed_attempt_drops_a_revoked_record_and_fails_an_emptied_selection() -> None:
    services, world = _services()
    spec = _assistant(records=[sw.REC_IN, sw.REC_G2])
    first = await _state(services, spec)
    assert await _outside(services, first, sw.REC_IN, sw.REC_G2) == []

    world.revoke(sw.REC_IN)
    # Every read by id is checked when it happens: no new attempt is needed for a revocation to apply.
    assert await _outside(services, first, sw.REC_IN, sw.REC_G2) == [sw.REC_IN]
    second = await _state(services, spec)
    assert await _outside(services, second, sw.REC_IN, sw.REC_G2) == [sw.REC_IN]

    world.revoke(sw.REC_G2)
    # A later attempt (main memoizes a resolved selection for 30 s per process; attempts may land on any worker).
    from app.modules.retrieval import selection_scope

    selection_scope._memo.clear()
    with pytest.raises(RunRefused) as refused:
        await _state(services, spec)
    assert refused.value.error_type == "selection_empty"


async def test_5_the_raw_read_gate_decides_on_the_one_access_check() -> None:
    """``RecordReadGate`` (fetch_full_record, Slack threads) asks main's ``check_access``, not a copy of the old ACL."""
    from app.harness.knowledge.read_gate import RecordReadGate

    class Off:
        async def enabled(self) -> bool:
            return False

    services, world = _services()
    gate = RecordReadGate(services.graph, sw.ORG, sw.USER, publications=Off())  # type: ignore[arg-type]
    assert await gate.decide_many([sw.REC_IN, sw.REC_B]) == {sw.REC_IN: "own", sw.REC_B: "own"}
    world.revoke(sw.GRP_B)
    assert await gate.decide_many([sw.REC_IN, sw.REC_B]) == {sw.REC_IN: "own", sw.REC_B: "denied"}
    assert world.asked, "the gate never reached the access rows"


# 6. Attachments: the client's recordId is never trusted.


async def test_6_a_forged_attachment_record_id_is_replaced_and_an_unreadable_one_dropped() -> None:
    services, world = _services()
    world.revoke(sw.REC_B)
    attachments = [
        {"recordId": sw.REC_B, "virtualRecordId": sw.VRID[sw.REC_IN], "recordName": "a.pdf", "mimeType": "application/pdf"},
        {"recordId": sw.REC_B, "virtualRecordId": sw.VRID[sw.REC_B], "recordName": "b.pdf", "mimeType": "application/pdf"},
    ]
    history = [{"role": "user_query", "content": "earlier", "attachments": [
        {"recordId": sw.REC_IN, "virtualRecordId": sw.VRID[sw.REC_B], "recordName": "c.pdf", "mimeType": "application/pdf"},
    ]}]
    state = await _state(services, _assistant(), attachments=attachments, previous=history)
    assert [(a["recordId"], a["virtualRecordId"]) for a in state.get("attachments") or []] == [
        (sw.REC_IN, sw.VRID[sw.REC_IN])
    ]
    (turn,) = [t for t in state.get("previous_conversations") or [] if t.get("content") == "earlier"]
    assert not turn.get("attachments")


async def test_6_an_agent_runs_attachments_through_the_same_check() -> None:
    services, world = _services(w.agent_doc(knowledge=[AGENT_A], toolsets=[], mcpServers=[]))
    attachments = [
        {"recordId": sw.REC_OUT, "virtualRecordId": sw.VRID[sw.REC_IN], "recordName": "a.pdf", "mimeType": "application/pdf"},
    ]
    state = await _state(services, _agent(apps=[sw.APP_A]), attachments=attachments)
    assert [a["recordId"] for a in state.get("attachments") or []] == [sw.REC_IN]
