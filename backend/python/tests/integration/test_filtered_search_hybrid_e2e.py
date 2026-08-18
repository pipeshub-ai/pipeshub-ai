"""End-to-end test of the hybrid native-filter + PipesHub-retrieval search
flow: PRE_TOOL_USE `filter_value_resolution` -> `FilteredSearch.
search_jira_issues` (real `JiraFilterAdapter` validation/identity
substitution, real `FilteredRetrievalBridge` permission gate) ->
POST_TOOL_USE `filtered_retrieval`.

Only the true external boundaries are mocked: the Jira API client, the
graph provider, and the retrieval service. Everything else — hook wiring,
JQL identity substitution, permission gating, and the tool-output rewrite
performed by the POST hook — runs for real, so this test is a regression
guard for the whole chain fitting together, not just each piece in
isolation (see the per-component unit tests alongside this file's
siblings under `tests/unit/agents/actions/filtered_search/` and
`tests/unit/agents/agent_loop/hooks/`).

Scenario: the model asks for "my high priority tickets" — the exact query
that used to trigger a DENY loop (see the design doc's "What actually
broke"). This exercises:
  1. The PRE hook substituting `currentUser()` with the asking user's real
     Jira account id BEFORE the native call — never the connector's own
     service identity.
  2. The native call carrying `priority` directly (no FilterSpec gap) and
     NO text predicate — asserted explicitly, since a native full-text
     term reaching the API silently returns weak results instead of
     routing through PipesHub retrieval.
  3. Three issues coming back from the (mocked) Jira API: one the graph
     can't resolve to an internal record (unresolved), one the user is not
     permitted to see (denied), one fully accessible.
  4. The POST hook running PipesHub's own scoped content search over
     exactly that one accessible record's virtual_record_id, and rewriting
     the tool output into a cited-answer-ready payload.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.hooks.middleware.context import (
    ToolCallContext,
    ToolResultContext,
)
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.actions.filtered_search import tools as tools_module
from app.agents.actions.filtered_search.tools import FilteredSearch
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import Record, RecordType

# See tests/unit/agents/agent_loop/hooks/test_filter_value_resolution.py for
# why these must go through importlib rather than `import ... as x` (the
# package __init__ re-exports the function, shadowing the submodule).
fvr_module = importlib.import_module("app.agents.agent_loop.hooks.filter_value_resolution")
fr_module = importlib.import_module("app.agents.agent_loop.hooks.filtered_retrieval")
from app.agents.agent_loop.hooks.filter_value_resolution import (
    filter_value_resolution,  # noqa: E402
)
from app.agents.agent_loop.hooks.filtered_retrieval import (
    filtered_retrieval,  # noqa: E402
)


def _jira_issue(issue_id: str, key: str, summary: str) -> dict:
    return {"id": issue_id, "key": key, "fields": {"summary": summary}}


def _record(external_record_id: str, virtual_record_id: str) -> Record:
    return Record(
        id=f"internal-{external_record_id}",
        record_name=f"Record {external_record_id}",
        record_type=RecordType.TICKET,
        external_record_id=external_record_id,
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="jira-conn-1",
        virtual_record_id=virtual_record_id,
        weburl=f"https://example.atlassian.net/browse/{external_record_id}",
    )


async def test_hybrid_filter_then_content_search_end_to_end(monkeypatch) -> None:
    # ── External boundary #1: the native Jira API client ────────────────────
    jira_response = MagicMock(status=200)
    jira_response.json.return_value = {
        "total": 3,
        "issues": [
            _jira_issue("1001", "CORE-1", "Fix login bug"),   # unresolved (graph has no match)
            _jira_issue("1002", "CORE-2", "Refactor auth"),   # denied (no permission)
            _jira_issue("1003", "CORE-3", "Add token refresh"),  # accessible
        ],
    }
    jira_client = MagicMock()
    jira_client.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=jira_response)

    # ── External boundary #2: the graph provider ─────────────────────────────
    accessible_record = _record("1003", virtual_record_id="vr-1003")
    denied_record = _record("1002", virtual_record_id="vr-1002")
    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1", "email": "alice@example.com"})
    graph_provider.get_app_user_by_email = AsyncMock(return_value=SimpleNamespace(source_user_id="acc-alice"))
    graph_provider.get_records_by_external_ids = AsyncMock(return_value=[denied_record, accessible_record])

    async def _access(node_id: str, **kwargs):
        return None if node_id == denied_record.id else {"id": node_id}

    graph_provider.get_knowledge_hub_node_access = AsyncMock(side_effect=_access)

    # ── External boundary #3: PipesHub's own retrieval service ───────────────
    retrieval_service = MagicMock()
    retrieval_service.search_with_filters = AsyncMock(return_value={
        "searchResults": [{"id": "vr-1003", "content": "Implemented token refresh via OAuth rotation."}],
    })

    chat_state = {
        "graph_provider": graph_provider,
        "retrieval_service": retrieval_service,
        "org_id": "org-1",
        "user_id": "user-1",
    }

    # Connector resolution is a graph-catalog concern covered by its own
    # unit tests (test_connector_context.py) — short-circuit it here so
    # this test's signal is the identity/validation/permission/retrieval
    # chain itself.
    monkeypatch.setattr(fvr_module, "resolve_connector_type", AsyncMock(return_value="JIRA"))
    monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("JIRA", jira_client)))

    # ── Step 1: PRE_TOOL_USE filter_value_resolution ─────────────────────────
    agent_context = SimpleNamespace(tool_state=chat_state)
    pre_middleware = filter_value_resolution(agent_context)
    tool_call_ctx = ToolCallContext(
        tool_path="/tools/filtered_search/search_jira_issues",
        tool_input={
            "connector_id": "jira-connector-1",
            "jql": 'assignee = currentUser() AND priority in ("Highest", "High")',
            "content_query": "token refresh",
            "limit": 10,
        },
    )

    pre_next_called = False

    async def _pre_next() -> None:
        nonlocal pre_next_called
        pre_next_called = True

    await pre_middleware(tool_call_ctx, _pre_next)

    assert tool_call_ctx.decision == PreDecision.ALLOW
    assert pre_next_called is True
    resolved_input = tool_call_ctx.tool_input
    # `currentUser()` is now the asking user's REAL Jira account id, resolved
    # via the graph — never left for the native API to resolve against its
    # own (service-account) identity.
    assert resolved_input["jql"] == 'assignee = "acc-alice" AND priority in ("Highest", "High")'

    # ── Step 2: the tool itself, called with the hook-resolved input ─────────
    tool = FilteredSearch(state=chat_state)
    ok, raw_output = await tool.search_jira_issues(
        connector_id=resolved_input["connector_id"], jql=resolved_input["jql"], limit=resolved_input["limit"],
    )

    assert ok is True
    # The native call must carry the resolved JQL and NO text predicate —
    # this is the whole "filters only" contract this design enforces.
    jira_client.search_and_reconsile_issues_using_jql_post.assert_awaited_once()
    sent_jql = jira_client.search_and_reconsile_issues_using_jql_post.call_args.kwargs["jql"]
    assert sent_jql == 'assignee = "acc-alice" AND priority in ("Highest", "High")'
    assert "~" not in sent_jql

    tool_payload = json.loads(raw_output)
    assert tool_payload["unresolved_count"] == 1  # issue 1001 had no graph match
    assert tool_payload["denied_count"] == 1  # issue 1002 was permission-denied
    assert tool_payload["accessible_count"] == 1  # only issue 1003 survives
    assert tool_payload["records"][0]["external_id"] == "1003"
    assert tool_payload["records"][0]["virtual_record_id"] == "vr-1003"

    # ── Step 3: POST_TOOL_USE filtered_retrieval ──────────────────────────────
    post_context = SimpleNamespace(
        retrieval_service=retrieval_service, user_id=chat_state["user_id"], org_id=chat_state["org_id"],
    )
    post_middleware = filtered_retrieval(post_context)
    tool_result_ctx = ToolResultContext(
        tool_path="/tools/filtered_search/search_jira_issues",
        tool_use_id=tool_call_ctx.tool_use_id,
        tool_response=ToolOutput(success=ok, data=raw_output),
        metadata=tool_call_ctx.metadata,
    )

    async def _post_next() -> None:
        return None

    await post_middleware(tool_result_ctx, _post_next)

    final_payload = json.loads(tool_result_ctx.tool_response.data)
    # Content search must have been scoped to ONLY the permission-gated
    # virtual_record_id — this is the crux of the whole design: the denied
    # and unresolved issues must never reach `search_with_filters`.
    retrieval_service.search_with_filters.assert_awaited_once_with(
        queries=["token refresh"], user_id="user-1", org_id="org-1", limit=10,
        virtual_record_ids_from_tool=["vr-1003"],
    )
    assert final_payload["content_matches"] == 1
    assert final_payload["results"] == [{"id": "vr-1003", "content": "Implemented token refresh via OAuth rotation."}]
    assert "records" not in final_payload  # plain listing replaced by content results
    assert final_payload["content_query"] == "token refresh"


async def test_unresolvable_identity_denies_before_any_native_call(monkeypatch) -> None:
    """The asking user has no Jira identity — the old bug here would let
    `currentUser()` reach Jira and resolve against the connector's own
    service account. The PRE hook must deny instead, and the native API
    must never be called at all."""
    jira_client = MagicMock()
    jira_client.search_and_reconsile_issues_using_jql_post = AsyncMock()

    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(return_value={"email": "bob@example.com"})
    graph_provider.get_app_user_by_email = AsyncMock(return_value=None)  # no identity on this connector

    chat_state = {"graph_provider": graph_provider, "org_id": "org-1", "user_id": "user-2"}
    monkeypatch.setattr(fvr_module, "resolve_connector_type", AsyncMock(return_value="JIRA"))

    pre_middleware = filter_value_resolution(SimpleNamespace(tool_state=chat_state))
    tool_call_ctx = ToolCallContext(
        tool_path="/tools/filtered_search/search_jira_issues",
        tool_input={"connector_id": "jira-connector-1", "jql": "assignee = currentUser()"},
    )
    next_called = False

    async def _next() -> None:
        nonlocal next_called
        next_called = True

    await pre_middleware(tool_call_ctx, _next)

    assert tool_call_ctx.decision == PreDecision.DENY
    assert next_called is False
    jira_client.search_and_reconsile_issues_using_jql_post.assert_not_called()


async def test_hybrid_filter_only_listing_when_no_content_query(monkeypatch) -> None:
    """Omitting `content_query` must stop at the permission-gated listing —
    `search_with_filters` must never be called."""
    jira_response = MagicMock(status=200)
    jira_response.json.return_value = {
        "total": 1,
        "issues": [_jira_issue("2001", "CORE-9", "Investigate flaky test")],
    }
    jira_client = MagicMock()
    jira_client.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=jira_response)

    record = _record("2001", virtual_record_id="vr-2001")
    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
    graph_provider.get_records_by_external_ids = AsyncMock(return_value=[record])
    graph_provider.get_knowledge_hub_node_access = AsyncMock(return_value={"id": record.id})

    retrieval_service = MagicMock()
    retrieval_service.search_with_filters = AsyncMock()

    chat_state = {
        "graph_provider": graph_provider,
        "retrieval_service": retrieval_service,
        "org_id": "org-1",
        "user_id": "user-1",
    }

    monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("JIRA", jira_client)))

    tool = FilteredSearch(state=chat_state)
    ok, raw_output = await tool.search_jira_issues(connector_id="jira-connector-1", jql='project = "CORE"')
    assert ok is True

    post_context = SimpleNamespace(retrieval_service=retrieval_service, user_id="user-1", org_id="org-1")
    post_middleware = filtered_retrieval(post_context)
    tool_result_ctx = ToolResultContext(
        tool_path="/tools/filtered_search/search_jira_issues",
        tool_use_id=__import__("uuid").uuid4(),
        tool_response=ToolOutput(success=True, data=raw_output),
        metadata={"filtered_search_call": {"content_query": None, "limit": None}},
    )

    await post_middleware(tool_result_ctx, AsyncMock())

    retrieval_service.search_with_filters.assert_not_called()
    final_payload = json.loads(tool_result_ctx.tool_response.data)
    assert final_payload["records"][0]["external_id"] == "2001"
