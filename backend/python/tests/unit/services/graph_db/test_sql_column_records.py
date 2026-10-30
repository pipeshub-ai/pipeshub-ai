"""SQL column records in the graph providers: stored in their own collection, read back typed."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    AccessRule,
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.neo4j import collection_to_label
from app.models.entities import RecordType, SQLColumnRecord
from app.schema.arango.documents import sql_column_record_schema
from app.services.graph_db.arango import arango_http_provider
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _column() -> SQLColumnRecord:
    return SQLColumnRecord(
        id="col-1", org_id="org-1", record_name="email", record_type=RecordType.SQL_COLUMN,
        external_record_id="db.public.users.email", origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.POSTGRESQL, connector_id="conn-1", mime_type=MimeTypes.SQL_COLUMN.value,
        version=1, fqn="db.public.users.email", table_fqn="public.users", data_type="text",
        is_unique=True, distinct_count=10, top_values=["a@x"], top_value_frequencies=[0.1], profiled_at=5,
    )


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), AsyncMock())
    provider.http_client = AsyncMock()
    return provider


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


class TestColumnCollection:
    def test_arango_creates_the_collection_with_its_schema(self) -> None:
        assert (CollectionNames.SQL_COLUMNS.value, sql_column_record_schema) in arango_http_provider.NODE_COLLECTIONS

    def test_neo4j_deletes_column_nodes_with_the_other_record_types(self) -> None:
        assert collection_to_label(CollectionNames.SQL_COLUMNS.value) in _neo4j()._record_type_labels()


class TestTypedColumnRecord:
    @pytest.mark.parametrize(("make", "builder"), [
        (_arango, "_create_typed_record_from_arango"),
        (_neo4j, "_create_typed_record_from_neo4j"),
    ], ids=["arango", "neo4j"])
    def test_a_column_reads_back_with_its_profile(self, make, builder) -> None:
        column = _column()

        restored = getattr(make(), builder)(column.to_arango_base_record(), column.to_arango_record())

        assert isinstance(restored, SQLColumnRecord)
        assert (restored.fqn, restored.table_fqn, restored.is_unique) == ("db.public.users.email", "public.users", True)
        assert (restored.distinct_count, restored.top_values, restored.profiled_at) == (10, ["a@x"], 5)

    @pytest.mark.parametrize(("make", "builder"), [
        (_arango, "_create_typed_record_from_arango"),
        (_neo4j, "_create_typed_record_from_neo4j"),
    ], ids=["arango", "neo4j"])
    def test_a_column_keeps_its_stored_access_rule(self, make, builder) -> None:
        column = _column().model_copy(update={"access_rule": AccessRule.RESTRICTED})

        restored = getattr(make(), builder)(column.to_arango_base_record(), column.to_arango_record())

        assert restored.access_rule == AccessRule.RESTRICTED


class TestLookupThatMustNotHideAFailure:
    async def test_arango_raises_the_graph_error_itself_when_asked(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(side_effect=ConnectionError("graph down"))

        with pytest.raises(ConnectionError):
            await provider.get_record_by_external_id("conn-1", "x", raise_on_error=True)

    async def test_neo4j_raises_the_graph_error_itself_when_asked(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(side_effect=ConnectionError("graph down"))

        with pytest.raises(ConnectionError):
            await provider.get_record_by_external_id("conn-1", "x", raise_on_error=True)
