"""Shared fixtures for the strict OpenAPI audit of /api/v1/agents."""

from __future__ import annotations

import datetime
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

_INTEGRATION_ROOT = Path(__file__).resolve().parents[4]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ai_models_setup import SeededAIModel  # noqa: E402
from helper.clients.agents_client import AgentsClient  # noqa: E402
from helper.clients.projects_client import ProjectsClient  # noqa: E402
from helper.config import MONGO_DB_NAME, MONGO_URI  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402

from agents_audit_support import (  # noqa: E402
    CHAT_SESSION_MESSAGES_COLLECTION,
    CHAT_SESSIONS_COLLECTION,
    SEED_AGENT_KEY,
    AgentsAuditClient,
    MakeAgent,
    MultipartFiles,
    SeedAgentConversation,
    SeedMessage,
    SeedProject,
    TrackAgent,
    UploadAttachment,
    attachment_files,
    forget_access_token,
    mint_narrow_scope_token,
    unique_agent_name,
    uploaded_record_ids,
)

logger = logging.getLogger("spec-audit-agents")


@pytest.fixture(scope="session")
def agents_audit_client(pipeshub_client: PipeshubClient) -> AgentsAuditClient:
    return AgentsAuditClient(pipeshub_client)


@pytest.fixture(scope="session")
def admin_user_id(pipeshub_client: PipeshubClient) -> str:
    """The Mongo user id the admin token resolves to, i.e. who owns a seeded conversation."""
    user_id = pipeshub_client.acting_user_id
    if not user_id:
        pytest.fail("admin access token carries no user identity")
    return user_id


@pytest.fixture(scope="session")
def chat_database() -> Iterator[Database]:
    """Direct Mongo handle, to seed conversations without an LLM run and to remove what tests leave."""
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - any connection failure means "cannot seed"
        client.close()
        pytest.fail(f"MongoDB is not reachable at TEST_MONGO_URI, cannot seed agent conversations: {exc}")
    try:
        yield client[MONGO_DB_NAME]
    finally:
        client.close()


@pytest.fixture(scope="session")
def chat_sessions_collection(chat_database: Database) -> Collection:
    return chat_database[CHAT_SESSIONS_COLLECTION]


@pytest.fixture(scope="session")
def chat_session_messages_collection(chat_database: Database) -> Collection:
    return chat_database[CHAT_SESSION_MESSAGES_COLLECTION]


@pytest.fixture
def forget_conversation(
    chat_sessions_collection: Collection,
    chat_session_messages_collection: Collection,
) -> Iterator[Callable[[str | None], None]]:
    """Register a conversation a route created; its session and messages are removed on teardown.

    Removed from Mongo rather than through DELETE, which only soft-deletes.
    """
    created: list[ObjectId] = []

    def _forget(conversation_id: str | None) -> None:
        if conversation_id and ObjectId.is_valid(conversation_id):
            created.append(ObjectId(conversation_id))

    try:
        yield _forget
    finally:
        if created:
            chat_session_messages_collection.delete_many({"sessionId": {"$in": created}})
            chat_sessions_collection.delete_many({"_id": {"$in": created}})


@pytest.fixture
def seed_agent_conversation(
    chat_sessions_collection: Collection,
    chat_session_messages_collection: Collection,
    pipeshub_client: PipeshubClient,
    admin_user_id: str,
) -> Iterator[SeedAgentConversation]:
    """Factory: insert one agent chat session and return its id; all are removed on teardown.

    ``seed_agent_conversation(agent_key=SEED_AGENT_KEY, owner=None, **fields)``. ``owner``
    defaults to the admin; pass ``second_user.user_id`` for the member's own. ``fields``
    override stored values, e.g. ``projectId=ObjectId(project_id), projectVisibility="private"``.
    """
    created: list[ObjectId] = []

    def _seed(
        agent_key: str = SEED_AGENT_KEY, owner: str | None = None, **fields: Any
    ) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        owner_id = ObjectId(owner or admin_user_id)
        document: dict[str, Any] = {
            "sessionType": "agent",
            "agentKey": agent_key,
            "conversationSource": "agent_chat",
            "nextSeq": 0,
            "orgId": ObjectId(pipeshub_client.org_id),
            "userId": owner_id,
            "initiator": owner_id,
            "title": f"spec-audit {uuid.uuid4().hex[:8]}",
            "isShared": False,
            "sharedWith": [],
            "isDeleted": False,
            "isArchived": False,
            "lastActivityAt": int(now.timestamp() * 1000),
            "status": "Complete",
            "conversationErrors": [],
            "createdAt": now,
            "updatedAt": now,
            # Mongoose stamps this on every document it saves.
            "__v": 0,
            **fields,
        }
        inserted = chat_sessions_collection.insert_one(document).inserted_id
        created.append(inserted)
        return str(inserted)

    try:
        yield _seed
    finally:
        if created:
            chat_session_messages_collection.delete_many({"sessionId": {"$in": created}})
            chat_sessions_collection.delete_many({"_id": {"$in": created}})


