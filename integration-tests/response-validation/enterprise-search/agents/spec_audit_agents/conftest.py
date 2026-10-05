"""Shared fixtures for the strict OpenAPI audit of /api/v1/agents."""

from __future__ import annotations

import datetime
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection

_INTEGRATION_ROOT = Path(__file__).resolve().parents[4]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.projects_client import ProjectsClient  # noqa: E402
from helper.config import MONGO_DB_NAME, MONGO_URI  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402

from agents_audit_support import (  # noqa: E402
    CHAT_SESSIONS_COLLECTION,
    SEED_AGENT_KEY,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedProject,
)


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
def chat_sessions_collection() -> Iterator[Collection]:
    """Direct Mongo handle: the only API route that creates an agent conversation runs an LLM."""
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - any connection failure means "cannot seed"
        client.close()
        pytest.skip(f"MongoDB is not reachable at TEST_MONGO_URI, cannot seed agent conversations: {exc}")
    try:
        yield client[MONGO_DB_NAME][CHAT_SESSIONS_COLLECTION]
    finally:
        client.close()


@pytest.fixture
def seed_agent_conversation(
    chat_sessions_collection: Collection,
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
            chat_sessions_collection.delete_many({"_id": {"$in": created}})


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
