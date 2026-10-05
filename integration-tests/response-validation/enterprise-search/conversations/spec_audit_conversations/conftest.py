"""Shared fixtures for the strict OpenAPI audit of /api/v1/conversations."""

from __future__ import annotations

import datetime
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
import requests
from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection

_INTEGRATION_ROOT = Path(__file__).resolve().parents[4]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.config import MONGO_DB_NAME, MONGO_URI  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402

from conversations_audit_support import (  # noqa: E402
    COLLECTION,
    ConversationsAuditClient,
    MultipartFiles,
    SeedConversation,
    UploadAttachment,
    attachment_files,
    uploaded_record_ids,
)

logger = logging.getLogger("conversations-spec-audit")


@pytest.fixture(scope="session")
def conversations_audit_client(pipeshub_client: PipeshubClient) -> ConversationsAuditClient:
    return ConversationsAuditClient(pipeshub_client)


@pytest.fixture(scope="session")
def admin_user_id(pipeshub_client: PipeshubClient) -> str:
    """The Mongo user id the admin token resolves to, i.e. who owns admin conversations."""
    user_id = pipeshub_client.acting_user_id
    if not user_id:
        pytest.fail("admin access token carries no user identity")
    return user_id


@pytest.fixture(scope="session")
def chat_sessions_collection() -> Iterator[Collection]:
    """Direct Mongo handle: every API route that creates a conversation costs an LLM call."""
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - any connection failure means "cannot seed"
        client.close()
        pytest.skip(f"MongoDB is not reachable at TEST_MONGO_URI, cannot seed conversations: {exc}")
    try:
        yield client[MONGO_DB_NAME][COLLECTION]
    finally:
        client.close()


@pytest.fixture
def seed_conversation(
    chat_sessions_collection: Collection,
    pipeshub_client: PipeshubClient,
    admin_user_id: str,
) -> Iterator[SeedConversation]:
    """Factory: insert one message-less chat session and return its id; all removed on teardown.

    ``seed_conversation(owner=None, project_id=None, **fields)``. ``owner`` defaults to the
    admin; pass ``second_user.user_id`` for the member's own. ``project_id`` links it to a
    project (any ObjectId string: the visibility route only checks the link is present).
    """
    created: list[ObjectId] = []

    def _seed(
        owner: str | None = None, project_id: str | None = None, **fields: Any
    ) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        owner_id = ObjectId(owner or admin_user_id)
        document: dict[str, Any] = {
            "sessionType": "chat",
            "nextSeq": 0,
            "orgId": ObjectId(pipeshub_client.org_id),
            "userId": owner_id,
            "initiator": owner_id,
            "title": f"spec-audit {uuid.uuid4().hex[:8]}",
            "isShared": False,
            "sharedWith": [],
            "isDeleted": False,
            "isArchived": False,
            "lastActivityAt": int(time.time() * 1000),
            "status": "Complete",
            "conversationErrors": [],
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
            **fields,
        }
        if project_id is not None:
            document["projectId"] = ObjectId(project_id)
            document.setdefault("projectVisibility", "private")
        inserted = chat_sessions_collection.insert_one(document).inserted_id
        created.append(inserted)
        return str(inserted)

    try:
        yield _seed
    finally:
        if created:
            chat_sessions_collection.delete_many({"_id": {"$in": created}})


@pytest.fixture
def upload_attachment(
    conversations_audit_client: ConversationsAuditClient,
) -> Iterator[UploadAttachment]:
    """Factory: upload as the admin and return the raw response; uploads are deleted on teardown.

    ``upload_attachment(files=None, **kwargs)``. ``files`` defaults to one small text file;
    kwargs go to ``ConversationsAuditClient.upload_attachments``. Read ids with
    ``uploaded_record_ids(resp)``.
    """
    record_ids: list[str] = []

    def _upload(files: MultipartFiles | None = None, **kwargs: Any) -> requests.Response:
        resp = conversations_audit_client.upload_attachments(
            attachment_files() if files is None else files, **kwargs
        )
        record_ids.extend(uploaded_record_ids(resp))
        return resp

    try:
        yield _upload
    finally:
        for record_id in record_ids:
            # Already-deleted ids answer 204 too, so a test may delete its own upload.
            resp = conversations_audit_client.delete_attachment(record_id)
            if resp.status_code >= 300:
                logger.warning(
                    "Could not delete attachment %s: HTTP %s", record_id, resp.status_code
                )
