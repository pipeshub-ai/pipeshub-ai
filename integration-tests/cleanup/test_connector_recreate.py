"""Create a connector, delete it, create the same connector again: the second is clean.

"The same connector" is the same type, the same instance name and the same
source folder. The second instance has to sync the same records as the first,
index every one of them itself, and share no node with the deleted one. Data the
first left behind could otherwise be picked up by MD5 dedup and leave the new
records "indexed" with nothing of their own.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper import cleanup_sources as src
from helper import delete_footprint as fp
from helper.run_folder import new_run_folder

logger = logging.getLogger("cleanup-connector-recreate")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]


async def _snapshot(graph_provider, vector_store, connector_id: str, files: list[str]) -> dict[str, Any]:
    records = await fp.wait_for_connector_records(graph_provider, connector_id, files)
    return {
        "connector_id": connector_id,
        "records": records,
        "graph": await fp.graph_footprint_of_connector(graph_provider, connector_id),
        "summary": await graph_provider.graph_summary(connector_id),
        "record_count": await graph_provider.count_records(connector_id),
        "points": await vector_store.count_for_connector(connector_id),
        "points_per_file": {
            name: await vector_store.count_content_chunks(r.virtual_record_id)
            for name, r in records.items()
            if r.virtual_record_id
        },
    }


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def recreated(pipeshub_client, graph_provider, vector_store) -> AsyncGenerator[dict[str, Any], None]:
    storage = src.minio()
    folder = new_run_folder()
    tag = uuid.uuid4().hex[:6]
    files = {
        f"recreate-a-{tag}.md": src.unique_text("recreate a"),
        f"recreate-b-{tag}.md": src.unique_text("recreate b"),
    }
    src.upload_files(storage, folder, files)
    name = f"cleanup-recreate-{tag}"
    live: list[str] = []
    try:
        first_id = await src.create_minio_connector(
            pipeshub_client, graph_provider, name=name, folder=folder, expected_records=len(files)
        )
        live.append(first_id)
        first = await _snapshot(graph_provider, vector_store, first_id, list(files))

        src.delete_connector(pipeshub_client, first_id)
        live.remove(first_id)
        await src.wait_for_connector_delete(graph_provider, vector_store, first_id, first["graph"])

        second_id = await src.create_minio_connector(
            pipeshub_client, graph_provider, name=name, folder=folder, expected_records=len(files)
        )
        live.append(second_id)
        second = await _snapshot(graph_provider, vector_store, second_id, list(files))
        yield {"first": first, "second": second}
    finally:
        for cid in live:
            try:
                src.delete_connector(pipeshub_client, cid)
            except Exception as exc:  # noqa: BLE001 - keep cleaning up
                logger.warning("Could not delete connector %s: %s", cid, exc)
        try:
            storage.clear_objects(src.MINIO_BUCKET, folder)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not clear %s: %s", folder, exc)


class TestCreateDeleteCreate:
    """The test list's 'Create connector -> Delete connector -> Create same connector' line."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_first_instance_left_nothing_in_the_graph(self, recreated, graph_provider) -> None:
        first, second = recreated["first"], recreated["second"]
        await fp.assert_graph_gone(graph_provider, first["graph"], timeout=60)
        shared = set(first["graph"].handles) & set(second["graph"].handles)
        assert not shared, f"The new connector reuses nodes of the deleted one: {sorted(shared)[:5]}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_first_instance_left_no_embeddings(self, recreated, vector_store) -> None:
        await vector_store.assert_connector_embeddings_gone(recreated["first"]["connector_id"], timeout=60)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_second_sync_builds_the_same_graph(self, recreated) -> None:
        """Same records, no duplicates, the same nodes and edges around them."""
        first, second = recreated["first"], recreated["second"]
        assert second["record_count"] == first["record_count"], (
            f"Record count: first {first['record_count']}, second {second['record_count']}"
        )
        assert second["summary"] == first["summary"], (
            f"Graph shape: first {first['summary']}, second {second['summary']}"
        )
        assert (len(second["graph"].handles), second["graph"].edges) == (
            len(first["graph"].handles), first["graph"].edges
        ), f"Nodes and edges: first {first['graph']}, second {second['graph']}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_second_instance_indexes_every_record_itself(self, recreated) -> None:
        first, second = recreated["first"], recreated["second"]
        for label, run in (("first", first), ("second", second)):
            not_done = {n: r.status for n, r in run["records"].items() if r.status != "COMPLETED"}
            assert not not_done, f"{label} instance: records not indexed: {not_done}"
        assert second["points"] == first["points"] > 0, (
            f"Embeddings carrying the connector: first {first['points']}, second "
            f"{second['points']}. Fewer means records were matched to leftovers of the "
            "deleted instance instead of being indexed."
        )
        for label, run in (("first", first), ("second", second)):
            empty = sorted(name for name in run["records"] if not run["points_per_file"].get(name))
            assert not empty, f"{label} instance: files with no chunks: {empty}"
        assert second["points_per_file"] == first["points_per_file"], (
            f"Chunks per file: first {first['points_per_file']}, second {second['points_per_file']}"
        )
