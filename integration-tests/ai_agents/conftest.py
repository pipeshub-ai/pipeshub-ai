"""Fixtures for the AI models, agents, MCP and agent harness suite.

Everything here runs against the live stack with a real model, so every
fixture that asks the model something is scoped to be asked once. What a
fixture creates it deletes, and what it changes on the org (the default
model, model roles, the MCP feature flag) it puts back.

The suite runs in its own nightly shard (``-m ai_agents``), not on pull
requests: each model call costs money.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from ai_models_setup import llm_configuration
from pipeshub_client import PipeshubClient

from ai_agents.support import (
    MCP_SERVERS,
    PATS,
    PLATFORM_SETTINGS,
    ItModel,
    add_llm,
    delete_llm,
    error_message,
    json_body,
    require_reasoning,
)
from helper.clients.agents_client import AgentsClient
from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_finished
from helper.local_auth import obtain_user_session_token
from helper.mcp_fixture import ORDER_TOOL, McpFixture
from helper.source_credentials import secrets_required, source_unavailable
from helper.stored_names import stored_name

logger = logging.getLogger("ai-agents-it")


@pytest.fixture(scope="session")
def ai_provider(ai_models_configured) -> tuple[str, str, dict[str, Any]]:
    """The provider the run has credentials for, as ``(provider, model, configuration)``."""
    del ai_models_configured
    resolved = llm_configuration()
    if resolved is None:
        source_unavailable(
            "no LLM provider credentials for the AI models suite",
            secrets=["TEST_AZURE_OPENAI_API_KEY", "TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME"],
            required=secrets_required(),
        )
    return resolved


@pytest.fixture(scope="session")
def it_model(pipeshub_client: PipeshubClient, ai_provider) -> Iterator[ItModel]:
    """A second LLM with a friendly name, added through the product's own form.

    Added as a reasoning model when the provider accepts that (agents need
    one); a provider that refuses the reasoning flag gets a plain model, and
    the reasoning tests skip with what it said.
    """
    provider, model_name, configuration = ai_provider
    friendly = f"IT Model {uuid.uuid4().hex[:6]}"
    configuration = {**configuration, "modelFriendlyName": friendly}
    wants_reasoning = provider != "ollama" or os.getenv("TEST_OLLAMA_REASONING") == "1"
    skip_reason: str | None = None

    resp = add_llm(pipeshub_client, configuration, provider=provider, is_reasoning=wants_reasoning)
    if resp.status_code != 200 and wants_reasoning and "reason" in error_message(resp).lower():
        skip_reason = f"{provider} refused the reasoning flag: {error_message(resp)[:300]}"
        wants_reasoning = False
        resp = add_llm(pipeshub_client, configuration, provider=provider, is_reasoning=False)
    elif not wants_reasoning:
        skip_reason = f"{model_name} on {provider} is not configured as a reasoning model"
    assert resp.status_code == 200, (
        f"adding {model_name} on {provider} failed its health check: {resp.status_code} {error_message(resp)}"
    )
    model_key = (json_body(resp).get("details") or {}).get("modelKey")
    assert model_key, f"add returned no details.modelKey: {resp.text[:500]}"

    model = ItModel(model_key, model_name, provider, friendly, wants_reasoning, configuration, skip_reason)
    try:
        yield model
    finally:
        delete_llm(pipeshub_client, model_key)


@pytest.fixture(scope="session")
def it_document(kb_client: KBClient, ai_models_configured) -> Iterator[dict[str, str]]:
    """A small indexed document with a fact only it contains, in a KB of its own."""
    del ai_models_configured
    token = uuid.uuid4().hex[:10]
    needle = f"marrowlight {token} escalation rota"
    name = f"ai-agents-it-{token}.md"
    body = (
        f"# Support escalation policy {token}\n\n"
        f"The {needle} is owned by the Tier 3 support desk. Severity 1 incidents "
        f"page the on-call engineer within 7 minutes, and the {needle} is reviewed "
        f"every quarter by the head of support.\n\n"
        "## Handover\n\nEach shift hands over open incidents in the support channel.\n"
    ).encode()

    kb_id = kb_client.create_kb(f"ai-agents-it-{token}")["id"]
    try:
        upload = kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")
        record_id = upload["records"][0]["recordId"]
        final = asyncio.run(wait_until_finished(kb_client, [record_id]))
        assert final.get(record_id) == "COMPLETED", (
            f"the suite's document did not finish indexing ({final.get(record_id)}), so the "
            "knowledge and search checks have nothing to find"
        )
        yield {
            "kb_id": kb_id,
            "record_id": record_id,
            "name": stored_name(name),
            "needle": needle,
            "question": f"Who owns the {needle}, and how fast is a Severity 1 incident paged?",
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as e:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not delete KB %s: %s", kb_id, e)


@pytest.fixture(scope="session")
def mcp_fixture() -> McpFixture:
    fixture = McpFixture.from_env()
    if not fixture.healthy():
        source_unavailable(
            f"the mcp-fixture service is not reachable at {fixture.test_url}; start the "
            "integration compose stack, which runs it",
            secrets=["MCP_FIXTURE_TEST_URL"],
            required=secrets_required(),
        )
    return fixture


@pytest.fixture(scope="session")
def mcp_feature_enabled(pipeshub_client: PipeshubClient) -> Iterator[None]:
    """External MCP servers are behind the ENABLE_MCP platform flag, off by default."""
    resp = pipeshub_client.request("GET", PLATFORM_SETTINGS)
    assert resp.status_code == 200, f"reading platform settings: {resp.status_code} {resp.text[:300]}"
    before = json_body(resp)
    flags = dict(before.get("featureFlags") or {})
    max_bytes = before.get("fileUploadMaxSizeBytes") or 30 * 1024 * 1024
    if flags.get("ENABLE_MCP") is True:
        yield
        return
    # The POST replaces the whole settings object, so send back everything else unchanged.
    resp = pipeshub_client.request("POST", PLATFORM_SETTINGS, json={
        "fileUploadMaxSizeBytes": max_bytes, "featureFlags": {**flags, "ENABLE_MCP": True},
    })
    assert resp.status_code == 200, f"enabling MCP servers: {resp.status_code} {resp.text[:300]}"
    try:
        yield
    finally:
        restore = pipeshub_client.request("POST", PLATFORM_SETTINGS, json={
            "fileUploadMaxSizeBytes": max_bytes, "featureFlags": flags,
        })
        if restore.status_code != 200:
            logger.warning("could not restore platform settings: %s %s", restore.status_code, restore.text[:300])


@pytest.fixture(scope="session")
def mcp_instance(
    pipeshub_client: PipeshubClient, mcp_fixture: McpFixture, mcp_feature_enabled
) -> Iterator[dict[str, Any]]:
    """The mcp-fixture server registered as an external MCP server (no auth).

    Yields the stored instance plus ``orderTool``: the namespaced tool name the
    product's own discovery reports, which is what an agent is given.
    """
    del mcp_feature_enabled
    name = f"it-mcp-{uuid.uuid4().hex[:6]}"
    resp = pipeshub_client.request("POST", f"{MCP_SERVERS}/instances", json={
        "name": name,
        "transport": "streamable_http",
        "authMode": "none",
        "url": mcp_fixture.connector_url,
        "description": "Order lookups for the integration run",
    })
    assert resp.status_code in (200, 201), f"registering the MCP server: {resp.status_code} {resp.text[:500]}"
    instance = json_body(resp)
    instance_id = instance.get("_id")
    assert instance_id, f"MCP instance create returned no _id: {resp.text[:500]}"
    try:
        tools_resp = pipeshub_client.request("GET", f"{MCP_SERVERS}/instances/{instance_id}/tools")
        assert tools_resp.status_code == 200, (
            f"PipesHub could not list the tools of {mcp_fixture.connector_url}: "
            f"{tools_resp.status_code} {tools_resp.text[:500]}"
        )
        tools = json_body(tools_resp).get("tools") or []
        order = next((t for t in tools if isinstance(t, dict) and t.get("name") == ORDER_TOOL), None)
        assert order, f"discovery did not report {ORDER_TOOL}: {tools}"
        yield {**instance, "orderTool": order.get("namespacedName") or f"mcp_{name}_{ORDER_TOOL}"}
    finally:
        deleted = pipeshub_client.request("DELETE", f"{MCP_SERVERS}/instances/{instance_id}")
        if deleted.status_code >= 300 and deleted.status_code != 404:
            logger.warning("could not delete MCP instance %s: %s", instance_id, deleted.text[:300])


@pytest.fixture(scope="session")
def kb_mcp_agent(
    agents_client: AgentsClient,
    it_model: ItModel,
    it_document: dict[str, str],
    mcp_instance: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """An Agent Builder agent with the suite's KB as knowledge and the MCP server as a tool."""
    require_reasoning(it_model)
    payload = {
        "name": f"it-kb-mcp-agent-{uuid.uuid4().hex[:6]}",
        "description": "Answers from the support policy and looks up orders.",
        "systemPrompt": (
            "You help the support team. Answer policy questions from the attached knowledge. "
            "For anything about a customer order, use the order status tool."
        ),
        "models": [{
            "modelKey": it_model.model_key,
            "modelName": it_model.model_name,
            "provider": it_model.provider,
            "isReasoning": True,
        }],
        "knowledge": [{"connectorId": it_document["kb_id"], "filters": {}}],
        "mcpServers": [{
            "instanceId": mcp_instance["_id"],
            "name": mcp_instance["name"],
            "tools": [{"name": ORDER_TOOL, "fullName": mcp_instance["orderTool"]}],
        }],
    }
    resp = agents_client.create_agent(**payload)
    assert resp.status_code in (200, 201), f"creating the agent: {resp.status_code} {resp.text[:500]}"
    agent_key = (json_body(resp).get("agent") or {}).get("_key")
    assert agent_key, f"agent create returned no agent._key: {resp.text[:500]}"
    try:
        yield {"agent_key": agent_key, "payload": payload}
    finally:
        deleted = agents_client.delete_agent(agent_key)
        if deleted.status_code >= 300 and deleted.status_code != 404:
            logger.warning("could not delete agent %s: %s", agent_key, deleted.text[:300])


@pytest.fixture(scope="session")
def personal_access_token(pipeshub_client: PipeshubClient) -> Iterator[str]:
    """A PAT as the MCP setup guide mints one; revoked at the end."""
    if not (os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv("PIPESHUB_TEST_USER_PASSWORD")):
        pytest.skip("PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD are not set")
    base_url = pipeshub_client.base_url
    headers = {"Authorization": f"Bearer {obtain_user_session_token(base_url)}"}
    resp = requests.post(
        f"{base_url}{PATS}",
        json={"name": f"ai-agents-it-{uuid.uuid4().hex[:8]}", "expiryDays": 30},
        headers=headers, timeout=30,
    )
    assert resp.status_code == 201, f"minting a personal access token: {resp.status_code} {resp.text[:300]}"
    token = resp.json()["token"]
    try:
        yield token["accessToken"]
    finally:
        try:
            revoked = requests.delete(f"{base_url}{PATS}/{token['id']}", headers=headers, timeout=30)
            # 404: already revoked. Anything else unsuccessful leaves a 30-day token live.
            if revoked.status_code >= 300 and revoked.status_code != 404:
                logger.warning("could not revoke personal access token %s: HTTP %s", token["id"], revoked.status_code)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not revoke personal access token %s", token["id"])
