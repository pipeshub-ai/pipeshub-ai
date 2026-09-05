# pyright: ignore-file

"""
MinIO Connector – Integration Tests
===================================

Tests receive a fully set-up connector via the ``minio_connector`` fixture
(defined in conftest.py), which uploads sample data, creates the connector and
waits for a full sync, then tears both down.

MinIO is the one object-store connector that needs no external account: the
integration compose file runs the server and pre-creates the bucket. That makes
this the same live coverage the S3 and GCS suites have, on a connector that
previously had none, for anyone who can start the stack.

Test cases:
  TC-SYNC-001   — Full sync + graph validation
  TC-INCR-001   — Incremental sync (upload new files, verify new + old unchanged)
  TC-UPDATE-001 — Content change detection (overwrite object, verify update in place)
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import PermissionModel
from connectors.minio.minio_storage_helper import (  # type: ignore[import-not-found]
    MinioStorageHelper,
)
from helper.assertions import assert_permission_model
from helper.graph_provider import GraphProviderProtocol
from helper.storage_incremental import (
    assert_incremental_new_files,
    record_names_from_keys,
    settle_record_baseline,
    sync_until_names_visible,
    unique_incremental_csv_files,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("minio-lifecycle-test")


@pytest.mark.integration
@pytest.mark.minio
@pytest.mark.asyncio(loop_scope="session")
class TestMinioConnector:
    """Lifecycle coverage for the MinIO connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        minio_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: After full sync, validate the graph thoroughly."""
        connector_id = minio_connector["connector_id"]
        uploaded = minio_connector["uploaded_count"]
        full_count = minio_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, uploaded)

        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=1,
            min_record_edges=max(1, full_count - 1),
        )

        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)

        known_name = minio_connector.get("rename_source_name")
        if known_name:
            await graph_provider.assert_record_paths_or_names_contain(
                connector_id, [known_name]
            )

        await assert_permission_model(
            graph_provider,
            connector_id,
            PermissionModel.APP_LEVEL.value,
            context="TC-SYNC-001",
        )
        logger.info(
            "TC-SYNC-001 passed: %d records for connector %s", full_count, connector_id
        )

    @pytest.mark.order(2)
    async def test_tc_incr_001_incremental_sync_new_files(
        self,
        minio_connector: dict[str, Any],
        minio_storage: MinioStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: New objects appear as new records; existing ones are untouched."""
        connector_id = minio_connector["connector_id"]
        bucket_name = minio_connector["bucket_name"]

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        new_files = unique_incremental_csv_files()
        new_names = record_names_from_keys(new_files)
        for object_key, file_bytes in new_files.items():
            minio_storage.upload_object(
                bucket_name, object_key, file_bytes, content_type="text/csv"
            )
        logger.info(
            "Uploaded %d new objects for incremental sync (connector %s): %s",
            len(new_files),
            connector_id,
            new_names,
        )

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, new_names
        )
        await assert_incremental_new_files(
            graph_provider,
            connector_id,
            before_count=before_count,
            after_count=after_count,
            new_names=new_names,
        )

        minio_connector["incr_sync_count"] = after_count
        logger.info(
            "TC-INCR-001 passed: before=%d, after=%d, new=%s (connector %s)",
            before_count,
            after_count,
            new_names,
            connector_id,
        )

    @pytest.mark.order(3)
    async def test_tc_update_001_content_change_detection(
        self,
        minio_connector: dict[str, Any],
        minio_storage: MinioStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: Overwriting an object updates it in place.

        The record count must not move: a re-indexed object that arrives as a
        second record is a duplicate, which is the failure this guards.
        """
        connector_id = minio_connector["connector_id"]
        bucket_name = minio_connector["bucket_name"]
        target_key = minio_connector["rename_source_key"]
        target_name = minio_connector["rename_source_name"]

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        # The connector increments a record's version when it updates one, so
        # comparing it is what separates "re-indexed" from "left alone". A count
        # that holds steady proves only that nothing was duplicated — a
        # connector that ignored the change entirely would also pass that.
        before_record = await graph_provider.get_record_by_name(
            connector_id, target_name
        )
        assert before_record is not None, (
            f"TC-UPDATE-001: {target_name} is not in the graph before the change"
        )
        before_version = before_record.get("version")

        minio_storage.overwrite_object(
            bucket_name,
            target_key,
            b"col_a,col_b\nupdated,content\n",
            content_type="text/csv",
        )
        logger.info("Overwrote %s in %s", target_key, bucket_name)

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [target_name]
        )

        assert after_count == before_count, (
            f"TC-UPDATE-001: record count changed from {before_count} to "
            f"{after_count} after overwriting one object. An update must not "
            "create a second record for the same object."
        )
        after_record = await graph_provider.get_record_by_name(
            connector_id, target_name
        )
        assert after_record is not None, (
            f"TC-UPDATE-001: {target_name} disappeared from the graph after the change"
        )
        assert after_record.get("version") != before_version, (
            f"TC-UPDATE-001: version stayed at {before_version} after the content "
            "changed, so the record was never re-indexed. The count assertion "
            "above would have passed regardless."
        )
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info(
            "TC-UPDATE-001 passed: %s updated in place, count stable at %d",
            target_name,
            after_count,
        )

