# pyright: ignore-file

"""
S3 Connector – Full Lifecycle Integration Test (test.pipeshub.com)
==================================================================

Ordered test class exercising the complete S3 connector lifecycle:

  1. Create S3 bucket                →  storage SDK
  2. Upload sample data              →  storage SDK
  3. Create connector instance       →  Pipeshub API
  4. Enable sync (init + test conn)  →  Pipeshub API  →  graph validation
  5. Full sync                       →  graph validation (records, groups, edges)
  6. Incremental sync                →  graph validation (count ≥ previous)
  7. Rename file                     →  storage SDK + sync →  graph validation
  8. Move file                       →  storage SDK + sync →  graph validation
  9. Disable connector               →  Pipeshub API
  10. Delete connector               →  Pipeshub API  →  graph validation (zero)
  11. Cleanup bucket                 →  storage SDK
"""

import logging
import os
import sys
import uuid
from pathlib import Path

import pytest
from neo4j import Driver

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from graph_assertions import (  # type: ignore[import-not-found]  # noqa: E402
    assert_all_records_cleaned,
    assert_app_record_group_edges,
    assert_min_records,
    assert_no_orphan_records,
    assert_record_groups_and_edges,
    assert_record_paths_or_names_contain,
    count_records,
    count_record_groups,
    graph_summary,
)
from pipeshub_client import (  # type: ignore[import-not-found]  # noqa: E402
    PipeshubClient,
)
from storage_helpers import S3StorageHelper  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("s3-lifecycle-test")

# Unique bucket name to avoid clashes across test runs
_BUCKET_NAME = f"pipeshub-inttest-s3-{uuid.uuid4().hex[:8]}"
_CONNECTOR_NAME = f"s3-lifecycle-test-{uuid.uuid4().hex[:8]}"

# Shared state across ordered tests
_state: dict = {}


