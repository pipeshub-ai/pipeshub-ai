"""The statements that report which edges a write created are sent only when the count is asked for or an `edge_effects.track` block is
active (the store's structure bookkeeping, which exists only while the scope fields are on). Otherwise each writer sends the statement it
always did."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.node_validator import NodeSchemaValidator
from app.services.graph_db import edge_effects
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

EDGE = {
    "from_id": "r1", "from_collection": "records", "to_id": "g1", "to_collection": "recordGroups",
    "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
}
REL = {"from_id": "r1", "from_collection": "records", "to_id": "r2", "to_collection": "records", "relationshipType": "PARENT_CHILD",
       "createdAtTimestamp": 1, "updatedAtTimestamp": 1}
BEL = CollectionNames.BELONGS_TO.value


def _neo4j() -> tuple[Neo4jProvider, AsyncMock]:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider.validator = NodeSchemaValidator()
    run = AsyncMock(return_value=[{"made": [["r1", "g1"]], "created": 1, "matched": 1, "upserted": 1}])
    provider.client = SimpleNamespace(execute_query=run)
    return provider, run


def _arango() -> tuple[ArangoHTTPProvider, AsyncMock]:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    run = AsyncMock(return_value=[["records/r1", "recordGroups/g1"]])
    provider.http_client = SimpleNamespace(execute_aql=run)
    return provider, run


def _text(run: AsyncMock) -> str:
    return "\n".join(str(c.args[0]) for c in run.await_args_list)


CALLS = {
    "batch_create_edges": lambda p, **k: p.batch_create_edges([EDGE], BEL, **k),
    "create_edges_if_absent": lambda p, **k: p.create_edges_if_absent([EDGE], BEL, **k),
    "batch_upsert_node_relations": lambda p, **k: p.batch_upsert_node_relations([REL], **k),
}
MARKERS = {"neo4j": ("edgeNew", "isNew"), "arango": ("OLD == null",)}


@pytest.mark.parametrize("name", sorted(CALLS))
@pytest.mark.parametrize("store", ["neo4j", "arango"])
async def test_the_plain_statement_is_sent_unless_the_count_is_asked_for_or_tracked(store: str, name: str) -> None:
    provider, run = _neo4j() if store == "neo4j" else _arango()
    await CALLS[name](provider)
    plain = _text(run)
    assert not any(m in plain for m in MARKERS[store]), plain
    run.reset_mock()
    await CALLS[name](provider, count_created=True)
    assert all(any(m in t for t in [_text(run)]) for m in MARKERS[store][:1]), _text(run)
    run.reset_mock()
    with edge_effects.track():
        await CALLS[name](provider)
    assert MARKERS[store][0] in _text(run)
