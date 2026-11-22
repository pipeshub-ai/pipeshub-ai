"""Document collections a build adds through ``app.edition.extra_node_collections`` get, on Neo4j, the unique ``id`` constraint
that keeps ``MERGE`` an index seek and convergent under concurrent writers, plus the indexes they declare; on ArangoDB a failed
index is reported."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.graph_db.arango.arango_http_provider as arango_module
import app.services.graph_db.neo4j.neo4j_provider as neo4j_module
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

EXTRA = {"extraDocs": (("orgId", "kind"), ("orgId",))}


def test_neo4j_gives_an_extra_collection_the_unique_id_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(neo4j_module, "extra_node_collections", lambda: EXTRA)
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    label = neo4j_module.collection_to_label("extraDocs")
    assert f"CREATE CONSTRAINT {label.lower()}_id_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE" in (
        provider._generate_unique_id_constraints()
    )


def test_neo4j_creates_the_indexes_an_extra_collection_declares(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(neo4j_module, "extra_node_collections", lambda: EXTRA)
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    label = neo4j_module.collection_to_label("extraDocs")
    indexes = provider._generate_performance_indexes()
    assert any(i.endswith(f"IF NOT EXISTS FOR (n:{label}) ON (n.orgId, n.kind)") for i in indexes)
    assert any(i.endswith(f"IF NOT EXISTS FOR (n:{label}) ON (n.orgId)") for i in indexes)


def test_neo4j_names_differ_for_field_lists_that_join_alike_and_key_is_indexed_as_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(neo4j_module, "extra_node_collections", lambda: {"extraDocs": (("a_b", "c"), ("a", "b_c"), ("orgId", "_key"))})
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    label = neo4j_module.collection_to_label("extraDocs")
    mine = [i for i in provider._generate_performance_indexes() if f"(n:{label})" in i]
    assert len({i.split()[2] for i in mine}) == 3
    assert any(i.endswith("ON (n.orgId, n.id)") for i in mine)


def test_extra_collections_add_exactly_their_constraint_and_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    monkeypatch.setattr(neo4j_module, "extra_node_collections", lambda: {})
    constraints, indexes = provider._generate_unique_id_constraints(), provider._generate_performance_indexes()
    monkeypatch.setattr(neo4j_module, "extra_node_collections", lambda: EXTRA)
    assert len(provider._generate_unique_id_constraints()) == len(constraints) + 1
    assert len(provider._generate_performance_indexes()) == len(indexes) + 2


@pytest.mark.asyncio
async def test_arango_reports_an_extra_index_it_could_not_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arango_module, "extra_node_collections", lambda: EXTRA)
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.ensure_persistent_index = AsyncMock(side_effect=lambda col, fields, *_, **__: col != "extraDocs" or fields != ["orgId"])
    await provider._ensure_indexes()
    errors = [c for c in provider.logger.error.call_args_list if "was not created" in str(c)]
    assert len(errors) == 1
    assert "extraDocs" in str(errors[0])
