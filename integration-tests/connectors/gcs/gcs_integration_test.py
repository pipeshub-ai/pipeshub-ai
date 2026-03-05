# pyright: ignore-file

"""
GCS Connector – Full Lifecycle Integration Test (test.pipeshub.com)
===================================================================

Ordered test class exercising the complete GCS connector lifecycle:

  1. Create GCS bucket              →  storage SDK
  2. Upload sample data              →  storage SDK
  3. Create connector instance       →  Pipeshub API
  4. Enable sync (init + test conn)  →  Pipeshub API  →  graph validation
  5. Full sync                       →  graph validation
  6. Incremental sync                →  graph validation
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

from graph_assertions import (  # type: ignore[import-not-found]
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
from pipeshub_client import (  # type: ignore[import-not-found]
    ConnectorInstance,
    PipeshubClient,
    PipeshubClientError,
)
from storage_helpers import GCSStorageHelper  # type: ignore[import-not-found]

logger = logging.getLogger("gcs-lifecycle-test")

_BUCKET_NAME = f"pipeshub-inttest-gcs-{uuid.uuid4().hex[:8]}"
_CONNECTOR_NAME = f"gcs-lifecycle-test-{uuid.uuid4().hex[:8]}"
_state: dict = {}


@pytest.mark.integration
@pytest.mark.gcs
class TestGCSFullLifecycle:
    """Full lifecycle integration test for the GCS connector."""

    @pytest.mark.order(1)
    def test_01_create_bucket(self, gcs_storage: GCSStorageHelper) -> None:
        logger.info("Creating GCS bucket: %s", _BUCKET_NAME)
        gcs_storage.create_bucket(_BUCKET_NAME)
        objects = gcs_storage.list_objects(_BUCKET_NAME)
        assert isinstance(objects, list)
        _state["bucket_created"] = True
        logger.info("✅ Bucket %s created", _BUCKET_NAME)

    @pytest.mark.order(2)
    def test_02_upload_sample_data(
        self, gcs_storage: GCSStorageHelper, sample_data_root: Path
    ) -> None:
        assert _state.get("bucket_created"), "Bucket must be created first"
        count = gcs_storage.upload_directory(_BUCKET_NAME, sample_data_root)
        logger.info("✅ Uploaded %d files to gs://%s", count, _BUCKET_NAME)
        assert count > 0
        _state["uploaded_count"] = count

        objects = gcs_storage.list_objects(_BUCKET_NAME)
        for key in objects:
            if not key.endswith("/"):
                _state["rename_source_key"] = key
                _state["rename_source_name"] = Path(key).name
                break

    @pytest.mark.order(3)
    def test_03_init_connector(self, pipeshub_client: PipeshubClient) -> None:
        sa_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON")
        assert sa_json, "GCS_SERVICE_ACCOUNT_JSON must be set"

        config = {"auth": {"serviceAccountJson": sa_json}}
        instance = pipeshub_client.create_connector(
            connector_type="GCS",
            instance_name=_CONNECTOR_NAME,
            scope="personal",
            config=config,
        )
        assert instance.connector_id
        _state["connector"] = instance
        _state["connector_id"] = instance.connector_id
        logger.info("✅ GCS connector created: %s", instance.connector_id)

    @pytest.mark.order(4)
    def test_04_test_connection(self, pipeshub_client: PipeshubClient) -> None:
        connector_id = _state["connector_id"]
        result = pipeshub_client.toggle_sync(connector_id, enable=True)
        logger.info("✅ Sync enabled (connection tested): %s", result)

    @pytest.mark.order(5)
    def test_05_full_sync_graph_validation(
        self, pipeshub_client: PipeshubClient, neo4j_driver: Driver
    ) -> None:
        connector_id = _state["connector_id"]
        uploaded = _state.get("uploaded_count", 1)

        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= uploaded,
            timeout=180,
            poll_interval=10,
            description="full sync",
        )

        full_count = count_records(neo4j_driver, connector_id)
        _state["full_sync_count"] = full_count

        assert_min_records(neo4j_driver, connector_id, uploaded)
        assert_record_groups_and_edges(
            neo4j_driver, connector_id, min_groups=1, min_record_edges=full_count
        )
        assert_app_record_group_edges(neo4j_driver, connector_id, min_edges=1)
        assert_no_orphan_records(neo4j_driver, connector_id)

        summary = graph_summary(neo4j_driver, connector_id)
        logger.info("📊 Graph summary after full sync: %s", summary)

    @pytest.mark.order(6)
    def test_06_incremental_sync_graph_validation(
        self, pipeshub_client: PipeshubClient, neo4j_driver: Driver
    ) -> None:
        connector_id = _state["connector_id"]
        before = count_records(neo4j_driver, connector_id)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= before,
            timeout=120,
            poll_interval=10,
            description="incremental sync",
        )

        after = count_records(neo4j_driver, connector_id)
        assert after >= before, f"Incremental sync reduced count; before={before}, after={after}"
        logger.info("✅ Incremental sync: before=%d, after=%d", before, after)

    @pytest.mark.order(7)
    def test_07_rename_file_graph_validation(
        self, gcs_storage: GCSStorageHelper, pipeshub_client: PipeshubClient, neo4j_driver: Driver
    ) -> None:
        connector_id = _state["connector_id"]
        old_key = _state.get("rename_source_key")
        if not old_key:
            pytest.skip("No source key for rename test")

        old_name = Path(old_key).name
        new_name = f"renamed-{old_name}"
        parts = old_key.rsplit("/", 1)
        new_key = f"{parts[0]}/{new_name}" if len(parts) == 2 else new_name

        count_before = count_records(neo4j_driver, connector_id)
        gcs_storage.rename_object(_BUCKET_NAME, old_key, new_key)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= count_before,
            timeout=120,
            poll_interval=10,
            description="rename sync",
        )

        assert_record_paths_or_names_contain(neo4j_driver, connector_id, [new_name])
        _state["move_source_key"] = new_key
        _state["move_source_name"] = new_name
        logger.info("✅ Rename validated: %s → %s", old_name, new_name)

    @pytest.mark.order(8)
    def test_08_move_file_graph_validation(
        self, gcs_storage: GCSStorageHelper, pipeshub_client: PipeshubClient, neo4j_driver: Driver
    ) -> None:
        connector_id = _state["connector_id"]
        old_key = _state.get("move_source_key")
        if not old_key:
            pytest.skip("No source key for move test")

        move_name = _state["move_source_name"]
        new_key = f"moved-folder/{move_name}"

        count_before = count_records(neo4j_driver, connector_id)
        gcs_storage.move_object(_BUCKET_NAME, old_key, new_key)

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

        assert_record_paths_or_names_contain(neo4j_driver, connector_id, [move_name])
        groups = count_record_groups(neo4j_driver, connector_id)
        assert groups >= 2, f"Expected ≥2 record groups after move, found {groups}"
        logger.info("✅ Move validated: %s → moved-folder/", move_name)

    @pytest.mark.order(9)
    def test_09_disable_connector(self, pipeshub_client: PipeshubClient) -> None:
        connector_id = _state["connector_id"]
        pipeshub_client.toggle_sync(connector_id, enable=False)
        status = pipeshub_client.get_connector_status(connector_id)
        assert not status.get("isActive")
        logger.info("✅ Connector %s disabled", connector_id)

    @pytest.mark.order(10)
    def test_10_delete_connector_graph_validation(
        self, pipeshub_client: PipeshubClient, neo4j_driver: Driver
    ) -> None:
        connector_id = _state["connector_id"]
        pipeshub_client.delete_connector(connector_id)
        pipeshub_client.wait(10)
        assert_all_records_cleaned(neo4j_driver, connector_id)
        logger.info("✅ Connector deleted, graph cleaned for %s", connector_id)

    @pytest.mark.order(11)
    def test_11_cleanup_bucket(self, gcs_storage: GCSStorageHelper) -> None:
        logger.info("Cleaning up bucket %s", _BUCKET_NAME)
        gcs_storage.delete_bucket(_BUCKET_NAME)
        logger.info("✅ Bucket %s deleted", _BUCKET_NAME)
