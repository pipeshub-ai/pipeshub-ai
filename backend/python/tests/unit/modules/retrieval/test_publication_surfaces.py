"""P2.13 SF-1..SF-3: every knowledge surface reaches the publication scope with the right principal.

Every surface ends in ``RetrievalService.search_with_filters`` (RS-* decide the scope there): the agent and
assistant knowledge tools, which the durable worker serves to engines through the run tool gateway; prefetch;
``POST /api/v1/search``, which MCP's ``pipeshub_search`` reaches through Node; and child runs, which carry their
parent's actor. What each surface must get right is *whose* scope it asks for.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.agents.actions.retrieval.retrieval import Retrieval
from app.api.routes.agent import _resolve_service_account_caller_identity
from app.harness.knowledge.caller import UNRESOLVED_CALLER, caller_user_id_of
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal import InMemoryJournal
from harness_core.journal.children import ChildGrantRequest, ChildRefused, ChildRequest
from harness_core.testing.run_spec import minimal_run_spec


def _state(retrieval_service: Any, user_info: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ANN401
    return {
        "org_id": "org-1",
        "user_id": "creator",
        "limit": 50,
        "filters": {"apps": [], "kb": []},
        "retrieval_service": retrieval_service,
        "graph_provider": AsyncMock(),
        "config_service": AsyncMock(),
        "logger": MagicMock(),
        "llm": None,
        "user_info": user_info or {},
    }


def _service() -> AsyncMock:
    service = AsyncMock()
    service.search_with_filters = AsyncMock(
        return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
    )
    return service


async def test_sf1_a_service_account_request_keeps_the_real_caller() -> None:
    graph = AsyncMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"email": "bob@example.com", "fullName": "Bob"})
    creator = {"userId": "creator", "orgId": "org-1"}
    named = MagicMock(callerDisplayName="Bob", callerEmail="bob@example.com")
    out = await _resolve_service_account_caller_identity(creator, named, {"userId": "bob", "orgId": "org-1"}, graph, MagicMock())
    assert (out["userId"], out["callerUserId"]) == ("creator", "bob")
    unnamed = MagicMock(callerDisplayName=None, callerEmail=None)
    out = await _resolve_service_account_caller_identity(creator, unnamed, {"userId": "bob", "orgId": "org-1"}, graph, MagicMock())
    assert (out["userId"], out["callerUserId"]) == ("creator", "bob")
    assert caller_user_id_of(out) == "bob"
    # A request without a user: the creator's clearance is bounded by nobody's, so nothing (RS-6).
    anonymous = await _resolve_service_account_caller_identity(creator, unnamed, {"orgId": "org-1"}, graph, MagicMock())
    assert caller_user_id_of(anonymous) == UNRESOLVED_CALLER
    assert caller_user_id_of({}) is None
    assert caller_user_id_of({"callerUserId": ""}) is None


@pytest.mark.parametrize(("user_info", "caller"), [({"callerUserId": "bob"}, "bob"), ({}, None)])
async def test_sf1_the_knowledge_tools_pass_the_caller(user_info: dict[str, Any], caller: str | None) -> None:
    service = _service()
    _ = await Retrieval(state=_state(service, user_info)).search_internal_knowledge(query="policy")
    kwargs = service.search_with_filters.await_args.kwargs
    assert (kwargs["user_id"], kwargs["caller_user_id"]) == ("creator", caller)

    service = _service()
    _ = await execute_search(_state(service, user_info), query="policy")
    kwargs = service.search_with_filters.await_args.kwargs
    assert (kwargs["user_id"], kwargs["caller_user_id"]) == ("creator", caller)


@pytest.mark.parametrize(("user_info", "caller"), [({"callerUserId": "bob"}, "bob"), ({}, None)])
async def test_sf1_prefetch_searches_with_the_caller(user_info: dict[str, Any], caller: str | None) -> None:
    from app.agents.chat_modes.setup import start_prefetch
    from app.utils.chat_helpers import CitationRefMapper

    service = _service()
    setup = SimpleNamespace(
        query_info={"query": "policy", "filters": {}, "limit": 5}, blob_store=MagicMock(), ref_mapper=CitationRefMapper()
    )
    context = SimpleNamespace(org_id="org-1", user_id="creator", user_info=user_info, tool_state={})
    started = start_prefetch(
        setup, context, SimpleNamespace(prefetch_retrieval=True),  # type: ignore[arg-type]
        retrieval_service=service, graph_provider=AsyncMock(), is_multimodal_llm=False, log=MagicMock(),
    )
    assert started is not None
    _ = await started
    kwargs = service.search_with_filters.await_args.kwargs
    assert (kwargs["user_id"], kwargs["caller_user_id"]) == ("creator", caller)


async def test_sf1_a_service_account_durable_run_acts_for_the_real_caller() -> None:
    """The run tool gateway serves the registry the durable worker builds from this identity."""
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import _identity
    from tests.unit.harness.assembly.world import ORG, agent_doc, claim, pinned_spec

    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[]), caller={"email": "bob@example.com", "displayName": "Bob"}))
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"_key": "creator-key", "userId": "creator", "orgId": ORG, "email": "c@x", "fullName": "C"})
    graph.get_user_by_user_id = AsyncMock(return_value=None)
    info, service_account = await _identity(
        run, agent_doc(isServiceAccount=True, createdBy="creator-key"), graph, logging.getLogger("t")
    )
    assert service_account is True
    assert info["userId"] == "creator"
    assert caller_user_id_of(info) == run.actor_id


async def test_sf2_the_search_api_searches_as_the_token_user() -> None:
    """The token's user reads as themself: own and published records (RS-1, RS-2), with no other caller."""
    from app.api.routes.search import SearchQuery, search

    service = MagicMock()
    service.llm = MagicMock()
    service.search_with_filters = AsyncMock(return_value={"searchResults": [], "status_code": 200})
    request = MagicMock()
    request.state.user = {"userId": "token-user", "orgId": "org-1", "callerUserId": "someone-else"}
    chain = MagicMock(ainvoke=AsyncMock(return_value="policy"))
    # The model comes from the search LLM extension point, whichever resolver it holds.
    with patch("app.api.routes.search.resolve_llm_for_search", new=AsyncMock(return_value=service.llm)), patch(
        "app.api.routes.search.setup_query_transformation", return_value=(chain, chain)
    ):
        _ = await search(request=request, body=SearchQuery(query="policy"), retrieval_service=service, graph_provider=MagicMock())
    kwargs = service.search_with_filters.await_args.kwargs
    assert (kwargs["org_id"], kwargs["user_id"], kwargs.get("caller_user_id")) == ("org-1", "token-user", None)


