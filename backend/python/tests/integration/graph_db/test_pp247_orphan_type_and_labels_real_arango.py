"""PP-247: Arango record / connector delete must not leave type docs or labels.

Reproduces two leftovers seen on Arango after deletes:

1. Type documents (files / webpages / …) whose matching record is gone.
2. Taxonomy label nodes (topics / categories / …) with no inbound belongsTo* edge.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_pp247_orphan_type_and_labels_real_arango.py -m integration -v
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, RECORD_TYPE_COLLECTION_MAPPING
from app.services.graph_db.taxonomy import TAXONOMY_COLLECTIONS, TAXONOMY_EDGE_COLLECTIONS
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("pp247-orphan-it")
ARANGO_DB = "pp247_orphan_it"
RECORDS = CollectionNames.RECORDS.value
FILES = CollectionNames.FILES.value
WEBPAGES = CollectionNames.WEBPAGES.value
TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value
IS_OF_TYPE = CollectionNames.IS_OF_TYPE.value
BELONGS_TO_TOPIC = CollectionNames.BELONGS_TO_TOPIC.value
BELONGS_TO_CATEGORY = CollectionNames.BELONGS_TO_CATEGORY.value
APPS = CollectionNames.APPS.value
INTER_CATEGORY = CollectionNames.INTER_CATEGORY_RELATIONS.value

TYPE_COLLECTIONS = sorted(set(RECORD_TYPE_COLLECTION_MAPPING.values()))


@pytest.fixture
async def arango() -> AsyncIterator[tuple]:
    try:
        provider = await connect_arango(logger, ARANGO_DB)
    except Exception as exc:
        backend_unavailable("arango", exc)
    org_id = f"org-pp247-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id
    finally:
        for collection in (
            IS_OF_TYPE,
            BELONGS_TO_TOPIC,
            BELONGS_TO_CATEGORY,
            INTER_CATEGORY,
            *TYPE_COLLECTIONS,
            *sorted(TAXONOMY_COLLECTIONS),
            RECORDS,
            APPS,
        ):
            try:
                await provider.http_client.execute_aql(
                    f"FOR d IN {collection} "
                    f"FILTER d.orgId == @org OR STARTS_WITH(d._key, @org) "
                    f"OR STARTS_WITH(d._from, @prefix) OR STARTS_WITH(d._to, @prefix) "
                    f"REMOVE d IN {collection}",
                    {"org": org_id, "prefix": f"{RECORDS}/{org_id}"},
                )
            except Exception:
                # Edge collections have no orgId; best-effort cleanup.
                await provider.http_client.execute_aql(
                    f"FOR d IN {collection} "
                    f"FILTER STARTS_WITH(d._key, @org) "
                    f"OR (HAS(d, '_from') AND CONTAINS(d._from, @org)) "
                    f"OR (HAS(d, '_to') AND CONTAINS(d._to, @org)) "
                    f"REMOVE d IN {collection}",
                    {"org": org_id},
                )
        await provider.disconnect()


async def _count_orphan_type_docs(provider, org_id: str, collection: str) -> int:
    rows = await provider.http_client.execute_aql(
        f"""
        FOR d IN {collection}
            FILTER d.orgId == @org
            FILTER DOCUMENT(CONCAT('{RECORDS}/', d._key)) == null
            COLLECT WITH COUNT INTO n
            RETURN n
        """,
        {"org": org_id},
    )
    return int(rows[0] if rows else 0)


async def _count_orphan_labels(provider, org_id: str, collection: str) -> int:
    edge = TAXONOMY_EDGE_COLLECTIONS[collection]
    rows = await provider.http_client.execute_aql(
        f"""
        FOR t IN {collection}
            FILTER t.orgId == @org
            FILTER LENGTH(
                FOR x IN {edge}
                    FILTER x._to == t._id
                    LIMIT 1
                    RETURN 1
            ) == 0
            COLLECT WITH COUNT INTO n
            RETURN n
        """,
        {"org": org_id},
    )
    return int(rows[0] if rows else 0)


async def _seed_app(provider, connector_id: str, org_id: str) -> None:
    now = get_epoch_timestamp_in_ms()
    await provider.batch_upsert_nodes([{
        "id": connector_id,
        "name": "Notion",
        "type": "NOTION",
        "appGroup": "Notion",
        "scope": "team",
        "isActive": True,
        "orgId": org_id,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }], APPS)


async def _seed_record_with_type_and_labels(
    provider,
    org_id: str,
    *,
    record_key: str,
    type_collection: str,
    topic_key: str,
    category_key: str,
    connector_id: str,
) -> None:
    now = get_epoch_timestamp_in_ms()
    await provider.batch_upsert_nodes([{
        "id": record_key,
        "orgId": org_id,
        "recordName": "doc",
        "externalRecordId": record_key,
        "recordType": "FILE" if type_collection == FILES else "WEBPAGE",
        "origin": "CONNECTOR",
        "connectorId": connector_id,
        "connectorName": "NOTION",
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }], RECORDS)
    if type_collection == FILES:
        type_doc = {"_key": record_key, "orgId": org_id, "name": "stub", "isFile": True}
    else:
        type_doc = {"_key": record_key, "orgId": org_id, "domain": "example.com"}
    await provider.http_client.execute_aql(
        f"INSERT @doc INTO {type_collection}",
        {"doc": type_doc},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_from: @f, _to: @t}} INTO {IS_OF_TYPE}",
        {"f": f"{RECORDS}/{record_key}", "t": f"{type_collection}/{record_key}"},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, name: 'Billing'}} INTO {TOPICS}",
        {"k": topic_key, "o": org_id},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_from: @f, _to: @t, createdAtTimestamp: 1}} INTO {BELONGS_TO_TOPIC}",
        {"f": f"{RECORDS}/{record_key}", "t": f"{TOPICS}/{topic_key}"},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, name: 'Policy'}} INTO {CATEGORIES}",
        {"k": category_key, "o": org_id},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_from: @f, _to: @t, createdAtTimestamp: 1}} INTO {BELONGS_TO_CATEGORY}",
        {"f": f"{RECORDS}/{record_key}", "t": f"{CATEGORIES}/{category_key}"},
    )


async def test_connector_delete_removes_live_isoftype_and_labels(arango) -> None:
    """delete_connector_instance alone, while records, isOfType edges and labels still exist."""
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    record_key = f"{org_id}-rec"
    topic_key = f"{org_id}-topic"
    category_key = f"{org_id}-cat"
    page_key = f"{org_id}-page"

    await _seed_app(provider, connector_id, org_id)
    await _seed_record_with_type_and_labels(
        provider,
        org_id,
        record_key=record_key,
        type_collection=FILES,
        topic_key=topic_key,
        category_key=category_key,
        connector_id=connector_id,
    )
    now = get_epoch_timestamp_in_ms()
    await provider.batch_upsert_nodes([{
        "id": page_key,
        "orgId": org_id,
        "recordName": "page",
        "externalRecordId": page_key,
        "recordType": "WEBPAGE",
        "origin": "CONNECTOR",
        "connectorId": connector_id,
        "connectorName": "NOTION",
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }], RECORDS)
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, domain: 'example.com'}} INTO {WEBPAGES}",
        {"k": page_key, "o": org_id},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_from: @f, _to: @t}} INTO {IS_OF_TYPE}",
        {"f": f"{RECORDS}/{page_key}", "t": f"{WEBPAGES}/{page_key}"},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_from: @f, _to: @t, createdAtTimestamp: 1}} INTO {BELONGS_TO_TOPIC}",
        {"f": f"{RECORDS}/{page_key}", "t": f"{TOPICS}/{topic_key}"},
    )

    result = await provider.delete_connector_instance(connector_id, org_id)
    assert result.get("success") is True, result

    assert await provider.get_document(connector_id, APPS) is None
    assert await provider.get_document(record_key, RECORDS) is None
    assert await provider.get_document(page_key, RECORDS) is None
    assert await provider.get_document(record_key, FILES) is None
    assert await provider.get_document(page_key, WEBPAGES) is None
    assert await provider.get_document(topic_key, TOPICS) is None
    assert await provider.get_document(category_key, CATEGORIES) is None
    isoftype = await provider.http_client.execute_aql(
        f"FOR e IN {IS_OF_TYPE} FILTER e._from IN @froms RETURN e",
        {"froms": [f"{RECORDS}/{record_key}", f"{RECORDS}/{page_key}"]},
    )
    assert isoftype == []
    assert await _count_orphan_type_docs(provider, org_id, FILES) == 0
    assert await _count_orphan_type_docs(provider, org_id, WEBPAGES) == 0
    assert await _count_orphan_labels(provider, org_id, TOPICS) == 0
    assert await _count_orphan_labels(provider, org_id, CATEGORIES) == 0


async def test_dangling_belongs_to_edge_does_not_keep_the_label(arango) -> None:
    """An edge whose record is already gone must not protect the label from the sweep."""
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    topic_key = f"{org_id}-dangling-topic"
    gone_record = f"{org_id}-gone"

    await _seed_app(provider, connector_id, org_id)
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, name: 'Dangling'}} INTO {TOPICS}",
        {"k": topic_key, "o": org_id},
    )
    # Record never inserted: the edge points at a missing record, the state after
    # a best-effort edge delete that missed belongsToTopic.
    await provider.http_client.execute_aql(
        f"INSERT {{_from: @f, _to: @t, createdAtTimestamp: 1}} INTO {BELONGS_TO_TOPIC}",
        {"f": f"{RECORDS}/{gone_record}", "t": f"{TOPICS}/{topic_key}"},
    )

    result = await provider.delete_connector_instance(connector_id, org_id)
    assert result.get("success") is True, result
    assert await provider.get_document(topic_key, TOPICS) is None


async def test_delete_nodes_and_edges_removes_type_doc(arango) -> None:
    """Sync on_record_deleted → delete_record_by_key uses delete_nodes_and_edges."""
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    record_key = f"{org_id}-rec"
    topic_key = f"{org_id}-topic"
    category_key = f"{org_id}-cat"

    await _seed_record_with_type_and_labels(
        provider,
        org_id,
        record_key=record_key,
        type_collection=FILES,
        topic_key=topic_key,
        category_key=category_key,
        connector_id=connector_id,
    )

    await provider.delete_nodes_and_edges([record_key], RECORDS)

    assert await provider.get_document(record_key, RECORDS) is None
    assert await provider.get_document(record_key, FILES) is None, (
        "type document must go with the record (PP-247)"
    )
    assert await _count_orphan_type_docs(provider, org_id, FILES) == 0


async def test_delete_nodes_and_edges_then_connector_clears_orphan_labels(arango) -> None:
    """After the last record for a topic/category is gone, connector delete sweeps labels."""
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    record_key = f"{org_id}-rec"
    topic_key = f"{org_id}-topic"
    category_key = f"{org_id}-cat"

    await _seed_app(provider, connector_id, org_id)
    await _seed_record_with_type_and_labels(
        provider,
        org_id,
        record_key=record_key,
        type_collection=FILES,
        topic_key=topic_key,
        category_key=category_key,
        connector_id=connector_id,
    )

    # First path that historically orphaned the type doc: edges+record only.
    await provider.delete_nodes_and_edges([record_key], RECORDS)

    result = await provider.delete_connector_instance(connector_id, org_id)
    assert result.get("success") is True, result

    assert await _count_orphan_type_docs(provider, org_id, FILES) == 0
    assert await _count_orphan_labels(provider, org_id, TOPICS) == 0
    assert await _count_orphan_labels(provider, org_id, CATEGORIES) == 0


async def test_connector_delete_sweeps_pre_orphaned_type_docs(arango) -> None:
    """Type docs left without a record (no isOfType) must still be removed on connector delete."""
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    orphan_file = f"{org_id}-orphan-file"
    orphan_page = f"{org_id}-orphan-page"

    await _seed_app(provider, connector_id, org_id)
    # No record, no edges — the state seen on test.pipeshub.com after earlier incomplete deletes.
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, name: 'accelerate.txt', isFile: true}} INTO {FILES}",
        {"k": orphan_file, "o": org_id},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, domain: 'example.com'}} INTO {WEBPAGES}",
        {"k": orphan_page, "o": org_id},
    )
    assert await _count_orphan_type_docs(provider, org_id, FILES) == 1
    assert await _count_orphan_type_docs(provider, org_id, WEBPAGES) == 1

    result = await provider.delete_connector_instance(connector_id, org_id)
    assert result.get("success") is True, result

    assert await _count_orphan_type_docs(provider, org_id, FILES) == 0
    assert await _count_orphan_type_docs(provider, org_id, WEBPAGES) == 0


async def test_shared_label_survives_when_another_record_still_links(arango) -> None:
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    shared_topic = f"{org_id}-shared-topic"
    rec_a = f"{org_id}-a"
    rec_b = f"{org_id}-b"
    now = get_epoch_timestamp_in_ms()

    await _seed_app(provider, connector_id, org_id)
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, name: 'Shared'}} INTO {TOPICS}",
        {"k": shared_topic, "o": org_id},
    )
    for record_key in (rec_a, rec_b):
        await provider.batch_upsert_nodes([{
            "id": record_key,
            "orgId": org_id,
            "recordName": record_key,
            "externalRecordId": record_key,
            "recordType": "FILE",
            "origin": "CONNECTOR",
            "connectorId": connector_id,
            "connectorName": "NOTION",
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }], RECORDS)
        await provider.http_client.execute_aql(
            f"INSERT {{_key: @k, orgId: @o, name: 'f', isFile: true}} INTO {FILES}",
            {"k": record_key, "o": org_id},
        )
        await provider.http_client.execute_aql(
            f"INSERT {{_from: @f, _to: @t}} INTO {IS_OF_TYPE}",
            {"f": f"{RECORDS}/{record_key}", "t": f"{FILES}/{record_key}"},
        )
        await provider.http_client.execute_aql(
            f"INSERT {{_from: @f, _to: @t, createdAtTimestamp: 1}} INTO {BELONGS_TO_TOPIC}",
            {"f": f"{RECORDS}/{record_key}", "t": f"{TOPICS}/{shared_topic}"},
        )

    # Delete only one record's graph presence; the shared topic must remain.
    await provider.delete_records_recursive([rec_a], connector_id)
    assert await provider.get_document(shared_topic, TOPICS) is not None

    # Full connector delete removes the last linking record and then the topic.
    result = await provider.delete_connector_instance(connector_id, org_id)
    assert result.get("success") is True, result
    assert await provider.get_document(shared_topic, TOPICS) is None
    assert await _count_orphan_labels(provider, org_id, TOPICS) == 0


async def test_shared_label_survives_delete_of_one_of_two_connectors(arango) -> None:
    """A topic linked from another connector in the same org must not be swept."""
    provider, org_id = arango
    conn_a = f"{org_id}-conn-a"
    conn_b = f"{org_id}-conn-b"
    shared_topic = f"{org_id}-cross-topic"
    rec_a = f"{org_id}-ra"
    rec_b = f"{org_id}-rb"
    now = get_epoch_timestamp_in_ms()

    for connector_id, name in ((conn_a, "Notion A"), (conn_b, "Notion B")):
        await provider.batch_upsert_nodes([{
            "id": connector_id,
            "name": name,
            "type": "NOTION",
            "appGroup": "Notion",
            "scope": "team",
            "isActive": True,
            "orgId": org_id,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }], APPS)

    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, name: 'Cross'}} INTO {TOPICS}",
        {"k": shared_topic, "o": org_id},
    )
    for record_key, connector_id in ((rec_a, conn_a), (rec_b, conn_b)):
        await provider.batch_upsert_nodes([{
            "id": record_key,
            "orgId": org_id,
            "recordName": record_key,
            "externalRecordId": record_key,
            "recordType": "FILE",
            "origin": "CONNECTOR",
            "connectorId": connector_id,
            "connectorName": "NOTION",
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }], RECORDS)
        await provider.http_client.execute_aql(
            f"INSERT {{_key: @k, orgId: @o, name: 'f', isFile: true}} INTO {FILES}",
            {"k": record_key, "o": org_id},
        )
        await provider.http_client.execute_aql(
            f"INSERT {{_from: @f, _to: @t}} INTO {IS_OF_TYPE}",
            {"f": f"{RECORDS}/{record_key}", "t": f"{FILES}/{record_key}"},
        )
        await provider.http_client.execute_aql(
            f"INSERT {{_from: @f, _to: @t, createdAtTimestamp: 1}} INTO {BELONGS_TO_TOPIC}",
            {"f": f"{RECORDS}/{record_key}", "t": f"{TOPICS}/{shared_topic}"},
        )

    result = await provider.delete_connector_instance(conn_a, org_id)
    assert result.get("success") is True, result
    assert await provider.get_document(shared_topic, TOPICS) is not None
    assert await provider.get_document(rec_b, RECORDS) is not None
    assert await provider.get_document(rec_b, FILES) is not None
    kept = await provider.http_client.execute_aql(
        f"FOR e IN {BELONGS_TO_TOPIC} FILTER e._from == @f RETURN e._to",
        {"f": f"{RECORDS}/{rec_b}"},
    )
    removed = await provider.http_client.execute_aql(
        f"FOR e IN {BELONGS_TO_TOPIC} FILTER e._from == @f RETURN e._to",
        {"f": f"{RECORDS}/{rec_a}"},
    )
    assert kept == [f"{TOPICS}/{shared_topic}"]
    assert removed == []


async def test_recursive_delete_removes_every_isoftype_target(arango) -> None:
    """A record with two isOfType edges must lose both type documents."""
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    record_key = f"{org_id}-rec"
    extra_page = f"{org_id}-extra-page"
    now = get_epoch_timestamp_in_ms()

    await _seed_app(provider, connector_id, org_id)
    await provider.batch_upsert_nodes([{
        "id": record_key,
        "orgId": org_id,
        "recordName": "doc",
        "externalRecordId": record_key,
        "recordType": "FILE",
        "origin": "CONNECTOR",
        "connectorId": connector_id,
        "connectorName": "NOTION",
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }], RECORDS)
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, name: 'f', isFile: true}} INTO {FILES}",
        {"k": record_key, "o": org_id},
    )
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, orgId: @o, domain: 'example.com'}} INTO {WEBPAGES}",
        {"k": extra_page, "o": org_id},
    )
    for target in (f"{FILES}/{record_key}", f"{WEBPAGES}/{extra_page}"):
        await provider.http_client.execute_aql(
            f"INSERT {{_from: @f, _to: @t}} INTO {IS_OF_TYPE}",
            {"f": f"{RECORDS}/{record_key}", "t": target},
        )

    await provider.delete_records_recursive([record_key], connector_id)

    assert await provider.get_document(record_key, RECORDS) is None
    assert await provider.get_document(record_key, FILES) is None
    assert await provider.get_document(extra_page, WEBPAGES) is None


async def test_connector_delete_sweeps_orphan_mail_without_org_id(arango) -> None:
    """Mails cannot store orgId. An already-orphaned mail still goes on connector delete."""
    provider, org_id = arango
    connector_id = f"{org_id}-conn"
    mail_key = f"{org_id}-mail"

    await _seed_app(provider, connector_id, org_id)
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @k, threadId: 't', isParent: false}} INTO {CollectionNames.MAILS.value}",
        {"k": mail_key},
    )
    assert await provider.get_document(mail_key, CollectionNames.MAILS.value) is not None

    result = await provider.delete_connector_instance(connector_id, org_id)
    assert result.get("success") is True, result
    assert await provider.get_document(mail_key, CollectionNames.MAILS.value) is None
