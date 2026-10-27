"""A single connector record delete on ArangoDB leaves no edge behind.

The Drive, Gmail/Outlook and local-FS deletes, and the hard delete Jira Data
Center and Linear use, each walked its own fixed list of edge collections. A
record in a record group also inherits that group's access over an
inheritPermissions edge, and a ticket-like record links people over
entityRelations; neither was on the lists, so both edges outlived the record
and pointed at a missing node.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_delete_all_edges_real_arango.py -m integration
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, GraphNames
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("record-delete-all-edges-it")
ARANGO_DB = "record_delete_all_edges_it"
RECORDS = CollectionNames.RECORDS.value
FILES = CollectionNames.FILES.value
MAILS = CollectionNames.MAILS.value
USERS = CollectionNames.USERS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
TOPICS = CollectionNames.TOPICS.value


@pytest.fixture
async def arango() -> AsyncIterator[tuple]:
    try:
        provider = await connect_arango(logger, ARANGO_DB)
    except Exception as exc:
        backend_unavailable("arango", exc)
    org_id = f"org-del-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id
    finally:
        for collection in (RECORDS, FILES, MAILS, USERS, GROUPS, TOPICS):
            await provider.http_client.execute_aql(
                f"FOR d IN {collection} FILTER STARTS_WITH(d._key, @org) REMOVE d IN {collection}", {"org": org_id},
            )
        await provider.disconnect()


async def _insert(provider, collection: str, doc: dict) -> None:
    await provider.http_client.execute_aql(f"INSERT @doc INTO {collection}", {"doc": doc})


async def _edge(provider, collection: str, frm: str, to: str, **fields) -> None:
    await _insert(provider, collection, {"_from": frm, "_to": to, "createdAtTimestamp": 1, **fields})


async def _seed(provider, org_id: str, connector: str, type_collection: str) -> tuple[str, dict]:
    """A record with its type node and one edge in every collection that reaches a connector record."""
    rec, parent, user, group, topic = (f"{org_id}-{n}" for n in ("rec", "parent", "user", "group", "topic"))
    record_type = "MAIL" if type_collection == MAILS else "FILE"
    for key in (rec, parent):
        await provider.batch_upsert_nodes([{
            "id": key, "orgId": org_id, "recordName": key, "externalRecordId": key,
            "recordType": record_type, "origin": "CONNECTOR", "connectorName": connector,
            "connectorId": f"{org_id}-conn", "virtualRecordId": f"{key}-v", "createdAtTimestamp": 1,
        }], RECORDS)
    type_doc = {"_key": rec, "threadId": "t", "isParent": True} if type_collection == MAILS else {"_key": rec, "name": rec}
    await _insert(provider, type_collection, type_doc)
    await _insert(provider, USERS, {"_key": user, "email": f"{user}@example.com", "orgId": org_id})
    await _insert(provider, GROUPS, {
        "_key": group, "groupName": group, "groupType": "DRIVE", "connectorName": connector,
        "createdAtTimestamp": 1, "orgId": org_id,
    })
    await _insert(provider, TOPICS, {"_key": topic, "name": topic, "orgId": org_id})

    r = f"{RECORDS}/{rec}"
    await _edge(provider, CollectionNames.IS_OF_TYPE.value, r, f"{type_collection}/{rec}")
    await _edge(provider, CollectionNames.PERMISSION.value, f"{USERS}/{user}", r, type="USER", role="OWNER")
    await _edge(provider, CollectionNames.BELONGS_TO.value, r, f"{GROUPS}/{group}")
    await _edge(provider, CollectionNames.INHERIT_PERMISSIONS.value, r, f"{GROUPS}/{group}")
    await _edge(provider, CollectionNames.NODE_RELATIONS.value, f"{RECORDS}/{parent}", r, relationshipType="PARENT_CHILD")
    await _edge(provider, CollectionNames.ENTITY_RELATIONS.value, r, f"{USERS}/{user}", edgeType="CREATED_BY")
    await _edge(provider, CollectionNames.BELONGS_TO_TOPIC.value, r, f"{TOPICS}/{topic}")
    record = await provider.http_client.get_document(collection=RECORDS, key=rec)
    return rec, record


async def _edges_touching(provider, ids: list[str]) -> dict[str, int]:
    counts = {}
    for collection in await provider._edge_collections_of_graph(GraphNames.KNOWLEDGE_GRAPH.value):
        rows = await provider.http_client.execute_aql(
            f"FOR e IN {collection} FILTER e._from IN @ids OR e._to IN @ids COLLECT WITH COUNT INTO n RETURN n",
            {"ids": ids},
        )
        if rows and rows[0]:
            counts[collection] = rows[0]
    return counts


@pytest.mark.parametrize(
    ("connector", "type_collection", "delete"),
    [
        pytest.param(
            Connectors.GOOGLE_DRIVE.value, FILES,
            lambda p, rec, record: p._execute_drive_record_deletion(rec, record, "OWNER"), id="drive",
        ),
        pytest.param(
            Connectors.GOOGLE_MAIL.value, MAILS,
            lambda p, rec, record: p._execute_gmail_record_deletion(rec, record, "OWNER"), id="gmail",
        ),
        pytest.param(
            Connectors.OUTLOOK.value, MAILS,
            lambda p, rec, record: p._execute_outlook_record_deletion(rec, record), id="outlook",
        ),
        pytest.param(
            Connectors.LOCAL_FS.value, FILES,
            lambda p, rec, record: p._execute_local_fs_record_deletion(rec, record), id="local-fs",
        ),
        pytest.param(
            Connectors.JIRA.value, FILES,
            lambda p, rec, record: p.delete_records_and_relations(rec, hard_delete=True),
            id="jira-dc-linear-hard-delete",
        ),
    ],
)
async def test_a_connector_record_delete_leaves_no_edge_behind(arango, connector, type_collection, delete) -> None:
    provider, org_id = arango
    rec, record = await _seed(provider, org_id, connector, type_collection)
    ids = [f"{RECORDS}/{rec}", f"{type_collection}/{rec}"]
    assert len(await _edges_touching(provider, ids)) == 7

    result = await delete(provider, rec, record)

    assert result is True or result["success"], result
    assert await _edges_touching(provider, ids) == {}
    assert await provider.http_client.get_document(collection=RECORDS, key=rec) is None
    assert await provider.http_client.get_document(collection=type_collection, key=rec) is None
    # Only the deleted record's links go: its parent, user, group and topic stay.
    for collection, key in ((RECORDS, f"{org_id}-parent"), (USERS, f"{org_id}-user"), (GROUPS, f"{org_id}-group"), (TOPICS, f"{org_id}-topic")):
        assert await provider.http_client.get_document(collection=collection, key=key) is not None
