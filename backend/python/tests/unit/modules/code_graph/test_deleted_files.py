"""The names a deleted code file defined, kept for the incremental edge build."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph import deleted_files
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID
from .sync_point_fakes import FakeSyncPoints

pytestmark = pytest.mark.asyncio


def _code_record(**over) -> dict:
    return {"_key": "rec-util", "orgId": ORG_ID, "connectorId": "conn1",
            "recordGroupId": GROUP_ID, "recordType": "CODE_FILE",
            "connectorName": "GITHUB", **over}


async def test_defined_names_are_its_definitions_and_module_references(graph, index_file) -> None:
    await index_file("rec-util", "pkg/util.py",
                     b"class Parser:\n    def run(self):\n        return 1\n\n\ndef helper():\n    return 2\n",
                     "python")

    names = await deleted_files.defined_names(graph, ORG_ID, "rec-util")

    assert {"Parser", "run", "helper"} <= names
    assert {"module:pkg/util", "module:util"} <= names


async def test_remember_keeps_one_sync_point_per_deletion() -> None:
    store = FakeSyncPoints()
    store.get_file_paths_for_records = AsyncMock(return_value={})
    blocks = AsyncMock(return_value=[{"name": "helper", "kind": "function"},
                                     {"name": "util.py", "kind": "file_summary"}])
    real_filters = store.get_nodes_by_filters

    async def get_nodes_by_filters(collection, filters, **kw) -> list[dict]:
        if collection == CollectionNames.BLOCKS.value:
            return await blocks(collection, filters, **kw)
        return await real_filters(collection, filters, **kw)

    store.get_nodes_by_filters = get_nodes_by_filters

    await deleted_files.remember(store, [_code_record(), _code_record()], MagicMock())

    assert len(store.points) == 2, "two deletes of one record never overwrite each other"
    point = next(iter(store.points.values()))
    assert point["deletedNames"] == ["helper"]
    assert point["recordGroupId"] == GROUP_ID and point["connectorId"] == "conn1"
    assert await deleted_files.was_remembered(store, ORG_ID, "rec-util")
    store.records.add("rec-util")
    assert await deleted_files.pending(store, ORG_ID, GROUP_ID) == ([], set()), \
        "not served until the record is gone"
    store.records.discard("rec-util")
    pending = await deleted_files.pending(store, ORG_ID, GROUP_ID)
    assert pending.names == {"helper"} and len(pending.keys) == 2

    await deleted_files.forget(store, pending.keys, MagicMock())
    assert not await deleted_files.was_remembered(store, ORG_ID, "rec-util")


async def test_forget_keeps_going_when_a_removal_fails() -> None:
    provider = MagicMock()
    provider.remove_sync_point = AsyncMock(side_effect=[RuntimeError("store down"), None])
    log = MagicMock()

    await deleted_files.forget(provider, ["k1", "k2"], log)

    assert [c.args[0] for c in provider.remove_sync_point.await_args_list] == ["k1", "k2"]
    log.warning.assert_called_once()


async def test_remember_skips_records_that_are_not_code_files() -> None:
    provider = MagicMock()
    provider.upsert_sync_point = AsyncMock()
    await deleted_files.remember(provider, [
        _code_record(recordType="FILE"),
        _code_record(connectorName="SLACK"),
        _code_record(recordGroupId=None),
        None,
    ], MagicMock())
    provider.upsert_sync_point.assert_not_awaited()


async def test_remember_never_raises() -> None:
    """The delete goes ahead; its event then finds nothing kept and asks for a full build."""
    provider = MagicMock()
    provider.get_nodes_by_filters = AsyncMock(side_effect=RuntimeError("graph down"))
    provider.upsert_sync_point = AsyncMock()
    log = MagicMock()
    await deleted_files.remember(provider, [_code_record()], log)
    provider.upsert_sync_point.assert_not_awaited()
    log.warning.assert_called_once()


async def test_an_incremental_build_on_deleted_names_rebinds_the_callers(graph, index_file) -> None:
    """The caller of a deleted definition is re-resolved although it never
    changed: its call binds to the other definition of the name."""
    await index_file("rec-a", "pkg/a.py", b"def shared():\n    return 1\n", "python")
    await index_file("rec-b", "pkg/b.py", b"def shared():\n    return 2\n", "python")
    await index_file("rec-use", "pkg/use.py",
                     b"from pkg.a import shared\n\n\ndef main():\n    shared()\n", "python")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID,
                                 record_group_id=GROUP_ID, facts_source=graph)
    await index_file("rec-use", "pkg/use.py",
                     b"from pkg.b import shared\n\n\ndef main():\n    shared()\n", "python")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"rec-use"}, facts_source=graph)

    names = await deleted_files.defined_names(graph, ORG_ID, "rec-b")
    await graph.delete_blocks_for_records(["rec-b"])
    graph.code_files.pop("rec-b")
    graph.blobs.pop("rec-b")
    result = await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID,
                                          record_group_id=GROUP_ID, touched_record_ids=set(),
                                          deleted_names=names, facts_source=graph)

    assert result.files_reresolved == 1
    incremental = {(e["_from"], e["_to"], e["relationshipType"]) for e in graph.code_edges()}
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID,
                                 record_group_id=GROUP_ID, facts_source=graph)
    full = {(e["_from"], e["_to"], e["relationshipType"]) for e in graph.code_edges()}
    assert incremental == full
