"""An Agent Builder agent that calls an internal REST API added as an API Toolset.

The ``openapi-fixture`` service (Acme Helpdesk) is added from its own OpenAPI spec with a
shared API key, and the agent may call two of its operations. A ticket question should
call the API; the API's own call log must show the request arrived with the API key.
Assertions are on stream events, the call log and one seeded fact, never on wording.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ai_agents.support import API_TOOLSETS, json_body, stream_agent
from helper.agui_run import RunTrace
from helper.clients.agents_client import AgentsClient
from helper.clients.conversations_client import AgentConversationsClient
from helper.openapi_fixture import OpenApiFixture

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pipeshub_client import PipeshubClient

# One worker: the tests share the API's call log and the platform flag their fixtures reset.
pytestmark = [pytest.mark.integration, pytest.mark.ai_agents, pytest.mark.xdist_group("api_toolsets")]

# Seeded in the fixture's data: no model could know who is working on it.
_TICKET = "ACME-7311"
_ASSIGNEE_PARTS = ("noor", "rahimi")


@pytest.fixture(scope="module")
def ticket_turn(
    agent_conversations_client: AgentConversationsClient,
    api_toolset_agent: dict[str, Any],
    openapi_fixture: OpenApiFixture,
) -> Iterator[tuple[RunTrace, list[dict[str, Any]]]]:
    openapi_fixture.reset()
    trace = stream_agent(
        agent_conversations_client, api_toolset_agent["agent_key"],
        f"Who is assigned to helpdesk ticket {_TICKET}, and what is its status?",
    )
    calls = openapi_fixture.calls()
    try:
        yield trace, calls
    finally:
        if trace.conversation_id:
            agent_conversations_client.delete_conversation(api_toolset_agent["agent_key"], trace.conversation_id)


class TestTheToolset:
    def test_members_see_it_connected_through_the_shared_key(
        self, pipeshub_client: PipeshubClient, api_toolset: dict[str, Any],
    ) -> None:
        resp = pipeshub_client.request("GET", f"{API_TOOLSETS}/instances")
        assert resp.status_code == 200, resp.text[:500]
        listed = next(i for i in json_body(resp)["instances"] if i["id"] == api_toolset["id"])
        assert listed["isConnected"] is True and listed["useSharedCredential"] is True
        assert [op["toolName"] for op in listed["operations"]] == ["list_tickets", "get_ticket"]
        assert "baseUrl" not in listed

    def test_the_agent_is_saved_with_its_operations(
        self, agents_client: AgentsClient, api_toolset_agent: dict[str, Any],
    ) -> None:
        resp = agents_client.get_agent(api_toolset_agent["agent_key"])
        assert resp.status_code == 200, resp.text[:500]
        agent = json_body(resp).get("agent") or json_body(resp)
        assert agent.get("apiToolsetTools") == api_toolset_agent["payload"]["apiToolsetTools"]

    def test_an_unknown_operation_is_refused(self, agents_client: AgentsClient, api_toolset: dict[str, Any]) -> None:
        resp = agents_client.create_agent(name="it-api-toolset-refused", apiToolsetTools=[f"{api_toolset['id']}:create_ticket"])
        if resp.status_code < 300:
            agents_client.delete_agent((json_body(resp).get("agent") or json_body(resp)).get("_key", ""))
        assert resp.status_code == 400 and "create_ticket" in resp.text, resp.text[:500]


class TestTicketQuestion:
    def test_it_calls_the_api_tool(self, ticket_turn: tuple[RunTrace, list[dict[str, Any]]]) -> None:
        trace, _calls = ticket_turn
        assert trace.finished and not trace.error, trace.describe()
        lookups = trace.calls_ending_with("get_ticket") + trace.calls_ending_with("list_tickets")
        assert lookups, f"no Acme Helpdesk call in the stream: {trace.describe()}"
        assert any(call.status == "completed" for call in lookups), [(c.name, c.status, c.result) for c in lookups]

    def test_the_api_received_the_call_with_the_shared_key(self, ticket_turn: tuple[RunTrace, list[dict[str, Any]]]) -> None:
        _trace, calls = ticket_turn
        assert calls, "the Acme Helpdesk API received no call"
        assert {c["auth"] for c in calls} == {"apiKey"}, calls
        assert any(
            c["path"] == f"/tickets/{_TICKET}" or (c["path"] == "/tickets" and c["method"] == "GET") for c in calls
        ), calls

    def test_the_answer_uses_what_only_the_api_knows(self, ticket_turn: tuple[RunTrace, list[dict[str, Any]]]) -> None:
        trace, _calls = ticket_turn
        answer = (trace.answer or "").lower()
        assert all(part in answer for part in _ASSIGNEE_PARTS), trace.describe()
