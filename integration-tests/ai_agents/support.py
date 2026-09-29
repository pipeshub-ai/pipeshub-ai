"""Shared calls for the AI models, agents, MCP and agent harness suite.

Kept apart from ``conftest.py`` so test modules can import them by package
path (``ai_agents.support``) without clashing with the root conftest.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests
from pipeshub_client import PipeshubClient

from helper.agui_run import RunTrace, read_run
from helper.clients.conversations_client import AgentConversationsClient, ConversationsClient

logger = logging.getLogger("ai-agents-it")

AI_MODELS = "/api/v1/configurationManager/ai-models"
PLATFORM_SETTINGS = "/api/v1/configurationManager/platform/settings"
MCP_SERVERS = "/api/v1/mcp-servers"
PATS = "/api/v1/personal-access-tokens"

STREAM_TIMEOUT = int(os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT") or 300)


@dataclass(frozen=True)
class ItModel:
    """The LLM this suite adds for itself, next to the org's default."""

    model_key: str
    model_name: str
    provider: str
    friendly_name: str
    is_reasoning: bool
    # Holds the provider key: kept out of repr so no failure message prints it.
    configuration: dict[str, Any] = field(repr=False)
    # Why reasoning is off for this provider, when it is.
    reasoning_skip_reason: str | None = None


def json_body(resp: requests.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def error_message(resp: requests.Response) -> str:
    """The message an admin would see: ``error.message``, or the top-level one."""
    body = json_body(resp)
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    for key in ("message", "detail"):
        if isinstance(body.get(key), str):
            return body[key]
    return resp.text[:1000]


def list_llms(client: PipeshubClient) -> list[dict[str, Any]]:
    resp = client.request("GET", f"{AI_MODELS}/llm")
    assert resp.status_code == 200, f"listing LLMs failed: {resp.status_code} {resp.text[:500]}"
    models = json_body(resp).get("models")
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else []


def default_llm_key(client: PipeshubClient) -> str | None:
    for model in list_llms(client):
        if model.get("isDefault"):
            return model.get("modelKey")
    return None


def add_llm(
    client: PipeshubClient,
    configuration: dict[str, Any],
    *,
    provider: str,
    is_reasoning: bool,
    is_default: bool = False,
) -> requests.Response:
    """The Workspace > AI Models form's POST; the backend health-checks it first."""
    return client.request("POST", f"{AI_MODELS}/providers", json={
        "modelType": "llm",
        "provider": provider,
        "configuration": configuration,
        "isMultimodal": False,
        "isReasoning": is_reasoning,
        "isDefault": is_default,
    })


def delete_llm(client: PipeshubClient, model_key: str) -> None:
    resp = client.request("DELETE", f"{AI_MODELS}/providers/llm/{model_key}")
    if resp.status_code >= 300 and resp.status_code != 404:
        logger.warning("could not delete LLM %s: %s %s", model_key, resp.status_code, resp.text[:300])


def set_default_llm(client: PipeshubClient, model_key: str) -> requests.Response:
    return client.request("PUT", f"{AI_MODELS}/default/llm/{model_key}")


def model_info_of(trace: RunTrace) -> list[dict[str, Any]]:
    """``modelInfo`` as saved on the answer and on the conversation, whichever exist."""
    found = []
    for holder in (trace.bot_message, trace.conversation):
        info = holder.get("modelInfo") if isinstance(holder, dict) else None
        if isinstance(info, dict) and info:
            found.append(info)
    return found


def stream_chat(conversations: ConversationsClient, body: dict[str, Any]) -> RunTrace:
    with conversations.stream_conversation(json=body, timeout=STREAM_TIMEOUT) as resp:
        return read_run(resp)


def stream_chat_message(
    conversations: ConversationsClient, conversation_id: str, body: dict[str, Any]
) -> RunTrace:
    with conversations.stream_message(conversation_id, json=body, timeout=STREAM_TIMEOUT) as resp:
        return read_run(resp)


def stream_agent(
    agent_conversations: AgentConversationsClient,
    agent_key: str,
    query: str,
    *,
    conversation_id: str | None = None,
) -> RunTrace:
    body = {"query": query, "chatMode": "quick"}
    if conversation_id:
        cm = agent_conversations.stream_message(agent_key, conversation_id, json=body, timeout=STREAM_TIMEOUT)
    else:
        cm = agent_conversations.stream_conversation(agent_key, json=body, timeout=STREAM_TIMEOUT)
    with cm as resp:
        return read_run(resp)


def delete_conversation(conversations: ConversationsClient, trace: RunTrace) -> None:
    if trace.conversation_id:
        try:
            conversations.delete_conversation(trace.conversation_id)
        except Exception as e:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not delete conversation %s: %s", trace.conversation_id, e)


def fetch_conversation(conversations: ConversationsClient, conversation_id: str) -> dict[str, Any]:
    resp = conversations.get_conversation(conversation_id)
    assert resp.status_code == 200, f"reading conversation {conversation_id}: {resp.status_code} {resp.text[:500]}"
    body = json_body(resp)
    conversation = body.get("conversation")
    return conversation if isinstance(conversation, dict) else body


def require_reasoning(model: ItModel) -> None:
    if not model.is_reasoning:
        pytest.skip(f"needs a reasoning model: {model.reasoning_skip_reason}")
