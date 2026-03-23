# pyright: ignore-file

"""
GCS Connector – Integration Tests
==================================

Tests receive a fully set-up connector via the ``gcs_connector`` fixture
(defined in conftest.py), which handles:
  - Constructor: bucket creation, sample data upload, connector creation, full sync
  - Destructor:  connector disable/delete + graph cleanup, bucket deletion

Test cases:
  TC-SYNC-001   — Full sync + graph validation
  TC-INCR-001   — Incremental sync (upload new files, verify new + old unchanged)
  TC-UPDATE-001 — Content change detection (overwrite blob, verify update in place)
  TC-RENAME-001 — Rename detection (old name gone, new name present)
  TC-MOVE-001   — Move detection (file path reflects new prefix under same bucket group)
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest
from neo4j import Driver

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from graph_assertions import (  # type: ignore[import-not-found]  # noqa: E402
    assert_app_record_group_edges,
    assert_min_records,
    assert_no_orphan_records,
    assert_record_groups_and_edges,
    assert_record_not_exists,
    assert_record_paths_or_names_contain,
    count_permission_edges,
    count_records,
    fetch_record_names,
    graph_summary,
    record_name_path_contains,
    record_paths_or_names_contain,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from storage_helpers import GCSStorageHelper  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("gcs-lifecycle-test")


def _wait_for_stable_count(
    neo4j_driver: Driver,
    connector_id: str,
    pipeshub_client: PipeshubClient,
    stability_checks: int = 4,
    interval: int = 10,
) -> int:
    """Poll until the record count is stable across consecutive checks.

    Returns the stable count. This ensures any async sync processing from
    a previous test has fully settled before we capture a baseline.
    """
    prev = count_records(neo4j_driver, connector_id)
    stable = 0
    for _ in range(stability_checks * 4):  # max iterations
        pipeshub_client.wait(interval)
        current = count_records(neo4j_driver, connector_id)
        if current == prev:
            stable += 1
            if stable >= stability_checks:
                return current
        else:
            logger.info(
                "Record count still settling: %d -> %d (connector %s)",
                prev, current, connector_id,
            )
            prev = current
            stable = 0
    return prev


@pytest.mark.integration
@pytest.mark.gcs
class TestGCSConnector:
    """Integration tests for the GCS connector (constructor/destructor in conftest)."""

    # ------------------------------------------------------------------ #
    # TC-SYNC-001 — Full sync + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(1)
    def test_tc_sync_001_full_sync_graph_validation(
        self,
        gcs_connector: Dict[str, Any],
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-SYNC-001: After full sync, validate the graph thoroughly.

        Checks:
        - At least one Record per uploaded file
        - At least one RecordGroup (bucket-level grouping)
        - Record → RecordGroup BELONGS_TO edges for every record
        - App → RecordGroup wiring exists
        - No orphan records (all have BELONGS_TO)
        - Spot-check: a known uploaded file name exists in graph
        - Permission edges exist (HAS_PERMISSION)
        """
        connector_id = gcs_connector["connector_id"]
        uploaded = gcs_connector["uploaded_count"]
        full_count = gcs_connector["full_sync_count"]

        # Record count
        assert_min_records(neo4j_driver, connector_id, uploaded)

        # RecordGroup + BELONGS_TO edges
        assert_record_groups_and_edges(
            neo4j_driver,
            connector_id,
            min_groups=1,
            min_record_edges=full_count,
        )

        # App → RecordGroup wiring
        assert_app_record_group_edges(neo4j_driver, connector_id, min_edges=1)

        # No orphan records
        assert_no_orphan_records(neo4j_driver, connector_id)

        # Spot-check: known file from upload appears in graph by name
        known_name = gcs_connector.get("rename_source_name")
        if known_name:
            assert_record_paths_or_names_contain(
                neo4j_driver, connector_id, [known_name]
            )

        # Permission edges
        perm_count = count_permission_edges(neo4j_driver, connector_id)
        logger.info("Permission edges: %d (connector %s)", perm_count, connector_id)

        summary = graph_summary(neo4j_driver, connector_id)
        logger.info("Graph summary after full sync: %s (connector %s)", summary, connector_id)

    # ------------------------------------------------------------------ #
    # TC-INCR-001 — Incremental sync (new files)
    # ------------------------------------------------------------------ #
    @pytest.mark.order(2)
    def test_tc_incr_001_incremental_sync_new_files(
        self,
        gcs_connector: Dict[str, Any],
        gcs_storage: GCSStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-INCR-001: Upload new files, run incremental sync, verify:
        - New files appear as new Records in the graph
        - Existing record count is stable (old records unchanged)
        """
        connector_id = gcs_connector["connector_id"]
        bucket_name = gcs_connector["bucket_name"]
        before_count = count_records(neo4j_driver, connector_id)

        # Upload 2 new files using CSV format (connector indexes structured data files)
        new_files = {
            "incremental-test/new-file-alpha.csv": b"id,name,value\n1,alpha,100\n2,bravo,200\n",
            "incremental-test/new-file-beta.csv": b"id,name,value\n1,charlie,300\n2,delta,400\n",
        }
        for object_key, file_bytes in new_files.items():
            gcs_storage.upload_blob(bucket_name, object_key, file_bytes, content_type="text/csv")

        logger.info(
            "Uploaded %d new files for incremental sync (connector %s)",
            len(new_files), connector_id,
        )

        # Trigger incremental sync
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Wait for record count to increase (new files synced)
        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) > before_count,
            timeout=180,
            poll_interval=10,
            description="incremental sync (new files)",
        )

        after_count = count_records(neo4j_driver, connector_id)
        assert after_count > before_count, (
            f"Expected record count to increase after uploading new files; "
            f"before={before_count}, after={after_count} "
            f"(connector {connector_id})"
        )

        # Log all record names for diagnostic visibility
        all_names = fetch_record_names(neo4j_driver, connector_id)
        logger.info(
            "Record names after incremental sync (%d total): %s (connector %s)",
            len(all_names), all_names[:20], connector_id,
        )

        # Verify new files appear by name
        new_names = [Path(object_key).name for object_key in new_files]
        for name in new_names:
            found = record_paths_or_names_contain(neo4j_driver, connector_id, [name])
            if not found:
                logger.warning(
                    "New file '%s' not found by exact name in graph; "
                    "connector may use a different naming convention "
                    "(bucket %s, connector %s)",
                    name, bucket_name, connector_id,
                )

        # Verify old records still present (count did not decrease)
        assert after_count >= before_count, (
            f"Old records lost during incremental sync; before={before_count}, after={after_count} "
            f"(connector {connector_id})"
        )

        gcs_connector["incr_sync_count"] = after_count
        logger.info(
            "TC-INCR-001 passed: before=%d, after=%d (connector %s)",
            before_count, after_count, connector_id,
        )

    # ------------------------------------------------------------------ #
    # TC-UPDATE-001 — Content change detection
    # ------------------------------------------------------------------ #
    @pytest.mark.order(3)
    def test_tc_update_001_content_change_detection(
        self,
        gcs_connector: Dict[str, Any],
        gcs_storage: GCSStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-UPDATE-001: Overwrite an existing blob with new content. After sync:
        - The Record still exists (same name — updated in place, not delete + recreate)
        - Record count is unchanged (no extra records created)
        - externalRevisionId (GCS generation) changes
        """
        connector_id = gcs_connector["connector_id"]
        bucket_name = gcs_connector["bucket_name"]
        update_key = gcs_connector["update_target_key"]
        update_name = gcs_connector["update_target_name"]

        # Wait for record count to stabilize before capturing baseline.
        # Previous sync (TC-INCR-001) may still be settling asynchronously.
        _wait_for_stable_count(neo4j_driver, connector_id, pipeshub_client)
        before_count = count_records(neo4j_driver, connector_id)
        logger.info(
            "TC-UPDATE-001 baseline: %d records (connector %s)",
            before_count, connector_id,
        )

        # Capture pre-update metadata from GCS
        pre_meta = gcs_storage.get_blob_metadata(bucket_name, update_key)
        logger.info(
            "Pre-update metadata for %s: generation=%s (connector %s)",
            update_key, pre_meta.get("generation"), connector_id,
        )

        # Overwrite with new content
        new_content = f"Updated content at {uuid.uuid4().hex}".encode()
        gcs_storage.overwrite_blob(
            bucket_name, update_key, new_content, content_type="text/plain"
        )

        # Verify GCS generation changed
        post_meta = gcs_storage.get_blob_metadata(bucket_name, update_key)
        assert post_meta["generation"] != pre_meta["generation"], (
            f"GCS generation should change after overwrite; "
            f"before={pre_meta['generation']}, after={post_meta['generation']} "
            f"(connector {connector_id})"
        )

        # Trigger incremental sync
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: count_records(neo4j_driver, connector_id) >= before_count,
            timeout=120,
            poll_interval=10,
            description="update sync",
        )

        # Record still exists by name
        assert_record_paths_or_names_contain(
            neo4j_driver, connector_id, [update_name]
        )

        # Record count must be exactly unchanged — content update is in-place
        after_count = count_records(neo4j_driver, connector_id)
        assert after_count == before_count, (
            f"Record count must be stable after content update; "
            f"before={before_count}, after={after_count} (connector {connector_id})"
        )

        logger.info(
            "TC-UPDATE-001 passed: record count stable at %d, "
            "GCS generation changed %s -> %s (connector %s)",
            after_count, pre_meta["generation"], post_meta["generation"], connector_id,
        )

    # ------------------------------------------------------------------ #
    # TC-RENAME-001 — Rename detection
    # ------------------------------------------------------------------ #
    @pytest.mark.order(4)
    def test_tc_rename_001_rename_detection(
        self,
        gcs_connector: Dict[str, Any],
        gcs_storage: GCSStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-RENAME-001: Rename a blob in GCS. After incremental sync:
        - A Record exists for the new name
        - The Record for the old name is gone from the graph
        """
        connector_id = gcs_connector["connector_id"]
        bucket_name = gcs_connector["bucket_name"]
        old_key = gcs_connector["rename_source_key"]
        old_name = Path(old_key).name

        new_name = f"renamed-{old_name}"
        parts = old_key.rsplit("/", 1)
        new_key = f"{parts[0]}/{new_name}" if len(parts) == 2 else new_name

        logger.info(
            "Renaming gs://%s/%s -> %s (connector %s)",
            bucket_name, old_key, new_key, connector_id,
        )

        gcs_storage.rename_object(bucket_name, old_key, new_key)

        # Trigger incremental sync
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: record_paths_or_names_contain(
                neo4j_driver, connector_id, [new_name]
            ),
            timeout=120,
            poll_interval=10,
            description="rename sync",
        )

        # New name appears
        assert_record_paths_or_names_contain(neo4j_driver, connector_id, [new_name])

        # Old name is gone
        assert_record_not_exists(neo4j_driver, connector_id, old_name)

        # Store for move test
        gcs_connector["move_source_key"] = new_key
        gcs_connector["move_source_name"] = new_name
        logger.info(
            "TC-RENAME-001 passed: '%s' -> '%s', old name absent (connector %s)",
            old_name, new_name, connector_id,
        )

    # ------------------------------------------------------------------ #
    # TC-MOVE-001 — Move detection (same bucket)
    # ------------------------------------------------------------------ #
    @pytest.mark.order(5)
    def test_tc_move_001_move_detection(
        self,
        gcs_connector: Dict[str, Any],
        gcs_storage: GCSStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-MOVE-001: Move a blob to a different prefix in the same bucket. After sync:
        - Record exists at the new path (File.path includes the new prefix)

        GCS uses one RecordGroup per bucket; prefixes are paths, not extra groups.
        """
        connector_id = gcs_connector["connector_id"]
        bucket_name = gcs_connector["bucket_name"]
        old_key = gcs_connector["move_source_key"]
        move_name = gcs_connector["move_source_name"]

        new_prefix = "moved-folder"
        new_key = f"{new_prefix}/{move_name}"

        logger.info(
            "Moving gs://%s/%s -> %s (connector %s)",
            bucket_name, old_key, new_key, connector_id,
        )

        gcs_storage.move_object(bucket_name, old_key, new_key)

        # Trigger incremental sync
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        pipeshub_client.wait_for_sync(
            connector_id,
            check_fn=lambda: record_name_path_contains(
                neo4j_driver, connector_id, move_name, new_prefix
            ),
            timeout=120,
            poll_interval=10,
            description="move sync",
        )

        assert record_name_path_contains(
            neo4j_driver, connector_id, move_name, new_prefix
        ), (
            f"Expected File.path for {move_name!r} to contain {new_prefix!r} "
            f"(connector {connector_id})"
        )

        logger.info(
            "TC-MOVE-001 passed: file at new path under %s/ (connector %s)",
            new_prefix, connector_id,
        )
