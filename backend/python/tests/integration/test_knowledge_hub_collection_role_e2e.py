"""Knowledge Hub's ``collectionRole`` against a real Neo4j and a real ArangoDB.

The collection header offers "Recently deleted" to the roles the trash API
serves (OWNER, WRITER, FILEORGANIZER). Knowledge Hub's context ``role`` can't
decide that inside a folder: there it ranks record permissions, which have no
FILEORGANIZER, so a file organizer reads as READER. ``collectionRole`` is the
user's own role on the collection, from the check restore and the trash list
use, at the collection and in a folder inside it alike.

Seeds the restore suite's collection with a file organizer and a reader beside
its owner.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  cd backend/python && pytest tests/integration/test_knowledge_hub_collection_role_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration import test_soft_delete_restore_e2e as restore_suite

if TYPE_CHECKING:
    from tests.integration.test_soft_delete_restore_e2e import _World

# The restore suite's seeded collection, on both databases.
seeded = restore_suite.world

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("knowledge-hub-collection-role-it")


async def _member(w: _World, role: str) -> str:
    """A user of the collection's org with *role* on the collection; returns their key."""
    key = f"{role.lower()}-{uuid.uuid4().hex[:10]}"
    w.ids[key] = key
    now = get_epoch_timestamp_in_ms()
    await w.graph.batch_upsert_nodes(
        [{"id": key, "userId": f"user-{key}", "orgId": w.org_id, "email": f"{key}@example.com",
          "fullName": role.title(), "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await w.graph.batch_create_edges(
        [restore_suite._edge(key, CollectionNames.USERS.value, w.kb_id, CollectionNames.APPS.value,
                             role=role, type="USER")],
        collection=CollectionNames.PERMISSION.value,
    )
    return key


@pytest.mark.parametrize("role", ["FILEORGANIZER", "READER", "WRITER"])
async def test_the_collection_role_is_the_same_at_the_collection_and_in_a_folder(seeded: _World, role: str) -> None:
    user = await _member(seeded, role)
    hub = KnowledgeHubService(logger, seeded.graph)

    at_collection = await hub._get_permissions(user, seeded.org_id, seeded.kb_id, "app")
    in_folder = await hub._get_permissions(user, seeded.org_id, seeded.ids["folder"], "folder")

    assert at_collection is not None and in_folder is not None
    assert (at_collection.collectionRole, in_folder.collectionRole) == (role, role)


async def test_a_file_organizer_reads_as_a_reader_in_a_folder_but_keeps_their_collection_role(seeded: _World) -> None:
    """Why the header can't use the context role: inside a folder it has no FILEORGANIZER."""
    user = await _member(seeded, "FILEORGANIZER")
    hub = KnowledgeHubService(logger, seeded.graph)

    in_folder = await hub._get_permissions(user, seeded.org_id, seeded.ids["folder"], "folder")

    assert in_folder is not None
    assert in_folder.role != "FILEORGANIZER"
    assert in_folder.collectionRole == "FILEORGANIZER"


async def test_a_node_outside_a_collection_has_no_collection_role(seeded: _World) -> None:
    hub = KnowledgeHubService(logger, seeded.graph)

    drive = await hub._get_permissions(seeded.user_key, seeded.org_id, seeded.drive_id, "app")
    elsewhere = await hub._get_permissions(seeded.user_key, f"{seeded.org_id}-other", seeded.ids["folder"], "folder")

    assert drive is None or drive.collectionRole is None
    assert elsewhere is None or elsewhere.collectionRole is None
