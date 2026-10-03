"""Deleting a connector clears its data from all four stores and touches nothing else.

The scenario, built once for the module:

* ``doomed``: a MinIO connector syncing two files, one unique to it and one
  (``shared``) whose bytes also exist elsewhere.
* ``twin``: a second MinIO connector, the same type, created after ``doomed`` has
  finished indexing, syncing its own unique file and an identical copy of
  ``shared``. MD5 dedup is org-wide, so the twin's copy reuses the virtual record
  id ``doomed`` created: the shared embeddings, envelope and storage documents
  started life under the connector being deleted and must outlive it.
* ``other``: a PostgreSQL connector, a different type, over its own schema.

Everything is counted before ``doomed`` is deleted; each test then checks one
store, or one survivor, so a failure names what went wrong.

What a connector delete does today (``event_service.py`` ``_handle_delete``):
it clears the graph, the vector database and the connector's config, then
deletes the connector's whole ``records/{connectorId}`` storage tree in blob
storage and MongoDB. That tree holds the shared envelope too, so it then
re-indexes the twin's copy (``repair_shared_records``), which files a new
envelope under the twin's own folder. The twin is checked against that: its
own content untouched, the shared content whole again in its folder.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper import cleanup_sources as src
from helper import delete_footprint as fp
from helper.mongo_store import records_folder
from helper.run_folder import new_run_folder

logger = logging.getLogger("cleanup-connector-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]

SHARED = b"# Kestrel Ringing Log\n\nEvery ring is logged with its date, site and ringer.\n"


async def _minio_connector(pipeshub_client, graph_provider, storage, label, files, created) -> str:
    folder = new_run_folder()
    created["folders"].append(folder)
    src.upload_files(storage, folder, files)
    connector_id = await src.create_minio_connector(
        pipeshub_client, graph_provider,
        name=f"cleanup-{label}-{uuid.uuid4().hex[:6]}", folder=folder, expected_records=len(files),
    )
    created["connectors"].append(connector_id)
    return connector_id


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def connector_delete(
    pipeshub_client, graph_provider, vector_store, blob_store, mongo_store, test_org_id
) -> AsyncGenerator[dict[str, Any], None]:
    storage = src.minio()
    tag = uuid.uuid4().hex[:6]
    pg = src.postgres(f"cleanup_{tag}")
    created: dict[str, list[str]] = {"connectors": [], "folders": []}
    # Per run, so the only other holder of the shared content is this run's twin.
    shared_body = SHARED + f"\nRun {tag}\n".encode()
    names = {
        "doomed_unique": f"doomed-unique-{tag}.md",
        "doomed_shared": f"doomed-shared-{tag}.md",
        "twin_unique": f"twin-unique-{tag}.md",
        "twin_shared": f"twin-shared-{tag}.md",
    }
    try:
        doomed = await _minio_connector(
            pipeshub_client, graph_provider, storage, "doomed",
            {names["doomed_unique"]: src.unique_text("doomed"), names["doomed_shared"]: shared_body},
            created,
        )
        doomed_records = await fp.wait_for_connector_records(
            graph_provider, doomed, [names["doomed_unique"], names["doomed_shared"]]
        )
        twin = await _minio_connector(
            pipeshub_client, graph_provider, storage, "twin",
            {names["twin_unique"]: src.unique_text("twin"), names["twin_shared"]: shared_body},
            created,
        )
        twin_records = await fp.wait_for_connector_records(
            graph_provider, twin, [names["twin_unique"], names["twin_shared"]]
        )

        pg.ensure_schema()
        pg.create_table_with_rows("notes", [("Perch height", "Kestrels favour posts two metres up.")])
        other = await src.create_postgres_connector(
            pipeshub_client, graph_provider,
            name=f"cleanup-other-{tag}", schema=pg.schema, expected_records=1,
        )
        created["connectors"].append(other)
        other_names = await graph_provider.fetch_record_names(other)
        other_records = await fp.wait_for_connector_records(graph_provider, other, other_names)

        shared_vrid = doomed_records[names["doomed_shared"]].virtual_record_id
        assert shared_vrid and twin_records[names["twin_shared"]].virtual_record_id == shared_vrid, (
            "The twin's copy of the shared file did not reuse the doomed connector's "
            f"virtual record id ({twin_records[names['twin_shared']].virtual_record_id} vs "
            f"{shared_vrid}), so this scenario cannot test shared content surviving."
        )
        # One envelope per virtual id, filed under the doomed copy until the delete removes it.
        shared_path, vendor = await fp.envelope_location(
            mongo_store, test_org_id, shared_vrid, within=records_folder(test_org_id, doomed)
        )

        doomed_graph = await fp.graph_footprint_of_connector(graph_provider, doomed)
        doomed_unique = [doomed_records[names["doomed_unique"]]]
        doomed_before = await fp.capture_when_stable(
            doomed_graph, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=doomed_unique, within=records_folder(test_org_id, doomed),
            connector_id=doomed, vendor=vendor,
        )
        fp.assert_every_store_holds_it(doomed_before)

        # The twin's copy of the shared content is re-indexed by the delete, so it is
        # left out of the twin's snapshot (with the twin's connector-wide point count,
        # which includes it) and counted on its own.
        holder = twin_records[names["twin_shared"]]
        survivors = {}
        for label, cid, records, excluded, count_points in (
            ("twin", twin, [twin_records[names["twin_unique"]]], [holder.record_id], False),
            ("other", other, list(other_records.values()), [], True),
        ):
            graph_fp = await fp.graph_footprint_of_connector(graph_provider, cid, excluding_records=excluded)
            survivors[label] = {
                "connector_id": cid if count_points else None,
                "records": records,
                "before": await fp.capture_when_stable(
                    graph_fp, vector_store, blob_store, mongo_store,
                    org_id=test_org_id, records=records, within=records_folder(test_org_id, cid),
                    connector_id=cid if count_points else None, vendor=vendor,
                ),
            }
        shared_before = await fp.capture_when_stable(
            await fp.graph_footprint_of_records(graph_provider, [holder.record_id]),
            vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[holder], vendor=vendor,
            envelope_paths={shared_vrid: shared_path},
        )
        fp.assert_shared_envelope_counted(shared_before, shared_vrid)

        src.delete_connector(pipeshub_client, doomed)
        created["connectors"].remove(doomed)
        await src.wait_for_connector_delete(graph_provider, vector_store, doomed, doomed_graph)

        yield {
            "connector_id": doomed,
            "unique": doomed_unique,
            "before": doomed_before,
            "shared_vrid": shared_vrid,
            "survivors": survivors,
            "twin_id": twin,
            "holder": holder,
            "shared_before": shared_before,
            "vendor": vendor,
        }
    finally:
        for cid in created["connectors"]:
            try:
                src.delete_connector(pipeshub_client, cid)
            except Exception as exc:  # noqa: BLE001 - keep cleaning up the rest
                logger.warning("Could not delete connector %s: %s", cid, exc)
        for folder in created["folders"]:
            try:
                storage.clear_objects(src.MINIO_BUCKET, folder)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not clear %s: %s", folder, exc)
        try:
            pg.drop_schema()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not drop schema %s: %s", pg.schema, exc)


class TestDeletingAConnector:
    """The test list's 'Deleting connector' scenario, store by store."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_no_node_or_edge_of_it_is_left_in_the_graph(
        self, connector_delete, graph_provider
    ) -> None:
        await fp.assert_graph_gone(graph_provider, connector_delete["before"].graph)
        await graph_provider.assert_connector_graph_fully_cleaned(
            connector_delete["connector_id"], timeout=60
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_embeddings_are_removed(self, connector_delete, vector_store) -> None:
        before = connector_delete["before"]
        await fp.assert_embeddings_gone(
            vector_store, before, [r.virtual_record_id for r in connector_delete["unique"]]
        )
        await vector_store.assert_connector_embeddings_gone(connector_delete["connector_id"], timeout=60)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_files_are_removed_from_blob_storage(
        self, connector_delete, blob_store
    ) -> None:
        before = connector_delete["before"]
        await fp.assert_blobs_gone(
            blob_store, before, fp.blob_keys_for(before, connector_delete["unique"]),
            vendor=connector_delete["vendor"],
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_storage_documents_are_removed_from_mongodb(
        self, connector_delete, mongo_store
    ) -> None:
        await fp.assert_documents_gone(
            mongo_store, connector_delete["before"],
            fp.document_keys_for(connector_delete["before"], connector_delete["unique"]),
        )


class TestWhatSurvivesAConnectorDelete:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_another_connector_of_the_same_type_is_untouched(
        self, connector_delete, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        """Everything of it except its copy of the shared content, which the next test checks."""
        twin = connector_delete["survivors"]["twin"]
        await fp.assert_unchanged(
            twin["before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=twin["records"], connector_id=twin["connector_id"],
            vendor=connector_delete["vendor"], what="the other MinIO connector",
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_content_it_shared_is_rebuilt_under_the_other_connector(
        self, connector_delete, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        await fp.assert_rebuilt(
            connector_delete["shared_before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, connector_id=connector_delete["twin_id"],
            holder=connector_delete["holder"], vendor=connector_delete["vendor"],
            what="the content shared with the other MinIO connector",
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_connector_of_another_type_is_untouched(
        self, connector_delete, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        other = connector_delete["survivors"]["other"]
        await fp.assert_unchanged(
            other["before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=other["records"], connector_id=other["connector_id"],
            vendor=connector_delete["vendor"], what="the PostgreSQL connector",
        )
