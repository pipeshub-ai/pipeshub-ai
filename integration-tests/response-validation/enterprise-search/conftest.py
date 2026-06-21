"""Enterprise search/conversation IT fixtures.

Provisions a KB at session start by downloading a known PDF from the public
pipeshub-ai/integration-test GitHub repo and uploading it via the existing
``/api/v1/knowledgeBase/{kbId}/upload`` endpoint. The KB is deleted on teardown.

To swap PDFs, update ASANA_PDF_BLOB_URL below — accepts either a github.com
/blob/ URL (gets converted to the raw URL) or a raw URL directly.
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Any, TypedDict
from urllib.parse import unquote, urlparse
from uuid import uuid4

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
from ai_models_setup import (
    SeededAIModel,
    setup_test_llm_model,
    teardown_test_llm_model,
)
from pipeshub_client import PipeshubClient

logger = logging.getLogger("enterprise-search-conftest")

_AGENTS_CREATE_PATH = "/api/v1/agents/create"
_AGENT_COUNT = 5


class AgentSession(TypedDict):
    primary_agent: str
    secondary_agents: list[str]


class AsanaPdfBlob(TypedDict):
    buffer: bytes
    originalname: str
    mimetype: str


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
def asana_pdf_blob() -> AsanaPdfBlob:
    """Session-scoped shared Asana DR PDF fixture used by enterprise-search ITs."""
    raw_url = _github_blob_to_raw(ASANA_PDF_BLOB_URL)
    buffer, originalname, mimetype = _fetch_url_bytes(raw_url)
    logger.info(
        "Fetched shared Asana PDF fixture %s (%s, %d bytes)",
        originalname, mimetype, len(buffer),
    )
    return {
        "buffer": buffer,
        "originalname": originalname,
        "mimetype": mimetype,
    }


@pytest.fixture(scope="session")
def session_kb(
    pipeshub_client: PipeshubClient,
    ai_models_configured,
    reasoning_multimodal_llm_model: SeededAIModel,
    asana_pdf_blob: AsanaPdfBlob,
):
    """Session-scoped KB with the Asana DR PDF uploaded and indexed.

    Yields ``{"kb_id": str, "record_id": str}``. Deletes the KB on teardown.
    """
    del ai_models_configured  # fixture ordering only
    del reasoning_multimodal_llm_model  # ensure OCR fallback has a multimodal LLM

    kb_client = KBClient(pipeshub_client)

    kb_resp = kb_client.create_kb(name="enterprise-search-it-kb")
    kb_id = _extract_kb_id(kb_resp)
    assert kb_id, f"KB create returned no id: {kb_resp}"
    logger.info("Created KB %s for enterprise search IT", kb_id)

    try:
        buffer = asana_pdf_blob["buffer"]
        originalname = asana_pdf_blob["originalname"]
        mimetype = asana_pdf_blob["mimetype"]

        upload_resp = kb_client.upload_file(
            kb_id,
            originalname,
            buffer,
            mimetype=mimetype,
        )
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


def _agent_create_payload(
    *,
    name: str,
    seeded_model: SeededAIModel,
    kb_id: str | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "models": [
            {
                "modelKey": seeded_model.model_key,
                "modelName": seeded_model.model_name,
                "provider": seeded_model.provider,
                "isReasoning": True,
            },
        ],
    }
    if kb_id and org_id:
        payload["knowledge"] = [
            {
                "connectorId": f"knowledgeBase_{org_id}",
                "filters": {"recordGroups": [kb_id], "records": []},
            },
        ]
    return payload


def _extract_agent_key(create_response: dict[str, Any]) -> str:
    agent_key = (create_response.get("agent") or {}).get("_key")
    if not isinstance(agent_key, str) or not agent_key:
        raise AssertionError(f"Agent create returned no agent._key: {create_response!r}")
    return agent_key


def _create_agent(
    client: PipeshubClient,
    payload: dict[str, Any],
) -> str:
    url = f"{client.base_url}{_AGENTS_CREATE_PATH}"
    resp = requests.post(
        url,
        headers=client.auth_headers,
        json=payload,
        timeout=client.timeout_seconds,
    )
    if resp.status_code >= 300:
        raise AssertionError(
            f"Agent create failed: HTTP {resp.status_code} {resp.text[:500]}"
        )
    return _extract_agent_key(resp.json())


def _delete_agent(client: PipeshubClient, agent_key: str) -> None:
    url = f"{client.base_url}/api/v1/agents/{agent_key}"
    resp = requests.delete(
        url,
        headers=client.auth_headers,
        timeout=client.timeout_seconds,
    )
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Agent delete failed for {agent_key}: HTTP {resp.status_code} {resp.text[:300]}"
        )


@pytest.fixture(scope="session")
def reasoning_multimodal_llm_model(
    pipeshub_client: PipeshubClient,
    ai_models_configured,
) -> SeededAIModel:
    """Dedicated GPT-5 model for agent ITs and OCR fallback.

    Always seeds a non-default LLM with both ``isReasoning: true`` and
    ``isMultimodal: true`` so agent tests avoid the fragile available-model
    lookup and scanned-PDF indexing can use VLM OCR when Docling is down.
    Depends on ``ai_models_configured`` so a default org LLM still exists for
    general indexing paths.
    """
    del ai_models_configured  # fixture ordering only

    seeded_by_fixture: SeededAIModel | None = None
    try:
        seeded_by_fixture = setup_test_llm_model(
            pipeshub_client,
            is_reasoning=True,
            is_multimodal=True,
            is_default=False,
        )
        logger.info(
            "Seeded reasoning multimodal LLM for agent ITs: modelKey=%s model=%s",
            seeded_by_fixture.model_key,
            seeded_by_fixture.model_name,
        )
        yield seeded_by_fixture
    except RuntimeError as e:
        pytest.fail(
            f"Failed to seed reasoning multimodal LLM for enterprise-search ITs: {e}. "
            "Set TEST_OPENAI_API_KEY (or OPENAI_API_KEY)."
        )
    finally:
        if seeded_by_fixture is not None:
            teardown_test_llm_model(pipeshub_client, seeded_by_fixture)


@pytest.fixture(scope="session")
def agent_session(
    pipeshub_client: PipeshubClient,
    reasoning_multimodal_llm_model: SeededAIModel,
    session_kb: dict[str, str],
) -> AgentSession:
    """Session-scoped agents for agent conversation stream ITs.

    Creates five agents: one primary (KB-attached) and four secondary (no knowledge).
    Yields ``{"primary_agent": str, "secondary_agents": [str, ...]}`` (4 secondary keys).
    Deletes all agents on teardown.
    """
    kb_id = session_kb["kb_id"]
    org_id = pipeshub_client.org_id
    created_keys: list[str] = []

    try:
        primary_key = _create_agent(
            pipeshub_client,
            _agent_create_payload(
                name=f"integration-agent-primary-{uuid4().hex[:8]}",
                seeded_model=reasoning_multimodal_llm_model,
                kb_id=kb_id,
                org_id=org_id,
            ),
        )
        created_keys.append(primary_key)
        logger.info("Created primary agent %s with KB %s", primary_key, kb_id)

        secondary_keys: list[str] = []
        for index in range(2, _AGENT_COUNT + 1):
            agent_key = _create_agent(
                pipeshub_client,
                _agent_create_payload(
                    name=f"integration-agent-{index}-{uuid4().hex[:8]}",
                    seeded_model=reasoning_multimodal_llm_model,
                ),
            )
            created_keys.append(agent_key)
            secondary_keys.append(agent_key)
            logger.info("Created secondary agent %s", agent_key)

        assert len(secondary_keys) == _AGENT_COUNT - 1

        yield AgentSession(
            primary_agent=primary_key,
            secondary_agents=secondary_keys,
        )
    finally:
        for agent_key in reversed(created_keys):
            try:
                _delete_agent(pipeshub_client, agent_key)
                logger.info("Deleted agent %s", agent_key)
            except Exception as e:
                logger.warning("Failed to delete agent %s: %s", agent_key, e)
