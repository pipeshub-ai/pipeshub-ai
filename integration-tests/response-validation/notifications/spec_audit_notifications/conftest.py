"""Shared fixtures for the strict OpenAPI audit of /api/v1/notifications."""

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

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.config import MONGO_DB_NAME, MONGO_URI  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from notifications_audit_support import (  # noqa: E402
    COLLECTION,
    NotificationsClient,
    SeedNotification,
)


@pytest.fixture(scope="session")
def notifications_client(pipeshub_client: PipeshubClient) -> NotificationsClient:
    return NotificationsClient(pipeshub_client)


@pytest.fixture(scope="session")
def admin_user_id(pipeshub_client: PipeshubClient) -> str:
    """The Mongo user id the admin token resolves to, i.e. whose notifications it sees."""
    user_id = pipeshub_client.acting_user_id
    if not user_id:
        pytest.fail("admin access token carries no user identity")
    return user_id


@pytest.fixture(scope="session")
def notifications_collection() -> Iterator[Collection]:
    """Direct Mongo handle: no API route creates a notification, only the broker consumer."""
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - any connection failure means "cannot seed"
        client.close()
        pytest.fail(f"MongoDB is not reachable at TEST_MONGO_URI, cannot seed notifications: {exc}")
    try:
        yield client[MONGO_DB_NAME][COLLECTION]
    finally:
        client.close()


@pytest.fixture
def seed_notification(
    notifications_collection: Collection,
    pipeshub_client: PipeshubClient,
    admin_user_id: str,
) -> Iterator[SeedNotification]:
    """Factory: insert one notification and return its id; every one is removed on teardown.

    ``seed_notification(status="unread", assigned_to=None, **fields)``. ``assigned_to``
    defaults to the admin; pass ``second_user.user_id`` for the member's own. A field
    passed as ``None`` is left out of the document.
    """
    created: list[ObjectId] = []

    def _seed(
        status: str = "unread", assigned_to: str | None = None, **fields: Any
    ) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        document: dict[str, Any] = {
            "orgId": ObjectId(pipeshub_client.org_id),
            "assignedTo": ObjectId(assigned_to or admin_user_id),
            "type": "CONNECTOR_INFO",
            "title": f"spec-audit {uuid.uuid4().hex[:8]}",
            "message": "Seeded by the notifications spec audit",
            "severity": "info",
            "originService": "Connector Service",
            "status": status,
            "isDeleted": False,
            "createdAt": now,
            "updatedAt": now,
            # Mongoose stamps this on every document the consumer saves.
            "__v": 0,
            **fields,
        }
        document = {key: value for key, value in document.items() if value is not None}
        inserted = notifications_collection.insert_one(document).inserted_id
        created.append(inserted)
        return str(inserted)

    try:
        yield _seed
    finally:
        if created:
            notifications_collection.delete_many({"_id": {"$in": created}})
