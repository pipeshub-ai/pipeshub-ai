"""The SQL connector hooks on the entities processor, as the base class runs them."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)


def _processor(deleted: list[str] | None = None, *, success: bool = True) -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    proc.on_records_deleted_cascade = AsyncMock(return_value={
        "success": success,
        "reason": None if success else "graph down",
        "deleted_records": [{"record_id": record_id, "name": record_id} for record_id in deleted or []],
    })
    return proc


class TestOnSqlRecordsDeleted:
    async def test_returns_every_record_the_cascade_removed(self) -> None:
        proc = _processor(deleted=["table-1", "col-1", "col-2"])

        removed = await proc.on_sql_records_deleted(["table-1"], "conn-1")

        assert removed == ["table-1", "col-1", "col-2"]
        proc.on_records_deleted_cascade.assert_awaited_once_with(["table-1"], "conn-1", include_trashed_roots=True)

    async def test_columns_of_a_table_are_deleted_the_same_way(self) -> None:
        proc = _processor(deleted=["col-1"])

        assert await proc.on_sql_records_deleted(["col-1"], "conn-1", table_record_id="table-1") == ["col-1"]

    async def test_no_ids_is_a_no_op(self) -> None:
        proc = _processor()

        assert await proc.on_sql_records_deleted([], "conn-1") == []
        proc.on_records_deleted_cascade.assert_not_awaited()

    async def test_a_failed_delete_raises_so_the_sync_retries_it(self) -> None:
        proc = _processor(success=False)

        with pytest.raises(RuntimeError, match="graph down"):
            await proc.on_sql_records_deleted(["table-1"], "conn-1")

    async def test_sends_no_event_of_its_own(self) -> None:
        proc = _processor(deleted=["table-1"])

        await proc.on_sql_records_deleted(["table-1"], "conn-1")

        proc.messaging_producer.send_event.assert_not_awaited()


class TestNoOpHooks:
    async def test_no_tables_need_relating_again(self) -> None:
        proc = _processor()

        assert await proc.sql_tables_to_relate_again(["users"], column_ids=["users.id"]) == set()

    @pytest.mark.parametrize(("hook", "args"), [
        ("on_sql_table_renamed", ("rec-1",)),
        ("on_sql_records_renamed", (["t1"], "conn-1")),
        ("on_sql_record_groups_deleted", (["g1"], "conn-1")),
    ])
    async def test_renames_and_group_deletes_need_nothing_more(self, hook, args) -> None:
        proc = _processor()

        assert await getattr(proc, hook)(*args) is None
        proc.messaging_producer.send_event.assert_not_awaited()
        proc.on_records_deleted_cascade.assert_not_awaited()


class TestGraphReadsForSqlConnectors:
    async def test_typed_records_come_from_the_graph(self) -> None:
        proc = _processor()
        graph = proc.data_store_provider.graph_provider
        graph.get_typed_records_batch = AsyncMock(return_value={"c1": "column"})

        assert await proc.get_typed_records_by_ids(["c1"]) == {"c1": "column"}
        graph.get_typed_records_batch.assert_awaited_once_with(["c1"], raise_on_error=False)

    async def test_no_ids_reads_nothing(self) -> None:
        proc = _processor()
        proc.data_store_provider.graph_provider.get_typed_records_batch = AsyncMock()

        assert await proc.get_typed_records_by_ids([]) == {}
        proc.data_store_provider.graph_provider.get_typed_records_batch.assert_not_awaited()

    async def test_incoming_links_move_to_the_new_record(self) -> None:
        proc = _processor()
        graph = proc.data_store_provider.graph_provider
        graph.get_edges_to_node = AsyncMock(return_value=[
            {"_from": "records/src", "_to": "records/stand-in", "relationshipType": "FOREIGN_KEY"},
        ])
        graph.batch_upsert_node_relations = AsyncMock()

        assert await proc.move_incoming_relations("stand-in", "clients") == 1
        graph.batch_upsert_node_relations.assert_awaited_once()
