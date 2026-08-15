"""Enterprise search/conversation IT fixtures.

Provisions a KB at session start by downloading a known PDF from the public
pipeshub-ai/integration-test GitHub repo and uploading it via the existing
``/api/v1/knowledgeBase/{kbId}/upload`` endpoint. The KB is deleted on teardown.

To swap PDFs, update ASANA_PDF_BLOB_URL below — accepts either a github.com
/blob/ URL (gets converted to the raw URL) or a raw URL directly.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any, TypedDict
from urllib.parse import unquote, urlparse
from uuid import uuid4

import pytest
import requests

from helper.agui_sse import (
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
    run_finished_result,
)
from helper.clients.kb_client import KBClient
from helper.second_user import second_user  # noqa: F401 - fixture
from helper.clients.agents_client import AgentsClient
from helper.clients.conversations_client import (
    AgentConversationsClient,
    ConversationsClient,
)
from helper.conversation_seeds import seed_query
from messaging.test_e2e_record_pipeline import (
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
from xdist_shared import shared_session_resource

logger = logging.getLogger("enterprise-search-conftest")

_SECONDARY_AGENT_COUNT = 2


class AgentSession(TypedDict):
    """Agents by role. Only ``primary_agent`` has the KB attached.

    A knowledge-free agent never gets the retrieval toolset registered
    (``app/modules/agents/qna/chat_state.py`` derives ``has_knowledge`` from it), so a
    stream against one is structurally a single LLM turn — it cannot spend a turn
    searching. Files that assert only on gateway/Mongo behaviour use ``workhorse_agent``
    for exactly that reason.
    """

    primary_agent: str
    workhorse_agent: str
    secondary_agents: list[str]
    disposable_agent: str
    all_agents: list[str]


class ReadOnlyConversation(TypedDict):
    conversation_id: str
    bot_message_id: str
    user_message_id: str
    search_token: str


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
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    pipeshub_client: PipeshubClient,
    ai_models_configured,
    reasoning_multimodal_llm_model: SeededAIModel,
    asana_pdf_blob: AsanaPdfBlob,
):
    """Session-scoped KB with the Asana DR PDF uploaded and indexed.

    Yields ``{"kb_id": str, "record_id": str}``. Deletes the KB on teardown.

    Uploading and indexing the PDF is the single most expensive piece of setup in
    this suite, so under ``-n`` it runs once per run and every worker queries the
    same KB rather than each building its own.
    """
    del ai_models_configured  # fixture ordering only
    del reasoning_multimodal_llm_model  # ensure OCR fallback has a multimodal LLM

    kb_client = KBClient(pipeshub_client)

    def _build() -> dict[str, str]:
        kb_resp = kb_client.create_kb(name="enterprise-search-it-kb")
        kb_id = _extract_kb_id(kb_resp)
        assert kb_id, f"KB create returned no id: {kb_resp}"
        logger.info("Created KB %s for enterprise search IT", kb_id)

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

        return {"kb_id": kb_id, "record_id": record_id}

    def _teardown(kb: dict[str, str]) -> None:
        try:
            kb_client.delete_kb(kb["kb_id"])
            logger.info("Deleted KB %s", kb["kb_id"])
        except Exception as e:
            logger.warning("Failed to delete KB %s: %s", kb["kb_id"], e)

    with shared_session_resource(
        "session_kb",
        config=request.config,
        tmp_path_factory=tmp_path_factory,
        create=_build,
        destroy=_teardown,
        dump=lambda kb: kb,
        load=lambda raw: raw,
    ) as kb:
        yield kb


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
                "connectorId": kb_id,  # KB UUID (not knowledgeBase_orgId)
                "type": "KB",
                "displayName": "Integration Test KB",
                "filters": {},  # No recordGroups - KBs are now apps
            },
        ]
    return payload


def _extract_agent_key(create_response: dict[str, Any]) -> str:
    agent_key = (create_response.get("agent") or {}).get("_key")
    if not isinstance(agent_key, str) or not agent_key:
        raise AssertionError(f"Agent create returned no agent._key: {create_response!r}")
    return agent_key


def _create_agent(
    agents: AgentsClient,
    payload: dict[str, Any],
) -> str:
    resp = agents.create_agent(**payload)
    if resp.status_code >= 300:
        raise AssertionError(
            f"Agent create failed: HTTP {resp.status_code} {resp.text[:500]}"
        )
    return _extract_agent_key(resp.json())


def _delete_agent(agents: AgentsClient, agent_key: str) -> None:
    resp = agents.delete_agent(agent_key)
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Agent delete failed for {agent_key}: HTTP {resp.status_code} {resp.text[:300]}"
        )


@pytest.fixture(scope="session")
def reasoning_multimodal_llm_model(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
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

    def _seed() -> SeededAIModel:
        try:
            seeded = setup_test_llm_model(
                pipeshub_client,
                is_reasoning=True,
                is_multimodal=True,
                is_default=False,
            )
        except RuntimeError as e:
            pytest.fail(
                f"Failed to seed reasoning multimodal LLM for enterprise-search ITs: {e}. "
                "Configure TEST_OPENAI_API_KEY, TEST_AZURE_OPENAI_API_KEY (+ endpoint "
                "and deployment), TEST_GEMINI_API_KEY, or TEST_GROQ_API_KEY. "
                "Set TEST_AI_MODEL_PROVIDER=azureOpenAI to prefer Azure when multiple "
                "providers are configured."
            )
        logger.info(
            "Seeded reasoning multimodal LLM for agent ITs: modelKey=%s model=%s",
            seeded.model_key,
            seeded.model_name,
        )
        return seeded

    with shared_session_resource(
        "reasoning_multimodal_llm_model",
        config=request.config,
        tmp_path_factory=tmp_path_factory,
        create=_seed,
        destroy=lambda seeded: teardown_test_llm_model(pipeshub_client, seeded),
        dump=asdict,
        load=lambda raw: SeededAIModel(**raw),
    ) as seeded_by_fixture:
        yield seeded_by_fixture


@pytest.fixture(scope="session")
def agent_session(
    agents_client: AgentsClient,
    pipeshub_client: PipeshubClient,
    reasoning_multimodal_llm_model: SeededAIModel,
    session_kb: dict[str, str],
) -> AgentSession:
    """Session-scoped agents by role for the agent conversation ITs.

    Creates five: ``primary_agent`` (the only one with the KB attached),
    ``workhorse_agent`` for fixture-only conversations, two ``secondary_agents`` used
    as "some other agent" scoping probes, and ``disposable_agent``, which exists to be
    deleted. Deletes whatever survives on teardown.
    """
    kb_id = session_kb["kb_id"]
    org_id = pipeshub_client.org_id
    created_keys: list[str] = []

    def _create_knowledge_free(label: str) -> str:
        agent_key = _create_agent(
            agents_client,
            _agent_create_payload(
                name=f"integration-agent-{label}-{uuid4().hex[:8]}",
                seeded_model=reasoning_multimodal_llm_model,
            ),
        )
        created_keys.append(agent_key)
        logger.info("Created knowledge-free agent %s (%s)", agent_key, label)
        return agent_key

    try:
        primary_key = _create_agent(
            agents_client,
            _agent_create_payload(
                name=f"integration-agent-primary-{uuid4().hex[:8]}",
                seeded_model=reasoning_multimodal_llm_model,
                kb_id=kb_id,
                org_id=org_id,
            ),
        )
        created_keys.append(primary_key)
        logger.info("Created primary agent %s with KB %s", primary_key, kb_id)

        workhorse_key = _create_knowledge_free("workhorse")
        secondary_keys = [
            _create_knowledge_free(f"secondary-{index}")
            for index in range(1, _SECONDARY_AGENT_COUNT + 1)
        ]
        # Owned by the delete-agent test alone. Nothing else may read it: a shared key
        # that one test deletes leaves every later "some other agent" probe pointing at
        # a corpse, and the gateway answers identically for a missing agent and one that
        # simply does not own the conversation.
        disposable_key = _create_knowledge_free("disposable")

        yield AgentSession(
            primary_agent=primary_key,
            workhorse_agent=workhorse_key,
            secondary_agents=secondary_keys,
            disposable_agent=disposable_key,
            all_agents=list(created_keys),
        )
    finally:
        for agent_key in reversed(created_keys):
            try:
                _delete_agent(agents_client, agent_key)
                logger.info("Deleted agent %s", agent_key)
            except Exception as e:
                logger.warning("Failed to delete agent %s: %s", agent_key, e)


def _conversation_id_from_stream(resp: requests.Response) -> str:
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    for envelope in iter_sse_envelopes(resp):
        payload = json.loads(envelope["data"])
        if is_root_error(envelope["event"], payload):
            raise AssertionError(f"stream emitted RUN_ERROR: {payload!r}")
        if not is_root_finished(envelope["event"], payload):
            continue
        result = run_finished_result(payload)
        conversation = result.get("conversation") or {}
        conversation_id = conversation.get("_id")
        assert isinstance(conversation_id, str) and conversation_id, (
            f"RUN_FINISHED result missing conversation._id: {result!r}"
        )
        return conversation_id
    raise AssertionError("conversation stream ended without a RUN_FINISHED event")


def _bot_and_user_message_ids(body: Any) -> tuple[str, str]:
    assert isinstance(body, dict), (
        f"Expected a JSON object response body, got: {body!r}"
    )
    conversation = body.get("conversation")
    assert isinstance(conversation, dict), (
        f"Expected conversation object, got: {body!r}"
    )
    messages = [m for m in (conversation.get("messages") or []) if isinstance(m, dict)]
    assert messages, f"Expected non-empty messages list, got: {body!r}"

    last = messages[-1]
    assert last.get("messageType") == "bot_response", (
        f"Expected last message bot_response, got: {last!r}"
    )
    bot_id = last.get("_id") or last.get("id")
    assert isinstance(bot_id, str) and bot_id, f"Expected bot message id, got: {last!r}"

    user_id = next(
        (
            m.get("_id") or m.get("id")
            for m in messages
            if m.get("messageType") == "user_query" and (m.get("_id") or m.get("id"))
        ),
        None,
    )
    assert isinstance(user_id, str) and user_id, (
        f"No user_query id found in messages: {messages!r}"
    )
    return bot_id, user_id


@pytest.fixture(scope="session")
def readonly_agent_conversation(
    agent_conversations_client: AgentConversationsClient,
    agent_session: AgentSession,
) -> Iterator[ReadOnlyConversation]:
    """One agent conversation shared by every read-only negative test.

    Deliberately does not go through the per-file
    ``_stream_create_agent_conversation_id`` helpers: those append to a function-scoped
    ``created_conversations`` fixture that soft-deletes on teardown, which would destroy
    this after its first borrower.

    Not wrapped in ``shared_session_resource`` either — unlike ``session_kb`` this is
    not an org-wide singleton, it belongs to a per-worker ``agent_session``, so sharing
    it across workers would hand one a conversation on an agent it never created.
    """
    agent_key = agent_session["workhorse_agent"]
    token = uuid4().hex
    with agent_conversations_client.stream_conversation(
        agent_key, query=seed_query(token), timeout=180,
    ) as resp:
        conversation_id = _conversation_id_from_stream(resp)

    resp = agent_conversations_client.get_conversation(agent_key, conversation_id)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    bot_id, user_id = _bot_and_user_message_ids(resp.json())
    logger.info(
        "Read-only agent conversation %s on agent %s", conversation_id, agent_key
    )

    try:
        yield ReadOnlyConversation(
            conversation_id=conversation_id,
            bot_message_id=bot_id,
            user_message_id=user_id,
            search_token=token,
        )
    finally:
        try:
            agent_conversations_client.delete_conversation(agent_key, conversation_id)
        except Exception as e:
            logger.warning("Failed to delete conversation %s: %s", conversation_id, e)


@pytest.fixture(scope="session")
def readonly_conversation(
    conversations_client: ConversationsClient,
) -> Iterator[ReadOnlyConversation]:
    """Non-agent twin of ``readonly_agent_conversation``, for the /conversations router."""
    token = uuid4().hex
    with conversations_client.stream_conversation(
        query=seed_query(token), timeout=180,
    ) as resp:
        conversation_id = _conversation_id_from_stream(resp)

    resp = conversations_client.get_conversation(conversation_id)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    bot_id, user_id = _bot_and_user_message_ids(resp.json())
    logger.info("Read-only conversation %s", conversation_id)

    try:
        yield ReadOnlyConversation(
            conversation_id=conversation_id,
            bot_message_id=bot_id,
            user_message_id=user_id,
            search_token=token,
        )
    finally:
        try:
            conversations_client.delete_conversation(conversation_id)
        except Exception as e:
            logger.warning("Failed to delete conversation %s: %s", conversation_id, e)