@pytest.fixture
def seed_message(
    chat_sessions_collection: Collection,
    chat_session_messages_collection: Collection,
) -> SeedMessage:
    """Factory: append one message to a seeded session and return its id.

    ``seed_message(conversation_id, message_type="bot_response", content=..., **fields)``.
    Messages go away with their session in ``seed_agent_conversation``'s teardown.
    """

    def _seed(
        conversation_id: str,
        message_type: str = "bot_response",
        content: str = "Seeded by the agents spec audit.",
        **fields: Any,
    ) -> str:
        session = chat_sessions_collection.find_one_and_update(
            {"_id": ObjectId(conversation_id)}, {"$inc": {"nextSeq": 1}}
        )
        assert session is not None, f"no seeded session {conversation_id}"
        now = datetime.datetime.now(datetime.timezone.utc)
        document: dict[str, Any] = {
            "sessionId": session["_id"],
            "orgId": session["orgId"],
            "seq": session.get("nextSeq", 0),
            "messageType": message_type,
            "content": content,
            "contentFormat": "MARKDOWN",
            "citations": [],
            "followUpQuestions": [],
            "feedback": [],
            "attachments": [],
            "referenceData": [],
            "tools": [],
            "reasoning": [],
            "parts": [],
            "createdAt": now,
            "updatedAt": now,
            **fields,
        }
        return str(chat_session_messages_collection.insert_one(document).inserted_id)

    return _seed


@pytest.fixture
def seed_project(projects_client: ProjectsClient) -> Iterator[SeedProject]:
    """Factory: create one project owned by the admin and return its id; all are deleted on teardown.

    ``seed_project(**fields)`` forwards ``fields`` to ``POST /api/v1/projects``.
    """
    created: list[str] = []

    def _seed(**fields: Any) -> str:
        fields.setdefault("name", f"spec-audit-{uuid.uuid4().hex[:8]}")
        resp = projects_client.create_project(**fields)
        assert resp.status_code == 201, f"project create failed: {resp.status_code} {resp.text[:300]}"
        project_id = resp.json()["project"]["_id"]
        created.append(project_id)
        return project_id

    try:
        yield _seed
    finally:
        for project_id in created:
            projects_client.delete_project(project_id)


@pytest.fixture(scope="session")
def audit_agent(
    agents_client: AgentsClient,
    reasoning_multimodal_llm_model: SeededAIModel,
) -> Iterator[str]:
    """Key of one knowledge-free agent: a turn against it is a single LLM call, no retrieval."""
    resp = agents_client.create_agent(
        name=f"spec-audit-agent-{uuid.uuid4().hex[:8]}",
        models=[
            {
                "modelKey": reasoning_multimodal_llm_model.model_key,
                "modelName": reasoning_multimodal_llm_model.model_name,
                "provider": reasoning_multimodal_llm_model.provider,
                "isReasoning": True,
            }
        ],
    )
    if resp.status_code != 201:
        pytest.fail(f"could not create the audit agent: {resp.status_code} {resp.text[:300]}")
    agent_key = resp.json()["agent"]["_key"]
    try:
        yield agent_key
    finally:
        deleted = agents_client.delete_agent(agent_key)
        if deleted.status_code >= 300:
            logger.warning("could not delete audit agent %s: %s", agent_key, deleted.text[:300])


@pytest.fixture(scope="session")
def narrow_scope_headers(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Headers of an OAuth token of the suite's own client without any ``agent:*`` scope."""
    token = mint_narrow_scope_token(pipeshub_client.base_url, pipeshub_client.timeout_seconds)
    try:
        yield {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    finally:
        forget_access_token(token)


@pytest.fixture
def upload_attachment(
    agents_audit_client: AgentsAuditClient,
) -> Iterator[UploadAttachment]:
    """Factory: upload as the admin and return the raw response; uploads are deleted on teardown.

    ``upload_attachment(files=None, agent_key=SEED_AGENT_KEY, **kwargs)``; ``files`` defaults
    to one small text file.
    """
    record_ids: list[str] = []

    def _upload(
        files: MultipartFiles | None = None, agent_key: str = SEED_AGENT_KEY, **kwargs: Any
    ) -> Any:
        resp = agents_audit_client.upload(
            agent_key, attachment_files() if files is None else files, **kwargs
        )
        record_ids.extend(uploaded_record_ids(resp))
        return resp

    try:
        yield _upload
    finally:
        for record_id in record_ids:
            resp = agents_audit_client.delete_attachment(SEED_AGENT_KEY, record_id)
            if resp.status_code >= 300:
                logger.warning("could not delete attachment %s: HTTP %s", record_id, resp.status_code)


@pytest.fixture
def track_agent(agents_audit_client: AgentsAuditClient) -> Iterator[TrackAgent]:
    """Register the agent a 201 create response made; it is deleted on teardown unless already gone."""
    keys: list[str] = []

    def _track(resp: Any) -> str:
        assert resp.status_code == 201, f"agent create failed: {resp.status_code} {resp.text[:300]}"
        key = resp.json()["agent"]["_key"]
        keys.append(key)
        return key

    try:
        yield _track
    finally:
        for key in reversed(keys):
            resp = agents_audit_client.delete_agent(key)
            if resp.status_code not in (200, 404):
                logger.warning("could not delete agent %s: HTTP %s", key, resp.status_code)


@pytest.fixture
def make_agent(agents_audit_client: AgentsAuditClient, track_agent: TrackAgent) -> MakeAgent:
    """Factory: create an agent owned by the admin and return its key; deleted on teardown.

    ``make_agent(**fields)`` sends ``fields`` as the body; ``name`` defaults to a unique one.
    """

    def _make(**fields: Any) -> str:
        fields.setdefault("name", unique_agent_name())
        return track_agent(agents_audit_client.create_agent(fields))

    return _make
