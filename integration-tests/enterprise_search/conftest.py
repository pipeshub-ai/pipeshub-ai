"""Enterprise search/conversation IT fixtures.

Provisions a KB at session start by downloading a known PDF from the public
pipeshub-ai/integration-test GitHub repo and uploading it via the existing
``/api/v1/knowledgeBase/{kbId}/upload`` endpoint. The KB is deleted on teardown.

To swap PDFs, update ASANA_PDF_BLOB_URL below — accepts either a github.com
/blob/ URL (gets converted to the raw URL) or a raw URL directly.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import time
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

import pytest
import requests

from messaging.test_e2e_record_pipeline import (
    KBClient,
    TERMINAL_STATUSES,
    _extract_kb_id,
    _extract_record_id,
    _get_record_status,
    poll_until,
)
from pipeshub_client import PipeshubClient
from enterprise_search.conversation_test_utils import (
    PRIMARY_AGENT_ARCHIVE_COUNT,
    archive_agent_conversation,
    create_agent_conversation,
    delete_agent_conversation,
    fetch_live_connectors,
    fetch_live_knowledge_bases,
    fetch_live_llm_models,
)

logger = logging.getLogger("enterprise-search-conftest")

ASANA_PDF_BLOB_URL = (
    "https://github.com/pipeshub-ai/integration-test/blob/main/"
    "sample-data/entities/enterprise-search/"
    "Asana%20Disaster%20Recovery%20Summary%20Report%20(2023-08).pdf"
)

INDEX_TIMEOUT_SEC = 180
INDEX_POLL_INTERVAL_SEC = 3


def _github_blob_to_raw(blob_url: str) -> str:
    parsed = urlparse(blob_url)
    if parsed.netloc == "raw.githubusercontent.com":
        return blob_url
    if parsed.netloc != "github.com" or "/blob/" not in parsed.path:
        raise ValueError(f"Not a GitHub blob URL: {blob_url}")
    new_path = parsed.path.replace("/blob/", "/", 1)
    return f"https://raw.githubusercontent.com{new_path}"


def _fetch_url_bytes(
    raw_url: str, preferred_name: str | None = None,
) -> tuple[bytes, str, str]:
    u = urlparse(raw_url.strip())
    if u.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs supported, got {u.scheme!r}")

    resp = requests.get(raw_url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    buffer = resp.content

    fallback = unquote(u.path.rsplit("/", 1)[-1]) or "file"
    originalname = (
        (preferred_name or fallback).replace("/", "").replace("\\", "")[:255]
        or "file"
    )

    mimetype, _ = mimetypes.guess_type(originalname)
    if not mimetype:
        ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        mimetype = ct or "application/octet-stream"

    return buffer, originalname, mimetype


@pytest.fixture(scope="session")
def session_kb(pipeshub_client: PipeshubClient, ai_models_configured):
    """Session-scoped KB with the Asana DR PDF uploaded and indexed.

    Yields ``{"kb_id": str, "record_id": str}``. Deletes the KB on teardown.
    """
    kb_client = KBClient(pipeshub_client)

    kb_resp = kb_client.create_kb(name="enterprise-search-it-kb")
    kb_id = _extract_kb_id(kb_resp)
    assert kb_id, f"KB create returned no id: {kb_resp}"
    logger.info("Created KB %s for enterprise search IT", kb_id)

    try:
        raw_url = _github_blob_to_raw(ASANA_PDF_BLOB_URL)
        buffer, originalname, mimetype = _fetch_url_bytes(raw_url)

        # Upload directly so the multipart tuple carries the real mimetype
        # (KBClient.upload_file hardcodes text/plain).
        files = [("files", (originalname, io.BytesIO(buffer), mimetype))]
        resp = requests.post(
            kb_client._url(f"/{kb_id}/upload"),
            headers=kb_client._headers(content_type=None),
            files=files,
            timeout=pipeshub_client.timeout_seconds,
        )
        upload_resp = pipeshub_client._handle_response(resp)
        record_id = _extract_record_id(upload_resp)
        assert record_id, f"Upload returned no record id: {upload_resp}"
        logger.info(
            "Uploaded %s (%s, %d bytes) to KB %s, record %s",
            originalname, mimetype, len(buffer), kb_id, record_id,
        )

        def _is_indexed() -> bool:
            return _get_record_status(kb_client.get_record(record_id)) in TERMINAL_STATUSES

        poll_until(
            _is_indexed,
            timeout=INDEX_TIMEOUT_SEC,
            interval=INDEX_POLL_INTERVAL_SEC,
            description=f"record {record_id} to finish indexing",
        )

        final_status = _get_record_status(kb_client.get_record(record_id))
        assert final_status == "COMPLETED", (
            f"PDF reached terminal status {final_status!r}, expected COMPLETED. "
            f"Search/conversation tests will not have any data to query."
        )

        yield {"kb_id": kb_id, "record_id": record_id}
    finally:
        try:
            kb_client.delete_kb(kb_id)
            logger.info("Deleted KB %s", kb_id)
        except Exception as e:
            logger.warning("Failed to delete KB %s: %s", kb_id, e)


@pytest.fixture(scope="class")
def provisioned_agent_keys(
    pipeshub_client: PipeshubClient,
    live_reasoning_llm_model: dict[str, Any],
):
    created_agent_keys: list[str] = []

    def create_agent(name_prefix: str) -> str:
        token = uuid.uuid4().hex[:8]
        payload = {
            "name": f"{name_prefix} {token}",
            "description": f"{name_prefix} integration test agent {token}",
            "systemPrompt": "You are a helpful assistant for integration tests.",
            "startMessage": "Hello from the integration test agent.",
            "instructions": "Answer briefly and clearly.",
            "models": [
                {
                    "modelKey": live_reasoning_llm_model["modelKey"],
                    "modelName": live_reasoning_llm_model["modelName"],
                    "provider": live_reasoning_llm_model.get("provider"),
                    "isReasoning": bool(live_reasoning_llm_model.get("isReasoning"))
                    or "gpt-5"
                    in str(live_reasoning_llm_model.get("modelName", "")).lower(),
                }
            ],
            "toolsets": [],
            "knowledge": [],
            "isPublic": False,
            "shareWithOrg": False,
        }
        response = pipeshub_client.create_agent(payload)
        agent = (
            response.get("agent")
            if isinstance(response.get("agent"), dict)
            else response
        )
        agent_key = agent.get("agentKey") or agent.get("_key")
        assert agent_key, f"created agent missing key: {agent!r}"
        created_agent_keys.append(str(agent_key))
        return str(agent_key)

    primary_agent_key = create_agent("IT Agent Conversation Primary")
    extra_agent_key = create_agent("IT Agent Conversation Extra")

    try:
        yield {
            "primary_agent_key": primary_agent_key,
            "extra_agent_key": extra_agent_key,
            "all_agent_keys": [extra_agent_key, primary_agent_key],
        }
    finally:
        for agent_key in reversed(created_agent_keys):
            try:
                pipeshub_client.delete_agent(agent_key)
            except Exception as exc:
                raise AssertionError(
                    f"Failed to delete integration test agent {agent_key}: {exc}"
                ) from exc


@pytest.fixture(scope="class")
def archived_agent_groups_dataset(
    pipeshub_client: PipeshubClient,
    provisioned_agent_keys: dict[str, Any],
):
    primary_agent_key = provisioned_agent_keys["primary_agent_key"]
    extra_agents = [provisioned_agent_keys["extra_agent_key"]]
    base_url = pipeshub_client.base_url
    headers = pipeshub_client.auth_headers
    timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
    stream_timeout = max(timeout, 120)
    created_conversations: list[tuple[str, str]] = []

    def create_archived(agent_key: str, query: str) -> str:
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
        assert archive_body.get("id") == conversation_id, (
            f"archive id mismatch: {archive_body!r}"
        )
        assert archive_body.get("status") == "archived", (
            f"expected archived status: {archive_body!r}"
        )
        created_conversations.append((agent_key, conversation_id))
        time.sleep(0.05)
        return conversation_id

    dataset_token = uuid.uuid4().hex
    extra_conversation_ids = [
        create_archived(
            agent_key,
            query=f"integration extra archived conversation {dataset_token} {idx}",
        )
        for idx, agent_key in enumerate(extra_agents)
    ]

    primary_conversation_ids = [
        create_archived(
            primary_agent_key,
            query=f"integration primary archived conversation {dataset_token} {idx}",
        )
        for idx in range(PRIMARY_AGENT_ARCHIVE_COUNT)
    ]

    try:
        yield {
            "base_url": base_url,
            "headers": headers,
            "timeout": timeout,
            "primary_agent_key": primary_agent_key,
            "primary_conversation_ids": primary_conversation_ids,
            "extra_agent_key": extra_agents[0],
            "extra_conversation_ids": extra_conversation_ids,
            "all_agent_keys": [*extra_agents, primary_agent_key],
            "dataset_token": dataset_token,
        }
    finally:
        for agent_key, conversation_id in reversed(created_conversations):
            delete_agent_conversation(
                base_url,
                headers,
                timeout,
                agent_key,
                conversation_id,
            )


@pytest.fixture
def live_llm_models(pipeshub_client: PipeshubClient) -> list[dict[str, Any]]:
    return fetch_live_llm_models(
        pipeshub_client.base_url,
        pipeshub_client.auth_headers,
        int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60")),
    )


@pytest.fixture
def live_llm_model(live_llm_models: list[dict[str, Any]]) -> dict[str, Any]:
    return live_llm_models[0]


@pytest.fixture
def live_knowledge_bases(
    pipeshub_client: PipeshubClient,
) -> list[dict[str, Any]]:
    return fetch_live_knowledge_bases(
        pipeshub_client.base_url,
        pipeshub_client.auth_headers,
        int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60")),
    )


@pytest.fixture
def live_knowledge_base(
    live_knowledge_bases: list[dict[str, Any]],
) -> dict[str, Any]:
    if not live_knowledge_bases:
        pytest.skip("No live knowledge bases available for this environment.")
    kb = live_knowledge_bases[0]
    assert kb.get("id"), f"live knowledge base missing id: {kb!r}"
    assert kb.get("name"), f"live knowledge base missing name: {kb!r}"
    return kb


@pytest.fixture
def live_connectors(pipeshub_client: PipeshubClient) -> list[dict[str, Any]]:
    return fetch_live_connectors(
        pipeshub_client.base_url,
        pipeshub_client.auth_headers,
        int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60")),
    )


@pytest.fixture
def live_connector(live_connectors: list[dict[str, Any]]) -> dict[str, Any]:
    if not live_connectors:
        pytest.skip("No configured connectors available for this environment.")
    connector = live_connectors[0]
    connector_id = connector.get("_id") or connector.get("id") or connector.get("_key")
    assert connector_id, f"live connector missing id: {connector!r}"
    return connector
