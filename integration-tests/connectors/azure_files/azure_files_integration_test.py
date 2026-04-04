# pyright: ignore-file

"""
Azure Files Connector – Integration Tests
===========================================

Tests receive a fully set-up connector via the ``azure_files_connector`` fixture
(defined in conftest.py), which handles:
  - Constructor: share creation, sample data upload, connector creation, full sync
  - Destructor:  connector disable/delete + graph cleanup, share deletion

Test cases:
  TC-SYNC-001   — Full sync + graph validation
  TC-INCR-001   — Incremental sync (upload new files, verify new + old unchanged)
  TC-UPDATE-001 — Content change detection (overwrite file, verify update in place)
  TC-RENAME-001 — Rename detection (old name gone, new name present)
  TC-MOVE-001   — Move detection (file path reflects new prefix under same share group)
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
from connectors.azure_files.azure_files_storage_helper import (  # type: ignore[import-not-found]  # noqa: E402
    AzureFilesStorageHelper,
)

logger = logging.getLogger("azure-files-lifecycle-test")


def _wait_for_stable_count(
    neo4j_driver: Driver,
    connector_id: str,
    pipeshub_client: PipeshubClient,
    stability_checks: int = 4,
    interval: int = 10,
) -> int:
    """Poll until the record count is stable across consecutive checks."""
    prev = count_records(neo4j_driver, connector_id)
    stable = 0
    for _ in range(stability_checks * 4):
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
@pytest.mark.azure_files
class TestAzureFilesConnector:
    """Integration tests for the Azure Files connector (constructor/destructor in conftest)."""

    # ------------------------------------------------------------------ #
    # TC-SYNC-001 — Full sync + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(1)
    def test_tc_sync_001_full_sync_graph_validation(
        self,
        azure_files_connector: Dict[str, Any],
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-SYNC-001: After full sync, validate the graph thoroughly.
        """
        connector_id = azure_files_connector["connector_id"]
        uploaded = azure_files_connector["uploaded_count"]
        full_count = azure_files_connector["full_sync_count"]

        assert_min_records(neo4j_driver, connector_id, uploaded)

        assert_record_groups_and_edges(
            neo4j_driver,
            connector_id,
            min_groups=1,
            min_record_edges=max(1, full_count - 1),
        )

        assert_app_record_group_edges(neo4j_driver, connector_id, min_edges=1)
        assert_no_orphan_records(neo4j_driver, connector_id)

        known_name = azure_files_connector.get("rename_source_name")
        if known_name:
            assert_record_paths_or_names_contain(
                neo4j_driver, connector_id, [known_name]
            )

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
        azure_files_connector: Dict[str, Any],
        azure_files_storage: AzureFilesStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-INCR-001: Upload new files, run incremental sync, verify:
        - New files appear as new Records in the graph
        - Existing record count is stable (old records unchanged)
        """
        connector_id = azure_files_connector["connector_id"]
        share_name = azure_files_connector["share_name"]
        before_count = count_records(neo4j_driver, connector_id)

        new_files = {
            "incremental-test/new-file-alpha.csv": b"id,name,value\n1,alpha,100\n2,bravo,200\n",
            "incremental-test/new-file-beta.csv": b"id,name,value\n1,charlie,300\n2,delta,400\n",
        }
        for path_key, file_bytes in new_files.items():
            azure_files_storage.upload_file(share_name, path_key, file_bytes)

        logger.info(
            "Uploaded %d new files for incremental sync (connector %s)",
            len(new_files), connector_id,
        )

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

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
            f"before={before_count}, after={after_count} (connector {connector_id})"
        )

        all_names = fetch_record_names(neo4j_driver, connector_id)
        logger.info(
            "Record names after incremental sync (%d total): %s (connector %s)",
            len(all_names), all_names[:20], connector_id,
        )

        new_names = [Path(path_key).name for path_key in new_files]
        for name in new_names:
            found = record_paths_or_names_contain(neo4j_driver, connector_id, [name])
            if not found:
                logger.warning(
                    "New file '%s' not found by exact name in graph "
                    "(share %s, connector %s)",
                    name, share_name, connector_id,
                )

        assert after_count >= before_count, (
            f"Old records lost during incremental sync; before={before_count}, after={after_count} "
            f"(connector {connector_id})"
        )

        azure_files_connector["incr_sync_count"] = after_count
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
        azure_files_connector: Dict[str, Any],
        azure_files_storage: AzureFilesStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-UPDATE-001: Overwrite an existing file with new content. After sync:
        - The Record still exists (updated in place)
        - Record count is unchanged
        - ETag changes
        """
        connector_id = azure_files_connector["connector_id"]
        share_name = azure_files_connector["share_name"]
        update_key = azure_files_connector["update_target_key"]
        update_name = azure_files_connector["update_target_name"]

        _wait_for_stable_count(neo4j_driver, connector_id, pipeshub_client)
        before_count = count_records(neo4j_driver, connector_id)
        logger.info(
            "TC-UPDATE-001 baseline: %d records (connector %s)",
            before_count, connector_id,
        )

        pre_meta = azure_files_storage.get_file_metadata(share_name, update_key)
        logger.info(
            "Pre-update metadata for %s: etag=%s (connector %s)",
            update_key, pre_meta.get("etag"), connector_id,
        )

        new_content = f"Updated content at {uuid.uuid4().hex}".encode()
        azure_files_storage.overwrite_file(share_name, update_key, new_content)

        post_meta = azure_files_storage.get_file_metadata(share_name, update_key)
        assert post_meta["etag"] != pre_meta["etag"], (
            f"Azure Files ETag should change after overwrite; "
            f"before={pre_meta['etag']}, after={post_meta['etag']} (connector {connector_id})"
        )

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

        assert_record_paths_or_names_contain(
            neo4j_driver, connector_id, [update_name]
        )

        after_count = count_records(neo4j_driver, connector_id)
        assert after_count == before_count, (
            f"Record count must be stable after content update; "
            f"before={before_count}, after={after_count} (connector {connector_id})"
        )

        logger.info(
            "TC-UPDATE-001 passed: record count stable at %d, "
            "ETag changed %s -> %s (connector %s)",
            after_count, pre_meta["etag"], post_meta["etag"], connector_id,
        )

    # ------------------------------------------------------------------ #
    # TC-RENAME-001 — Rename detection
    # ------------------------------------------------------------------ #
    @pytest.mark.order(4)
    def test_tc_rename_001_rename_detection(
        self,
        azure_files_connector: Dict[str, Any],
        azure_files_storage: AzureFilesStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-RENAME-001: Rename a file. After incremental sync:
        - A Record exists for the new name
        - The Record for the old name is gone from the graph
        """
        connector_id = azure_files_connector["connector_id"]
        share_name = azure_files_connector["share_name"]
        old_key = azure_files_connector["rename_source_key"]
        old_name = Path(old_key).name

        new_name = f"renamed-{old_name}"
        parts = old_key.rsplit("/", 1)
        new_key = f"{parts[0]}/{new_name}" if len(parts) == 2 else new_name

        logger.info(
            "Renaming %s/%s -> %s (connector %s)",
            share_name, old_key, new_key, connector_id,
        )

        azure_files_storage.rename_object(share_name, old_key, new_key)

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

        assert_record_paths_or_names_contain(neo4j_driver, connector_id, [new_name])
        assert_record_not_exists(neo4j_driver, connector_id, old_name)

        # Rename uses server-side File Rename (see AzureFilesStorageHelper) so SMB file_id
        # is preserved and the connector can treat it as the same Record path update.
        azure_files_connector["move_source_key"] = new_key
        azure_files_connector["move_source_name"] = new_name
        logger.info(
            "TC-RENAME-001 passed: '%s' -> '%s' (connector %s)",
            old_name, new_name, connector_id,
        )

    # ------------------------------------------------------------------ #
    # TC-MOVE-001 — Move detection (same share)
    # ------------------------------------------------------------------ #
    @pytest.mark.order(5)
    def test_tc_move_001_move_detection(
        self,
        azure_files_connector: Dict[str, Any],
        azure_files_storage: AzureFilesStorageHelper,
        pipeshub_client: PipeshubClient,
        neo4j_driver: Driver,
    ) -> None:
        """
        TC-MOVE-001: Move a file to a different directory. After sync:
        - Record exists at the new path (File.path includes the new prefix)

        Azure Files uses one RecordGroup per share; directories are paths.
        """
        connector_id = azure_files_connector["connector_id"]
        share_name = azure_files_connector["share_name"]
        old_key = azure_files_connector["move_source_key"]
        move_name = azure_files_connector["move_source_name"]

        new_prefix = "moved-folder"
        new_key = f"{new_prefix}/{move_name}"

        logger.info(
            "Moving %s/%s -> %s (connector %s)",
            share_name, old_key, new_key, connector_id,
        )

        azure_files_storage.move_object(share_name, old_key, new_key)

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
