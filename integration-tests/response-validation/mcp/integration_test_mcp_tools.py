"""
MCP tools — integration tests
=============================

Calls every tool on ``{PIPESHUB_BASE_URL}/mcp`` the way an MCP host does
after it connects, and checks the answer against what this run put on the
instance: a document indexed in a collection of its own, and an agent with
that collection as its knowledge (see ``conftest.py``).

Auth:
  An authorization_code + PKCE token for the test user, the token a real MCP
  host holds. A client_credentials token would act as the app's service
  account, which cannot see the test user's collection or agent.

``integration_test_mcp_server.py`` pins what the tools look like; this file
checks what they do.
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import Callable

import pytest
from pipeshub_client import PipeshubClient

from helper.mcp_client import McpToolResult, call_mcp_tool

pytestmark = [pytest.mark.integration, pytest.mark.mcp]

CallTool = Callable[..., McpToolResult]


@pytest.fixture(scope="module")
def call(mcp_base_url: str, mcp_access_token: str) -> CallTool:
    def _call(name: str, **arguments: object) -> McpToolResult:
        return call_mcp_tool(mcp_base_url, mcp_access_token, name, arguments)

    return _call


@pytest.fixture(scope="module")
def app_owner(call: CallTool, mcp_user_jwt: str) -> dict:
    """The test user as ``list_users`` reports them. ``mcp_user_jwt`` guarantees the email is set."""
    del mcp_user_jwt
    email = os.environ["PIPESHUB_TEST_USER_EMAIL"].lower()
    result = call("pipeshub_directory", action="list_users", search=email)
    assert not result.is_error, result.text
    matches = [u for u in result.json()["users"] if (u.get("email") or "").lower() == email]
    assert len(matches) == 1, f"list_users found {len(matches)} users with the test email"
    return matches[0]


def _ok(result: McpToolResult) -> dict:
    assert not result.is_error, f"tool failed: {result.text[:2000]}"
    return result.json()


# ====================================================================
# pipeshub_directory
# ====================================================================
class TestDirectory:
    def test_whoami_names_the_org(self, call: CallTool, pipeshub_client: PipeshubClient) -> None:
        body = _ok(call("pipeshub_directory", action="whoami"))

        assert body["orgId"] == pipeshub_client.org_id
        assert body.get("fullName"), body

    def test_whoami_is_the_app_owner(self, call: CallTool, app_owner: dict) -> None:
        body = _ok(call("pipeshub_directory", action="whoami"))

        assert body["userId"] == app_owner["userId"], body
        assert body["identityVerified"] is True, body

    def test_list_users_finds_the_app_owner(self, app_owner: dict) -> None:
        assert app_owner.get("userId"), app_owner
        assert app_owner.get("isActive") is True, app_owner

    def test_list_users_pages(self, call: CallTool) -> None:
        body = _ok(call("pipeshub_directory", action="list_users", limit=1))

        assert len(body["users"]) == 1
        assert body["pagination"]["limit"] == 1
        assert body["pagination"]["page"] == 1

    def test_list_users_with_no_match_is_empty(self, call: CallTool) -> None:
        body = _ok(call("pipeshub_directory", action="list_users", search=f"nobody-{uuid.uuid4().hex}"))

        assert body["users"] == []

    def test_get_user_returns_the_profile(self, call: CallTool, app_owner: dict) -> None:
        body = _ok(call("pipeshub_directory", action="get_user", userId=app_owner["userId"]))

        assert (body.get("email") or "").lower() == app_owner["email"].lower(), body

    def test_get_user_without_a_user_id_is_a_tool_error(self, call: CallTool) -> None:
        result = call("pipeshub_directory", action="get_user")

        assert result.is_error
        assert "userId" in result.text

    def test_list_groups_includes_everyone(self, call: CallTool) -> None:
        body = _ok(call("pipeshub_directory", action="list_groups"))

        types = [g.get("type") for g in body["groups"]]
        assert "everyone" in types, types
        assert all(isinstance(g.get("userCount"), int) for g in body["groups"]), body["groups"]

    def test_list_my_teams_answers_a_page(self, call: CallTool) -> None:
        body = _ok(call("pipeshub_directory", action="list_my_teams"))

        assert isinstance(body["teams"], list)
        # The backend omits hasNext from the empty page, which a fresh CI user gets.
        if body["teams"]:
            assert isinstance(body["pagination"]["hasNext"], bool)

    def test_an_unknown_action_is_rejected(self, call: CallTool) -> None:
        result = call("pipeshub_directory", action="not-an-action")

        assert result.is_error
        assert "action" in result.text


# ====================================================================
# pipeshub_sources
# ====================================================================
class TestSources:
    def test_lists_the_seeded_collection_as_indexed(self, call: CallTool, mcp_seeded_record: dict) -> None:
        body = _ok(call("pipeshub_sources"))

        source = next((s for s in body["sources"] if s["id"] == mcp_seeded_record["kb_id"]), None)
        assert source, f"{mcp_seeded_record['kb_name']} is not among {[s['name'] for s in body['sources']]}"
        assert source["kind"] == "knowledgeBase"
        assert source["name"] == mcp_seeded_record["kb_name"]
        assert source["coverage"]["indexed"] >= 1, source

    def test_lists_one_default_llm_and_no_embeddings(self, call: CallTool, ai_models_configured) -> None:
        body = _ok(call("pipeshub_sources"))

        models = body["llmModels"]
        assert ai_models_configured.model_key in [m["modelKey"] for m in models], models
        assert sum(1 for m in models if m.get("isDefault")) == 1, models
        assert "embeddingModels" not in body

    def test_embedding_models_only_when_asked(self, call: CallTool, ai_models_configured) -> None:
        body = _ok(call("pipeshub_sources", include=["embeddingModels"]))

        assert set(body) == {"embeddingModels"}, sorted(body)
        assert body["embeddingModels"], body
        assert all(m.get("modelKey") for m in body["embeddingModels"]), body


# ====================================================================
# pipeshub_agents
# ====================================================================
class TestAgents:
    def test_finds_the_seeded_agent_with_its_knowledge(
        self, call: CallTool, mcp_seeded_agent: dict, mcp_seeded_record: dict
    ) -> None:
        body = _ok(call("pipeshub_agents", search=mcp_seeded_agent["name"]))

        assert [a["agentId"] for a in body["agents"]] == [mcp_seeded_agent["agent_id"]], body
        agent = body["agents"][0]
        assert agent["name"] == mcp_seeded_agent["name"]
        assert agent["isActive"] is True
        assert agent["knowledge"] == [{"name": mcp_seeded_record["kb_name"], "type": "KB"}], agent

    def test_a_search_that_matches_nothing_is_empty(self, call: CallTool) -> None:
        body = _ok(call("pipeshub_agents", search=f"no-agent-{uuid.uuid4().hex}"))

        assert body["agents"] == []


# ====================================================================
# pipeshub_search
# ====================================================================
class TestSearch:
    def test_finds_the_seeded_document_in_its_collection(self, call: CallTool, mcp_seeded_record: dict) -> None:
        body = _ok(call(
            "pipeshub_search", query=mcp_seeded_record["needle"], kb=[mcp_seeded_record["kb_id"]], limit=5,
        ))

        assert [r["recordId"] for r in body["records"]] == [mcp_seeded_record["record_id"]], body
        record = body["records"][0]
        assert record["recordName"] == mcp_seeded_record["name"]
        assert record["connector"] == "KB"
        passages = " ".join(p["text"] for p in record["passages"])
        assert mcp_seeded_record["needle"] in passages, passages

    def test_finds_it_with_no_scope(self, call: CallTool, mcp_seeded_record: dict) -> None:
        body = _ok(call("pipeshub_search", query=mcp_seeded_record["needle"], limit=10))

        assert mcp_seeded_record["record_id"] in [r["recordId"] for r in body["records"]], body

    def test_an_empty_query_is_rejected(self, call: CallTool) -> None:
        result = call("pipeshub_search", query="")

        assert result.is_error
        assert "query" in result.text


# ====================================================================
# pipeshub_get_record_content
# ====================================================================
class TestGetRecordContent:
    def test_content_reads_the_document(self, call: CallTool, mcp_seeded_record: dict) -> None:
        result = call("pipeshub_get_record_content", recordId=mcp_seeded_record["record_id"])

        assert not result.is_error, result.text[:2000]
        assert f"Record ID: {mcp_seeded_record['record_id']}" in result.text
        assert mcp_seeded_record["needle"] in result.text

    def test_content_with_a_query_returns_the_passage(self, call: CallTool, mcp_seeded_record: dict) -> None:
        result = call(
            "pipeshub_get_record_content",
            recordId=mcp_seeded_record["record_id"],
            query="who owns the rollback drill",
        )

        assert not result.is_error, result.text[:2000]
        assert mcp_seeded_record["needle"] in result.text

    def test_navigate_lists_the_document_in_its_collection(self, call: CallTool, mcp_seeded_record: dict) -> None:
        result = call("pipeshub_get_record_content", mode="navigate", nodeId=mcp_seeded_record["kb_id"])

        assert not result.is_error, result.text[:2000]
        assert f"record_id={mcp_seeded_record['record_id']}" in result.text
        assert mcp_seeded_record["name"] in result.text

    def test_navigate_a_record_shows_its_path(self, call: CallTool, mcp_seeded_record: dict) -> None:
        result = call("pipeshub_get_record_content", mode="navigate", nodeId=mcp_seeded_record["record_id"])

        assert not result.is_error, result.text[:2000]
        path = result.text.splitlines()[0]
        assert mcp_seeded_record["kb_name"] in path and mcp_seeded_record["name"] in path, path

    def test_lookup_of_an_unknown_reference_is_a_miss_not_an_error(self, call: CallTool) -> None:
        # The unmatched reference is echoed only when the org has a connector besides KBs.
        reference = f"https://example.invalid/{uuid.uuid4().hex}"
        result = call("pipeshub_get_record_content", mode="lookup", identifiers=[reference])

        assert not result.is_error, result.text[:2000]
        assert result.text.startswith("Not found"), result.text

    def test_content_without_a_record_id_is_a_tool_error(self, call: CallTool) -> None:
        result = call("pipeshub_get_record_content", mode="content")

        assert result.is_error
        assert "recordId" in result.text

    def test_a_record_that_does_not_exist_is_forbidden(self, call: CallTool) -> None:
        result = call("pipeshub_get_record_content", recordId=str(uuid.uuid4()))

        assert result.is_error
        assert "403" in result.text, result.text

    def test_thread_mode_on_a_document_is_a_tool_error(self, call: CallTool, mcp_seeded_record: dict) -> None:
        result = call("pipeshub_get_record_content", mode="thread", recordId=mcp_seeded_record["record_id"])

        assert result.is_error
        assert "thread" in result.text.lower(), result.text

    def test_a_limit_under_the_minimum_is_rejected(self, call: CallTool) -> None:
        result = call("pipeshub_get_record_content", mode="navigate", limit=10)

        assert result.is_error
        assert "limit" in result.text


# ====================================================================
# pipeshub_download_record
# ====================================================================
class TestDownloadRecord:
    def test_returns_the_file_as_stored(self, call: CallTool, mcp_seeded_record: dict) -> None:
        result = call("pipeshub_download_record", recordId=mcp_seeded_record["record_id"])

        assert not result.is_error, result.text[:2000]
        resources = result.resources
        if resources:
            resource = resources[0]
            data = resource.get("text") or base64.b64decode(resource.get("blob", "")).decode()
        else:
            data = result.text
        assert data == mcp_seeded_record["body"]

    def test_an_unknown_record_is_a_tool_error(self, call: CallTool) -> None:
        result = call("pipeshub_download_record", recordId=str(uuid.uuid4()))

        assert result.is_error


# ====================================================================
# pipeshub_chat
# ====================================================================
@pytest.fixture(scope="module")
def first_turn(call: CallTool, mcp_seeded_record: dict) -> dict:
    # The model sometimes answers right but leaves out the citation marker, so ask up to 3 times.
    for _ in range(3):
        body = _ok(call(
            "pipeshub_chat",
            query=f"Who owns the {mcp_seeded_record['needle']}? Answer in one sentence.",
            filters={"kb": [mcp_seeded_record["kb_id"]]},
        ))
        if any(c.get("recordId") == mcp_seeded_record["record_id"] for c in body.get("citations") or []):
            break
    return body


# Each test asks the model, and the citation depends on its answer: nightly shard, not every PR.
@pytest.mark.ai_agents
class TestChat:
    def test_answers_with_a_citation_of_the_seeded_document(self, first_turn: dict, mcp_seeded_record: dict) -> None:
        assert first_turn.get("answer"), first_turn
        assert first_turn.get("conversationId"), first_turn
        cited = [c.get("recordId") for c in first_turn.get("citations") or []]
        assert mcp_seeded_record["record_id"] in cited, first_turn

    def test_a_follow_up_continues_the_conversation(self, call: CallTool, first_turn: dict) -> None:
        body = _ok(call(
            "pipeshub_chat",
            query="How often does it run? Answer in one sentence.",
            conversationId=first_turn["conversationId"],
        ))

        assert body["conversationId"] == first_turn["conversationId"]
        assert body.get("answer"), body
        assert body["messageCount"] > first_turn["messageCount"], body
