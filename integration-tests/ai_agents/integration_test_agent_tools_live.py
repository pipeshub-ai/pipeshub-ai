"""An Agent Builder agent with knowledge and an external MCP tool, used for real.

CTO list: Agent Builder, MCPs, Toolsets. The agent has the suite's indexed
document as its knowledge and the ``mcp-fixture`` server's order tool. A
policy question should be answered from the document with citations and no
tool call; an order question should call the tool, and the MCP server's own
call log must show the request arrived. Assertions are on stream events and
the saved conversation, never on the answer's wording.

Connector toolsets (Slack, Jira, ...) each need that service's credentials, so
the tool here is the external MCP server, attached the way Agent Builder
attaches one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from ai_agents.support import json_body, stream_agent
from helper.agui_run import RunTrace
from helper.clients.agents_client import AgentsClient
from helper.clients.conversations_client import AgentConversationsClient
from helper.mcp_fixture import ORDER_TOOL, McpFixture

pytestmark = [pytest.mark.integration, pytest.mark.ai_agents, pytest.mark.mcp]

_ORDER_ID = "KO-5821"


def _delete(agent_conversations: AgentConversationsClient, agent_key: str, trace: RunTrace) -> None:
    if trace.conversation_id:
        agent_conversations.delete_conversation(agent_key, trace.conversation_id)


@pytest.fixture(scope="module")
def knowledge_turn(
    agent_conversations_client: AgentConversationsClient,
    kb_mcp_agent: dict[str, Any],
    it_document: dict[str, str],
    mcp_fixture: McpFixture,
) -> Iterator[tuple[RunTrace, list[dict[str, Any]]]]:
    mcp_fixture.reset()
    trace = stream_agent(agent_conversations_client, kb_mcp_agent["agent_key"], it_document["question"])
    calls = mcp_fixture.calls()
    try:
        yield trace, calls
    finally:
        _delete(agent_conversations_client, kb_mcp_agent["agent_key"], trace)


@pytest.fixture(scope="module")
def order_turn(
    agent_conversations_client: AgentConversationsClient,
    kb_mcp_agent: dict[str, Any],
    mcp_fixture: McpFixture,
) -> Iterator[tuple[RunTrace, list[dict[str, Any]]]]:
    mcp_fixture.reset()
    trace = stream_agent(
        agent_conversations_client, kb_mcp_agent["agent_key"],
        f"Where is customer order {_ORDER_ID} right now?",
    )
    calls = mcp_fixture.calls()
    try:
        yield trace, calls
    finally:
        _delete(agent_conversations_client, kb_mcp_agent["agent_key"], trace)


class TestAgentBuilderAgent:
    def test_the_agent_is_saved_with_its_knowledge_and_tool(
        self, agents_client: AgentsClient, kb_mcp_agent: dict[str, Any],
        it_document: dict[str, str], mcp_instance: dict[str, Any],
    ) -> None:
        resp = agents_client.get_agent(kb_mcp_agent["agent_key"])
        assert resp.status_code == 200, resp.text[:500]
        agent = json_body(resp).get("agent") or json_body(resp)
        knowledge = agent.get("knowledge") or []
        assert any(
            isinstance(k, dict) and it_document["kb_id"] in str(k.get("connectorId", "")) for k in knowledge
        ), knowledge
        servers = agent.get("mcpServers") or []
        assert any(
            isinstance(s, dict) and s.get("instanceId") == mcp_instance["_id"]
            and any(t.get("name") == ORDER_TOOL for t in s.get("tools") or [])
            for s in servers
        ), servers


class TestKnowledgeQuestion:
    def test_it_answers_from_the_knowledge_source_with_citations(
        self, knowledge_turn, it_document: dict[str, str]
    ) -> None:
        trace, _calls = knowledge_turn
        assert trace.finished and not trace.error, trace.describe()
        cited = {
            ((c.get("citationData") or {}).get("metadata") or {}).get("recordId") for c in trace.citations
        }
        assert it_document["record_id"] in cited, f"cited records {cited}; {trace.describe()}"

    def test_it_does_not_call_the_order_tool(self, knowledge_turn) -> None:
        trace, calls = knowledge_turn
        assert not trace.calls_ending_with(ORDER_TOOL), trace.describe()
        assert calls == [], f"the MCP server was called for a policy question: {calls}"


class TestToolQuestion:
    def test_it_calls_the_mcp_tool(self, order_turn, mcp_instance: dict[str, Any]) -> None:
        trace, _calls = order_turn
        assert trace.finished and not trace.error, trace.describe()
        order_calls = trace.calls_ending_with(ORDER_TOOL)
        assert order_calls, f"no {mcp_instance['orderTool']} call in the stream: {trace.describe()}"
        assert any(call.status == "completed" for call in order_calls), [
            (call.name, call.status, call.result) for call in order_calls
        ]

    def test_the_mcp_server_received_the_call(self, order_turn) -> None:
        _trace, calls = order_turn
        asked = [str((c.get("arguments") or {}).get("order_id", "")) for c in calls if c.get("tool") == ORDER_TOOL]
        assert any("5821" in order_id for order_id in asked), f"calls the MCP server saw: {calls}"