EXTENSION: dict[str, Any] = {
    "knowledge": {"apps": ["app-1"], "kb": ["kb-hr", "kb-eng"], "strictScope": False},
    "mcp": [],
    "skills": [],
    "mounts": [],
    "via": "session",
}


async def test_sf3_a_child_run_inherits_no_more_than_its_parent() -> None:
    spec = dump_contract(minimal_run_spec(max_children=2))
    spec["extensions"] = {"x.pipeshub": EXTENSION}
    journal = InMemoryJournal()
    journal.admit("parent", org_id="o", session_id="s1", spec=validate_contract(RunSpec, spec), input_text="go")
    claim = await journal.claim("parent", worker_id="w")
    assert claim is not None
    narrowed = await journal.spawn(claim.token, ChildRequest("t1.0", "hr", ChildGrantRequest(knowledge_kb=("kb-hr",))))
    child = journal._runs[narrowed.child_run_id].spec  # pyright: ignore[reportPrivateUsage]
    parent = journal._runs["parent"].spec  # pyright: ignore[reportPrivateUsage]
    # The same principal (so the same audience for publications), a knowledge grant ⊆ the parent's.
    assert child["principalRef"] == parent["principalRef"]
    assert child["extensions"]["x.pipeshub"]["knowledge"]["kb"] == ["kb-hr"]
    with pytest.raises(ChildRefused):
        await journal.spawn(claim.token, ChildRequest("t2.0", "all", ChildGrantRequest(knowledge_kb=("kb-hr", "kb-finance"))))

