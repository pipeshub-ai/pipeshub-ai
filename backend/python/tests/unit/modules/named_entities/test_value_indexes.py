"""Value lookups that filter only orgId (and kind) need an index of their own:
every other valueMentions index is sparse on a third field (Arango) or
composite (Neo4j), and neither serves a query that leaves that field out."""

from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import Neo4jLabel
from app.modules.named_entities.graph_ops import (
    ensure_named_entity_indexes,
    named_entity_neo4j_indexes,
)

_VALUES = CollectionNames.VALUE_MENTIONS.value


async def test_arango_has_a_non_sparse_org_kind_index_on_value_mentions() -> None:
    http_client = MagicMock(ensure_persistent_index=AsyncMock(return_value=True), execute_aql=AsyncMock())

    await ensure_named_entity_indexes(http_client)

    org_kind = [
        call for call in http_client.ensure_persistent_index.await_args_list
        if call.args[:2] == (_VALUES, ["orgId", "kind"])
    ]
    assert len(org_kind) == 1
    assert not org_kind[0].kwargs.get("sparse", False)


def test_neo4j_has_a_single_property_org_index_on_value_mentions() -> None:
    label = Neo4jLabel.VALUE_MENTIONS.value
    statements = named_entity_neo4j_indexes()

    assert f"CREATE INDEX value_mention_org IF NOT EXISTS FOR (v:{label}) ON (v.orgId)" in statements
