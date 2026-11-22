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
    assert f"CREATE INDEX {label.lower()}_orgid_kind IF NOT EXISTS FOR (n:{label}) ON (n.orgId, n.kind)" in indexes
    assert f"CREATE INDEX {label.lower()}_orgid IF NOT EXISTS FOR (n:{label}) ON (n.orgId)" in indexes


def test_the_default_adds_no_constraint_or_index(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    before = (provider._generate_unique_id_constraints(), provider._generate_performance_indexes())
    monkeypatch.setattr(neo4j_module, "extra_node_collections", lambda: {})
    assert (provider._generate_unique_id_constraints(), provider._generate_performance_indexes()) == before


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