@pytest.mark.integration
@pytest.mark.s3
class TestS3FullLifecycle:
    """Full lifecycle integration test for the S3 connector."""

    # ------------------------------------------------------------------ #
    # 1. Create bucket
    # ------------------------------------------------------------------ #
    @pytest.mark.order(1)
    def test_01_create_bucket(self, s3_storage: S3StorageHelper) -> None:
        """Create a dedicated S3 bucket for this test run."""
        logger.info("Creating S3 bucket: %s", _BUCKET_NAME)
        s3_storage.create_bucket(_BUCKET_NAME)
        # Verify bucket exists by listing it (head_bucket equivalent)
        objects = s3_storage.list_objects(_BUCKET_NAME)
        assert isinstance(objects, list)  # bucket is accessible
        _state["bucket_created"] = True
        logger.info("✅ Bucket %s created", _BUCKET_NAME)

    # ------------------------------------------------------------------ #
    # 2. Upload sample data
    # ------------------------------------------------------------------ #
    @pytest.mark.order(2)
    def test_02_upload_sample_data(
        self, s3_storage: S3StorageHelper, sample_data_root: Path
    ) -> None:
        """Upload sample data files from the GitHub repo into the bucket."""
        assert _state.get("bucket_created"), "Bucket must be created first"

        count = s3_storage.upload_directory(_BUCKET_NAME, sample_data_root)
        logger.info("✅ Uploaded %d files to s3://%s", count, _BUCKET_NAME)
        assert count > 0, "Expected at least 1 file in sample data"
        _state["uploaded_count"] = count

        # Pick a file for rename/move tests later
        objects = s3_storage.list_objects(_BUCKET_NAME)
        # Find a non-directory key
        for key in objects:
            if not key.endswith("/"):
                _state["rename_source_key"] = key
                _state["rename_source_name"] = Path(key).name
                break

    # ------------------------------------------------------------------ #
    # 3. Create connector instance
    # ------------------------------------------------------------------ #
    @pytest.mark.order(3)
    def test_03_init_connector(self, pipeshub_client: PipeshubClient) -> None:
        """Create an S3 connector instance on test.pipeshub.com."""
        access_key = os.getenv("S3_ACCESS_KEY")
        secret_key = os.getenv("S3_SECRET_KEY")
        assert access_key and secret_key, "S3 credentials must be set"

        config = {
            "auth": {
                "accessKey": access_key,
                "secretKey": secret_key,
            },
        }

        instance = pipeshub_client.create_connector(
            connector_type="S3",
            instance_name=_CONNECTOR_NAME,
            scope="personal",
            config=config,
        )
        assert instance.connector_id, "Connector must have a valid ID"
        _state["connector"] = instance
        _state["connector_id"] = instance.connector_id
        logger.info("✅ S3 connector created: %s", instance.connector_id)

    # ------------------------------------------------------------------ #
    # 4. Test connection (enable sync triggers init + test_connection)
    # ------------------------------------------------------------------ #
    @pytest.mark.order(4)
    def test_04_test_connection(self, pipeshub_client: PipeshubClient) -> None:
        """Enable sync which internally calls init() + test_connection_and_access()."""
        connector_id = _state["connector_id"]
        result = pipeshub_client.toggle_sync(connector_id, enable=True)
        logger.info("✅ Sync enabled (connection tested): %s", result)

    # ------------------------------------------------------------------ #
    # 5. Full sync + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(5)
    def test_05_full_sync_graph_validation(
        self,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """Wait for full sync to complete and validate graph state."""
        connector_id = _state["connector_id"]
        uploaded = _state.get("uploaded_count", 1)

        # Poll until records appear in Neo4j
        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= uploaded,
            timeout=180,
            poll_interval=10,
            description="full sync",
        )

        full_count = count_records(neo4j_driver, connector_id)
        logger.info("Full sync produced %d records", full_count)
        _state["full_sync_count"] = full_count

        # Validate record count
        assert_min_records(neo4j_driver, connector_id, uploaded)

        # Validate record groups (at least the bucket itself)
        assert_record_groups_and_edges(
            neo4j_driver,
            connector_id,
            min_groups=1,
            min_record_edges=full_count,
        )

        # Validate app → record-group wiring
        assert_app_record_group_edges(neo4j_driver, connector_id, min_edges=1)

        # Validate no orphan records
        assert_no_orphan_records(neo4j_driver, connector_id)

        # Log full graph summary
        summary = graph_summary(neo4j_driver, connector_id)
        logger.info("📊 Graph summary after full sync: %s", summary)

    # ------------------------------------------------------------------ #
    # 6. Incremental sync + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(6)
    def test_06_incremental_sync_graph_validation(
        self,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """Toggle sync off/on to trigger incremental sync; count must not decrease."""
        connector_id = _state["connector_id"]
        before = count_records(neo4j_driver, connector_id)

        # Toggle off then on
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Wait for sync
        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= before,
            timeout=120,
            poll_interval=10,
            description="incremental sync",
        )

        after = count_records(neo4j_driver, connector_id)
        assert after >= before, (
            f"Incremental sync should not reduce record count; before={before}, after={after}"
        )
        logger.info("✅ Incremental sync: before=%d, after=%d", before, after)

    # ------------------------------------------------------------------ #
    # 7. Rename file + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(7)
    def test_07_rename_file_graph_validation(
        self,
        s3_storage: S3StorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """Rename a file in S3, sync, and validate graph reflects the change."""
        connector_id = _state["connector_id"]
        old_key = _state.get("rename_source_key")
        if not old_key:
            pytest.skip("No source key found for rename test")

        old_name = Path(old_key).name
        new_name = f"renamed-{old_name}"
        new_key = old_key.rsplit("/", 1)
        if len(new_key) == 2:
            new_key = f"{new_key[0]}/{new_name}"
        else:
            new_key = new_name

        count_before = count_records(neo4j_driver, connector_id)
        logger.info("Renaming s3://%s/%s → %s", _BUCKET_NAME, old_key, new_key)

        # Rename in S3
        s3_storage.rename_object(_BUCKET_NAME, old_key, new_key)

        # Trigger incremental sync
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Wait for sync to process
        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= count_before,
            timeout=120,
            poll_interval=10,
            description="rename sync",
        )

        # Validate: new name appears in either path or name
        assert_record_paths_or_names_contain(neo4j_driver, connector_id, [new_name])

        # Store for move test
        _state["move_source_key"] = new_key
        _state["move_source_name"] = new_name
        logger.info("✅ Rename validated: %s → %s", old_name, new_name)

    # ------------------------------------------------------------------ #
    # 8. Move file + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(8)
    def test_08_move_file_graph_validation(
        self,
        s3_storage: S3StorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """Move a file to a different prefix, sync, and validate graph."""
        connector_id = _state["connector_id"]
        old_key = _state.get("move_source_key")
        if not old_key:
            pytest.skip("No source key found for move test")

        move_name = _state["move_source_name"]
        new_key = f"moved-folder/{move_name}"

        count_before = count_records(neo4j_driver, connector_id)
        logger.info("Moving s3://%s/%s → %s", _BUCKET_NAME, old_key, new_key)

        # Move in S3
        s3_storage.move_object(_BUCKET_NAME, old_key, new_key)

        # Trigger incremental sync
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= count_before,
            timeout=120,
            poll_interval=10,
            description="move sync",
        )

        # Validate: file still exists under new path
        assert_record_paths_or_names_contain(neo4j_driver, connector_id, [move_name])

        # Validate record groups include the new folder
        groups = count_record_groups(neo4j_driver, connector_id)
        assert groups >= 2, f"Expected at least 2 record groups after move, found {groups}"

        logger.info("✅ Move validated: %s → moved-folder/", move_name)

    # ------------------------------------------------------------------ #
    # 9. Disable connector
    # ------------------------------------------------------------------ #
    @pytest.mark.order(9)
    def test_09_disable_connector(self, pipeshub_client: PipeshubClient) -> None:
        """Disable the connector by toggling sync off."""
        connector_id = _state["connector_id"]
        pipeshub_client.toggle_sync(connector_id, enable=False)

        status = pipeshub_client.get_connector_status(connector_id)
        assert not status.get("isActive"), "Connector should be inactive after disable"
        logger.info("✅ Connector %s disabled", connector_id)

    # ------------------------------------------------------------------ #
    # 10. Delete connector + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(10)
    def test_10_delete_connector_graph_validation(
        self,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """Delete the connector and assert all graph data is cleaned up."""
        connector_id = _state["connector_id"]
        logger.info("Deleting connector %s", connector_id)

        pipeshub_client.delete_connector(connector_id)

        # Allow time for async cleanup
        pipeshub_client.wait(10)

        # Validate graph is clean
        assert_all_records_cleaned(neo4j_driver, connector_id)
        logger.info("✅ Connector deleted, graph cleaned for %s", connector_id)

    # ------------------------------------------------------------------ #
    # 11. Cleanup bucket
    # ------------------------------------------------------------------ #
    @pytest.mark.order(11)
    def test_11_cleanup_bucket(self, s3_storage: S3StorageHelper) -> None:
        """Delete the test bucket and all its contents."""
        logger.info("Cleaning up bucket %s", _BUCKET_NAME)
        s3_storage.delete_bucket(_BUCKET_NAME)
        logger.info("✅ Bucket %s deleted", _BUCKET_NAME)
