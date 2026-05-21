from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from openapi_search_validator import assert_matches_component_schema

PRIMARY_AGENT_ARCHIVE_COUNT = 6
EXTRA_AGENT_GROUP_COUNT = 1
AGENT_DETAIL_PATH = "/agents/{agentKey}"
GROUPED_AGENT_ARCHIVE_PATH = "/agents/conversations/show/archives"
PER_AGENT_ARCHIVES_SPEC_PATH = "/agents/{agentKey}/conversations/show/archives"
AGENT_MESSAGE_STREAM_PATH = (
    "/agents/{agentKey}/conversations/{conversationId}/messages/stream"
)
AGENT_CONVERSATION_DETAIL_PATH = "/agents/{agentKey}/conversations/{conversationId}"
AGENT_REGENERATE_PATH = (
    "/agents/{agentKey}/conversations/{conversationId}/message/{messageId}/regenerate"
)
AGENT_FEEDBACK_PATH = (
    "/agents/{agentKey}/conversations/{conversationId}/message/{messageId}/feedback"
)
SSE_MAX_EVENTS = 10_000


def extract_conversation_id(payload: dict[str, Any]) -> str:
    conversation = (
        payload.get("conversation")
        if isinstance(payload.get("conversation"), dict)
        else payload
    )
    for key in ("_id", "id"):
        value = conversation.get(key) if isinstance(conversation, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    raise AssertionError(f"Could not extract conversation id from payload: {payload!r}")


def iter_sse_envelopes(
    resp: requests.Response, *, max_events: int = SSE_MAX_EVENTS
):
    event_name: str | None = None
    data_lines: list[str] = []

    def flush():
        nonlocal event_name, data_lines
        if event_name is None:
            return None
        env = {"event": event_name, "data": "\n".join(data_lines)}
        event_name = None
        data_lines = []
        return env

    emitted = 0
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\r")
        if line == "":
            env = flush()
            if env is not None:
                yield env
                emitted += 1
                if emitted >= max_events:
                    raise AssertionError(f"SSE exceeded max_events={max_events}")
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    env = flush()
    if env is not None:
        yield env


def read_sse_json_until_complete(
    resp: requests.Response,
    *,
    context: str,
    envelope_schema: str | None = None,
) -> dict[str, Any]:
    for envelope in iter_sse_envelopes(resp):
        if envelope_schema:
            assert_matches_component_schema(envelope, envelope_schema)
        if envelope["event"] == "error":
            payload = json.loads(envelope["data"])
            raise AssertionError(f"{context} emitted error event: {payload!r}")
        if envelope["event"] != "complete":
            continue
        return json.loads(envelope["data"])
    raise AssertionError(f"{context} ended without a complete event")


def stream_json_post_to_complete(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout: int,
    context: str,
    envelope_schema: str | None = None,
) -> tuple[str, dict[str, Any]]:
    stream_headers = {**headers, "Accept": "text/event-stream"}
    with requests.post(
        url,
        headers=stream_headers,
        json=payload,
        stream=True,
        timeout=timeout,
    ) as resp:
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        content_type = (resp.headers.get("content-type") or "").lower()
        complete_payload = read_sse_json_until_complete(
            resp,
            context=context,
            envelope_schema=envelope_schema,
        )
    return content_type, complete_payload


def create_agent_conversation(
    base_url: str,
    headers: dict[str, str],
    stream_timeout: int,
    agent_key: str,
    *,
    query: str | None = None,
) -> str:
    _, payload = stream_json_post_to_complete(
        f"{base_url}/api/v1/agents/{agent_key}/conversations/stream",
        headers,
        {"query": query or f"integration agent conversation seed {uuid.uuid4().hex}"},
        timeout=stream_timeout,
        context=f"agent conversation stream for agent {agent_key!r}",
    )
    return extract_conversation_id(payload)


def delete_agent_conversation(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
    agent_key: str,
    conversation_id: str,
) -> None:
    delete_resp = requests.delete(
        f"{base_url}/api/v1/agents/{agent_key}/conversations/{conversation_id}",
        headers=headers,
        timeout=timeout,
    )
    assert delete_resp.status_code == 200, (
        f"{delete_resp.status_code}: {delete_resp.text}"
    )


def archive_agent_conversation(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
    agent_key: str,
    conversation_id: str,
) -> dict[str, Any]:
    archive_resp = requests.post(
        f"{base_url}/api/v1/agents/{agent_key}/conversations/{conversation_id}/archive",
        headers=headers,
        timeout=timeout,
    )
    assert archive_resp.status_code == 200, (
        f"{archive_resp.status_code}: {archive_resp.text}"
    )
    return archive_resp.json()


def create_and_archive_agent_conversation(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
    stream_timeout: int,
    agent_key: str,
    *,
    query: str,
) -> tuple[str, dict[str, Any]]:
    conversation_id = create_agent_conversation(
        base_url,
        headers,
        stream_timeout,
        agent_key,
        query=query,
    )
    archive_body = archive_agent_conversation(
        base_url,
        headers,
        timeout,
        agent_key,
        conversation_id,
    )
    return conversation_id, archive_body


def list_agent_conversation_ids(
    url: str,
    headers: dict[str, str],
    timeout: int,
    *,
    limit: int = 100,
) -> set[str]:
    found: set[str] = set()
    page = 1
    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={"page": page, "limit": limit},
            timeout=timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        for row in body.get("conversations") or []:
            if not isinstance(row, dict):
                continue
            cid = row.get("_id")
            if isinstance(cid, str) and cid.strip():
                found.add(cid)
        pagination = body.get("pagination") or {}
        if not pagination.get("hasNextPage"):
            break
        page += 1
    return found


def fetch_live_llm_models(
    base_url: str, headers: dict[str, str], timeout: int
) -> list[dict[str, Any]]:
    resp = requests.get(
        f"{base_url}/api/v1/configurationManager/ai-models/available/llm",
        headers=headers,
        timeout=timeout,
    )
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    body = resp.json()
    models = body.get("models") or []
    assert models, f"no live llm models returned: {body!r}"
    for model in models:
        assert isinstance(model, dict), f"unexpected model shape: {model!r}"
        assert model.get("modelKey"), f"live model missing modelKey: {model!r}"
        assert model.get("modelName"), f"live model missing modelName: {model!r}"
    return models


def fetch_live_knowledge_bases(
    base_url: str, headers: dict[str, str], timeout: int
) -> list[dict[str, Any]]:
    resp = requests.get(
        f"{base_url}/api/v1/knowledgeBase",
        headers=headers,
        params={"page": 1, "limit": 100},
        timeout=timeout,
    )
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    body = resp.json()
    knowledge_bases = body.get("knowledgeBases") or []
    assert isinstance(knowledge_bases, list), (
        f"unexpected knowledge base response shape: {body!r}"
    )
    return [kb for kb in knowledge_bases if isinstance(kb, dict)]


def fetch_live_connectors(
    base_url: str, headers: dict[str, str], timeout: int
) -> list[dict[str, Any]]:
    resp = requests.get(
        f"{base_url}/api/v1/connectors/configured",
        headers=headers,
        params={"page": 1, "limit": 100},
        timeout=timeout,
    )
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    body = resp.json()
    connectors_payload = body.get("connectors") or []
    if isinstance(connectors_payload, dict):
        connectors = connectors_payload.get("connectors") or []
    else:
        connectors = connectors_payload
    assert isinstance(connectors, list), f"unexpected connectors response shape: {body!r}"
    return [connector for connector in connectors if isinstance(connector, dict)]


def get_agent_conversation_messages(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
    agent_key: str,
    conversation_id: str,
) -> list[dict[str, Any]]:
    resp = requests.get(
        f"{base_url}/api/v1/agents/{agent_key}/conversations/{conversation_id}",
        headers=headers,
        timeout=timeout,
    )
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    body = resp.json()
    conversation = body.get("conversation") or {}
    messages = conversation.get("messages") or []
    assert isinstance(messages, list), (
        f"conversation messages missing or invalid: {body!r}"
    )
    return [message for message in messages if isinstance(message, dict)]


def runtime_timezone_name() -> str:
    tz_name = (os.getenv("TZ") or "").strip()
    if "/" in tz_name:
        return tz_name
    return "UTC"


def runtime_current_time() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_kb_filter_payload(kb: dict[str, Any]) -> dict[str, Any]:
    kb_id = str(kb["id"])
    kb_name = str(kb["name"])
    return {
        "filters": {"kb": [kb_id]},
        "appliedFilters": {
            "kb": [
                {
                    "id": kb_id,
                    "name": kb_name,
                    "nodeType": "recordGroup",
                    "connector": "KB",
                }
            ]
        },
    }


def build_connector_filter_payload(connector: dict[str, Any]) -> dict[str, Any]:
    connector_id = str(
        connector.get("_id") or connector.get("id") or connector.get("_key")
    )
    connector_name = str(
        connector.get("name")
        or connector.get("displayName")
        or connector.get("type")
        or "Connector"
    )
    connector_type = str(
        connector.get("type")
        or connector.get("connectorType")
        or connector.get("connector")
        or "connector"
    )
    return {
        "filters": {"apps": [connector_id]},
        "appliedFilters": {
            "apps": [
                {
                    "id": connector_id,
                    "name": connector_name,
                    "nodeType": "app",
                    "connector": connector_type,
                }
            ]
        },
    }


def build_follow_up_payload(
    query: str | None,
    *,
    live_model: dict[str, Any] | None = None,
    include_model: bool = False,
    include_kb_filter: bool = False,
    include_time_context: bool = False,
    include_tools: bool = False,
    chat_mode: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if query is not None:
        payload["query"] = query

    if include_model:
        assert live_model is not None, "live_model is required when include_model=True"
        payload["modelKey"] = live_model["modelKey"]
        payload["modelName"] = live_model["modelName"]
        if live_model.get("modelFriendlyName"):
            payload["modelFriendlyName"] = live_model["modelFriendlyName"]

    if include_kb_filter:
        raise AssertionError(
            "include_kb_filter in build_follow_up_payload is deprecated; "
            "pass live KB data explicitly from fixtures."
        )

    if include_time_context:
        payload["timezone"] = runtime_timezone_name()
        payload["currentTime"] = runtime_current_time()

    if include_tools:
        payload["tools"] = []

    if chat_mode is not None:
        payload["chatMode"] = chat_mode

    return payload


def stream_create_agent_conversation_and_last_bot_message_id(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
    stream_timeout: int,
    agent_key: str,
    *,
    query: str,
) -> tuple[str, str]:
    conversation_id = create_agent_conversation(
        base_url, headers, stream_timeout, agent_key, query=query
    )
    messages = get_agent_conversation_messages(
        base_url, headers, timeout, agent_key, conversation_id
    )
    bot_id: str | None = None
    for message in reversed(messages):
        if message.get("messageType") != "bot_response":
            continue
        mid = message.get("_id") or message.get("id")
        if isinstance(mid, str) and mid:
            bot_id = mid
            break
    assert bot_id, f"no bot_response with _id in messages: {messages!r}"
    return conversation_id, bot_id


def stream_create_agent_conversation_bot_and_user_message_ids(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
    stream_timeout: int,
    agent_key: str,
    *,
    query: str,
) -> tuple[str, str, str]:
    conversation_id = create_agent_conversation(
        base_url, headers, stream_timeout, agent_key, query=query
    )
    messages = get_agent_conversation_messages(
        base_url, headers, timeout, agent_key, conversation_id
    )
    bot_id: str | None = None
    user_id: str | None = None
    for message in reversed(messages):
        if message.get("messageType") != "bot_response":
            continue
        mid = message.get("_id") or message.get("id")
        if isinstance(mid, str) and mid:
            bot_id = mid
            break
    for message in messages:
        if message.get("messageType") != "user_query":
            continue
        mid = message.get("_id") or message.get("id")
        if isinstance(mid, str) and mid:
            user_id = mid
            break
    assert bot_id, f"no bot_response with _id in messages: {messages!r}"
    assert user_id, f"no user_query with _id in messages: {messages!r}"
    return conversation_id, bot_id, user_id


def create_agent_conversations(
    base_url: str,
    headers: dict[str, str],
    stream_timeout: int,
    agent_key: str,
    queries: list[str],
) -> list[str]:
    return [
        create_agent_conversation(
            base_url,
            headers,
            stream_timeout,
            agent_key,
            query=query,
        )
        for query in queries
    ]
