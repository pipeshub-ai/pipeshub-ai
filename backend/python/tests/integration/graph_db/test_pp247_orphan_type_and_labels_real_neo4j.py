"""PP-247 on Neo4j: connector delete removes type nodes and unused labels.

A label another connector still links, and that connector's relationship, stay.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_pp247_orphan_type_and_labels_real_neo4j.py -m integration
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import (
    collection_to_label,
    edge_collection_to_relationship,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("pp247-orphan-neo4j-it")
RECORDS = CollectionNames.RECORDS.value
FILES = CollectionNames.FILES.value
TOPICS = CollectionNames.TOPICS.value
APPS = CollectionNames.APPS.value
IS_OF_TYPE = CollectionNames.IS_OF_TYPE.value
BELONGS_TO_TOPIC = CollectionNames.BELONGS_TO_TOPIC.value


@pytest.fixture
async def neo4j(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Neo4jProvider, str]]:
    try:
        provider = await connect_neo4j(logger, monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    org_id = f"org-pp247-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id
    finally:
        with_suppress = provider.client.execute_query(
            """
            MATCH (n)
            WHERE n.orgId = $org OR n.id STARTS WITH $org
            DETACH DELETE n
            """,
            parameters={"org": org_id},
        )
        try:
            await with_suppress
        except Exception:
            logger.warning("neo4j cleanup failed", exc_info=True)
        await provider.disconnect()


async def _edge_count(provider: Neo4jProvider, record_id: str, topic_id: str) -> int:
    rel = edge_collection_to_relationship(BELONGS_TO_TOPIC)
    record_label = collection_to_label(RECORDS)
    topic_label = collection_to_label(TOPICS)
    rows = await provider.client.execute_query(
        f"""
        MATCH (r:`{record_label}` {{id: $record}})-[e:`{rel}`]->(t:`{topic_label}` {{id: $topic}})
        RETURN count(e) AS n
        """,
        parameters={"record": record_id, "topic": topic_id},
    )
    return int((rows or [{"n": 0}])[0]["n"])


async def test_connector_delete_keeps_the_other_connectors_label_link(neo4j) -> None:
    provider, org_id = neo4j
    conn_a = f"{org_id}-a"
    conn_b = f"{org_id}-b"
    rec_a = f"{org_id}-ra"
    rec_b = f"{org_id}-rb"
    topic = f"{org_id}-topic"
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

    await provider.batch_upsert_nodes([{
        "id": topic, "orgId": org_id, "name": "Shared",
    }], TOPICS)

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
        await provider.batch_upsert_nodes([{
            "id": record_key, "orgId": org_id, "name": "f", "isFile": True,
        }], FILES)
        await provider.batch_create_edges([{
            "from_id": record_key, "from_collection": RECORDS,
            "to_id": record_key, "to_collection": FILES,
            "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }], IS_OF_TYPE)
        await provider.batch_create_edges([{
            "from_id": record_key, "from_collection": RECORDS,
            "to_id": topic, "to_collection": TOPICS,
            "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }], BELONGS_TO_TOPIC)

    result = await provider.delete_connector_instance(conn_a, org_id)
    assert result.get("success") is True, result

    assert await provider.get_document(rec_a, RECORDS) is None
    assert await provider.get_document(rec_a, FILES) is None
    assert await provider.get_document(topic, TOPICS) is not None
    assert await provider.get_document(rec_b, RECORDS) is not None
    assert await provider.get_document(rec_b, FILES) is not None
    assert await _edge_count(provider, rec_b, topic) == 1
    assert await _edge_count(provider, rec_a, topic) == 0

    result = await provider.delete_connector_instance(conn_b, org_id)
    assert result.get("success") is True, result
    assert await provider.get_document(topic, TOPICS) is None
    assert await provider.get_document(rec_b, FILES) is None


async def test_enrichment_after_connector_delete_does_not_leave_a_new_label(neo4j) -> None:
    from app.models.blocks import SemanticMetadata
    from app.modules.entity_resolution.keys import taxonomy_node_key
    from app.modules.entity_resolution.models import (
        TOPIC,
        EntityResolution,
        ResolutionMode,
        ResolvedEntity,
    )
    from app.modules.entity_resolution.normalizer import normalize_name
    from app.modules.transformers.graphdb import GraphDBTransformer

    provider, org_id = neo4j
    connector_id = f"{org_id}-conn"
    record_key = f"{org_id}-rec"
    now = get_epoch_timestamp_in_ms()
    await provider.batch_upsert_nodes([{
        "id": connector_id, "name": "Notion", "type": "NOTION", "appGroup": "Notion",
        "scope": "team", "isActive": True, "orgId": org_id,
        "createdAtTimestamp": now, "updatedAtTimestamp": now,
    }], APPS)
    await provider.batch_upsert_nodes([{
        "id": record_key, "orgId": org_id, "recordName": "doc",
        "externalRecordId": record_key, "recordType": "FILE", "origin": "CONNECTOR",
        "connectorId": connector_id, "connectorName": "NOTION",
        "createdAtTimestamp": now, "updatedAtTimestamp": now,
    }], RECORDS)
    result = await provider.delete_connector_instance(connector_id, org_id)
    assert result.get("success") is True, result

    name = "Left after delete"
    normalized = normalize_name(name)
    key = taxonomy_node_key(org_id, TOPICS, normalized)
    resolution = EntityResolution(org_id=org_id, mode=ResolutionMode.APPLY)
    resolution.add(ResolvedEntity(
        kind=TOPIC, key=key, name=name, normalized=normalized,
        is_new=True, decision="new", extracted_names=[name],
    ))
    with pytest.raises(Exception, match="not found"):
        await GraphDBTransformer(provider, logger).save_metadata_to_db(
            record_key, SemanticMetadata(topics=[name]), "vr", resolution=resolution,
        )
    assert await provider.get_document(key, TOPICS) is None
