"""ensure_pipeline_schema on both engines, against recorded client calls."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.schema.arango.documents import record_schema, stage_state_schema
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

STAGES = CollectionNames.STAGE_STATES.value
RECORDS = CollectionNames.RECORDS.value
LABEL = collection_to_label(STAGES)


def _arango(*, stages_exist: bool = True) -> tuple[ArangoHTTPProvider, AsyncMock]:
    provider = ArangoHTTPProvider(MagicMock(), AsyncMock())
    client = AsyncMock()
    client.has_collection = AsyncMock(return_value=stages_exist)
    client.create_collection = AsyncMock(return_value=True)
    client.update_collection_schema = AsyncMock(return_value=True)
    client.ensure_persistent_index = AsyncMock(return_value=True)
    provider.http_client = client
    return provider, client


class TestArangoPipelineSchema:
    async def test_creates_the_stage_store_and_applies_both_schemas(self) -> None:
        provider, client = _arango(stages_exist=False)

        await provider.ensure_pipeline_schema()

        client.create_collection.assert_awaited_once_with(STAGES, schema=stage_state_schema)
        assert [call.args for call in client.update_collection_schema.await_args_list] == [
            (STAGES, stage_state_schema),
            (RECORDS, record_schema),
        ]
        assert [call.args for call in client.ensure_persistent_index.await_args_list] == [
            (STAGES, ["status", "updatedAtMs"]),
            (STAGES, ["virtualRecordId", "rev"]),
        ]

    async def test_an_existing_stage_store_is_not_created_again(self) -> None:
        provider, client = _arango()

        await provider.ensure_pipeline_schema()

        client.create_collection.assert_not_awaited()
        assert client.update_collection_schema.await_count == 2

    async def test_records_the_connector_service_has_not_created_yet_raise(self) -> None:
        provider, client = _arango()
        client.update_collection_schema = AsyncMock(side_effect=lambda name, _schema: name != RECORDS)

        with pytest.raises(RuntimeError, match=RECORDS):
            await provider.ensure_pipeline_schema()

    async def test_an_index_that_cannot_be_ensured_raises(self) -> None:
        provider, client = _arango()
        client.ensure_persistent_index = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="index"):
            await provider.ensure_pipeline_schema()

    async def test_raises_when_not_connected(self) -> None:
        provider = ArangoHTTPProvider(MagicMock(), AsyncMock())
        provider.http_client = None

        with pytest.raises(RuntimeError, match="not connected"):
            await provider.ensure_pipeline_schema()


def _neo4j(execute: AsyncMock) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = execute
    return provider


def _queries(execute: AsyncMock) -> list[str]:
    return [" ".join(str(call.args[0]).split()) for call in execute.await_args_list]


def _engine(*, constraint_errors: int = 0, duplicates: int = 0, found: int = 1) -> AsyncMock:
    """A Neo4j that rejects the first ``constraint_errors`` constraint creations."""
    remaining = {"errors": constraint_errors}

    async def execute(query: str, parameters: dict[str, object] | None = None) -> list[dict[str, object]]:
        if query.startswith("CREATE CONSTRAINT"):
            if remaining["errors"]:
                remaining["errors"] -= 1
                raise RuntimeError("Unable to create Constraint: equal property values exist")
            return []
        if "DETACH DELETE" in query:
            return [{"removed": duplicates}]
        if query.startswith("SHOW CONSTRAINTS"):
            assert parameters == {"label": LABEL}
            return [{"found": found}]
        return []

    return AsyncMock(side_effect=execute)


class TestNeo4jPipelineSchema:
    async def test_creates_the_unique_key_and_indexes_then_verifies_the_key(self) -> None:
        execute = _engine()

        await _neo4j(execute).ensure_pipeline_schema()

        queries = _queries(execute)
        assert queries[0] == (
            f"CREATE CONSTRAINT {LABEL.lower()}_id_unique IF NOT EXISTS FOR (n:{LABEL}) REQUIRE n.id IS UNIQUE"
        )
        assert any("stage_state_status_updated" in query for query in queries)
        assert any("stage_state_revision" in query for query in queries)
        assert queries[-1].startswith("SHOW CONSTRAINTS")
        assert not any("DETACH DELETE" in query for query in queries)

    async def test_duplicates_that_block_the_unique_key_are_removed_first(self) -> None:
        execute = _engine(constraint_errors=1, duplicates=2)
        provider = _neo4j(execute)

        await provider.ensure_pipeline_schema()

        queries = _queries(execute)
        assert sum(query.startswith("CREATE CONSTRAINT") for query in queries) == 2
        assert any("DETACH DELETE" in query for query in queries)
        provider.logger.warning.assert_called_once()

    async def test_a_constraint_failure_with_no_duplicates_raises(self) -> None:
        execute = _engine(constraint_errors=1, duplicates=0)

        with pytest.raises(RuntimeError, match="equal property values"):
            await _neo4j(execute).ensure_pipeline_schema()

    async def test_a_unique_key_missing_after_creation_raises(self) -> None:
        execute = _engine(found=0)

        with pytest.raises(RuntimeError, match="unique constraint"):
            await _neo4j(execute).ensure_pipeline_schema()

    async def test_raises_when_not_connected(self) -> None:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        provider.client = None

        with pytest.raises(RuntimeError, match="not connected"):
            await provider.ensure_pipeline_schema()

    def test_the_connector_bootstrap_creates_the_same_stage_state_schema(self) -> None:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        constraint = f"CREATE CONSTRAINT {LABEL.lower()}_id_unique IF NOT EXISTS FOR (n:{LABEL}) REQUIRE n.id IS UNIQUE"

        assert constraint in provider._generate_unique_id_constraints()
        indexes = provider._generate_performance_indexes()
        assert any("stage_state_status_updated" in index for index in indexes)
        assert any("stage_state_revision" in index for index in indexes)
