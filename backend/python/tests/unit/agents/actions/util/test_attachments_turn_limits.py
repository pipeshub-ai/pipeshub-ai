"""R1-03: the outbound tools (Gmail, Outlook, Salesforce, Slack) attach and send
records through ``resolve_attachments``. It checked only that the run-as identity
may read a record, so a limited turn (a service-account agent runs as its
creator) could send any record the creator can read. It is now held to the
turn's limits, like every tool that reaches a record by id.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.selection_scope as selection_scope
from app.agents.actions.util.attachments import resolve_attachments
from app.services.graph_db.interface.graph_db_provider import AccessCheck

ORG = "o"
CONVERSATION = "conv-1"


@pytest.fixture(autouse=True)
def _clear_memo():
    selection_scope._memo.clear()
    yield
    selection_scope._memo.clear()


class _Graph:
    """Records as ``id -> connectorId``, every one readable by the run-as user;
    ``check_access`` applies the scopes it is given as the real one does."""

    def __init__(self, rows: dict[str, str], artifacts: dict[str, str] | None = None) -> None:
        self.rows = rows
        self.artifacts = artifacts or {}
        self.get_user_by_user_id = AsyncMock(return_value={"_key": "creator-key"})
        self.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": []})
        self.check_record_access_with_details = AsyncMock(return_value={"ok": True})
        self.check_access = AsyncMock(side_effect=self._check_access)
        self.get_record_by_id = AsyncMock(side_effect=lambda rid: (
            {"_key": rid, "orgId": ORG, "recordName": f"{rid}.pdf", "isDeleted": False}
            if rid in self.rows else None
        ))
        self.get_nodes_by_field_in = AsyncMock(side_effect=lambda collection, field, ids, **_: [
            {"id": i, "orgId": ORG, "conversationId": self.artifacts[i]} for i in ids if i in self.artifacts
        ])

    async def _check_access(self, _user_key, _org, *, node_ids=(), scopes=(), **_) -> AccessCheck:
        readable = [i for i in node_ids if i in self.rows]
        return AccessCheck(
            node_ids=frozenset(readable),
            node_ids_in_scope=frozenset(
                i for i in readable
                if all(s.admits({"id": i, "connectorId": self.rows[i], "groupIds": []}) for s in scopes)
            ),
        )


async def _turn_of_a_whole_kb_agent(graph: _Graph) -> dict:
    from app.api.routes.agent import _resolve_turn_filters

    return await _resolve_turn_filters(
        agent_id="svc-agent",
        agent_knowledge=[{"connectorId": "kb-1", "type": "KB", "filters": ""}],
        requested_filters=None, graph_provider=graph, caller_user_id="caller", org_id=ORG,
        logger=logging.getLogger("t"), retrieval_user_id="creator",
    )


def _state(graph: _Graph, filters: dict | None) -> dict:
    return {
        "org_id": ORG, "user_id": "creator", "config_service": MagicMock(),
        "graph_provider": graph, "conversation_id": CONVERSATION, "blob_store": None,
        "filters": filters,
    }


async def _send(state: dict, refs: list[str]):
    with patch(
        "app.services.record_content.resolver.RecordContentResolver._fetch",
        new=AsyncMock(return_value=(b"bytes", "blob")),
    ) as fetch:
        bundle = await resolve_attachments(state, refs)
    return bundle, fetch


ROWS = {"kb-rec": "kb-1", "jira-1": "jira", "artifact-here": "coding_sandbox_o", "artifact-old": "coding_sandbox_o"}
ARTIFACTS = {"artifact-here": CONVERSATION, "artifact-old": "conv-0"}


class TestAServiceAccountAgentWithAWholeCollection:
    @pytest.mark.asyncio
    async def test_a_record_outside_its_knowledge_is_not_sent(self) -> None:
        graph = _Graph(ROWS, ARTIFACTS)
        state = _state(graph, await _turn_of_a_whole_kb_agent(graph))

        bundle, fetch = await _send(state, ["jira-1"])

        assert bundle.resolved == []
        assert [(f.ref, f.error_type) for f in bundle.failures] == [("jira-1", "RecordOutsideTurnError")]
        assert "outside what this conversation is limited to" in bundle.failures[0].error
        fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_record_inside_its_knowledge_is_sent(self) -> None:
        graph = _Graph(ROWS, ARTIFACTS)
        state = _state(graph, await _turn_of_a_whole_kb_agent(graph))

        bundle, _ = await _send(state, ["kb-rec", "jira-1"])

        assert [r.record_id for r in bundle.resolved] == ["kb-rec"]
        assert [f.ref for f in bundle.failures] == ["jira-1"]

    @pytest.mark.asyncio
    async def test_an_artifact_of_this_conversation_is_sent_and_one_of_another_is_not(self) -> None:
        graph = _Graph(ROWS, ARTIFACTS)
        state = _state(graph, await _turn_of_a_whole_kb_agent(graph))

        bundle, _ = await _send(state, ["artifact-here", "artifact-old"])

        assert [r.record_id for r in bundle.resolved] == ["artifact-here"]
        assert [(f.ref, f.error_type) for f in bundle.failures] == [("artifact-old", "RecordOutsideTurnError")]


class TestATurnLimitedByNothing:
    @pytest.mark.asyncio
    async def test_any_readable_record_is_sent_without_asking_the_graph_for_limits(self) -> None:
        graph = _Graph(ROWS)

        bundle, _ = await _send(_state(graph, {"apps": [], "kb": []}), ["jira-1"])

        assert [r.record_id for r in bundle.resolved] == ["jira-1"]
        graph.check_access.assert_not_awaited()


class TestALimitTooLargeToResolve:
    @pytest.mark.asyncio
    async def test_the_failure_says_why(self, monkeypatch) -> None:
        """R1-23: it surfaced as "Unexpected error: " with an empty message."""
        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "1")
        graph = _Graph(ROWS)
        graph.get_selection_nodes = AsyncMock(return_value={
            "groups": [{"id": "g1", "connectorId": "kb-1"}, {"id": "g1-sub", "connectorId": "kb-1"}], "records": [],
        })
        filters = {"apps": [], "kb": ["kb-1"], "allowedApps": [], "allowedRecordGroups": ["g1"], "allowedRecords": []}

        bundle, _ = await _send(_state(graph, filters), ["kb-rec"])

        assert [(f.ref, f.error_type) for f in bundle.failures] == [("kb-rec", "selection_too_large")]
        assert bundle.failures[0].error.startswith("This agent is limited to folders and items that hold too many")
