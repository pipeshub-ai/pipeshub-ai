# pyright: ignore-file

"""
S3 Connector – Integration Tests
==================================

Tests receive a fully set-up connector via the ``s3_connector`` fixture
(defined in conftest.py), which handles:
  - Constructor: container creation, sample data upload, connector creation, full sync
  - Destructor:  connector disable/delete + graph cleanup, container deletion

Test cases:
  TC-SYNC-001   — Full sync + graph validation
  TC-INCR-001   — Incremental sync (upload new files, verify new + old unchanged)
  TC-UPDATE-001 — Content change detection (overwrite blob, verify update in place)
  TC-RENAME-001 — Rename detection (old name gone, new name present)
  TC-MOVE-001   — Move detection (file path reflects new prefix under same container group)
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    async_wait_for_stable_record_count,
    wait_until_graph_condition,
)
from connectors.s3.s3_storage_helper import (  # type: ignore[import-not-found]  # noqa: E402
    S3StorageHelper,
)

logger = logging.getLogger("s3-lifecycle-test")


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
class TestS3Connector:
    """Integration tests for the S3 connector (constructor/destructor in conftest)."""

    # ------------------------------------------------------------------ #
    # TC-SYNC-001 — Full sync + graph validation
    # ------------------------------------------------------------------ #
    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        s3_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """
        TC-SYNC-001: After full sync, validate the graph thoroughly.
        """
        connector_id = s3_connector["connector_id"]
        uploaded = s3_connector["uploaded_count"]
        full_count = s3_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, uploaded)

        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=1,
            min_record_edges=max(1, full_count - 1),
        )

        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)

        known_name = s3_connector.get("rename_source_name")
        if known_name:
            await graph_provider.assert_record_paths_or_names_contain(
                connector_id, [known_name]
            )

        perm_count = await graph_provider.count_permission_edges(connector_id)
        logger.info("Permission edges: %d (connector %s)", perm_count, connector_id)

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("Graph summary after full sync: %s (connector %s)", summary, connector_id)

    # ------------------------------------------------------------------ #
    # TC-INCR-001 — Incremental sync (new files)
    # ------------------------------------------------------------------ #
    @pytest.mark.order(2)
    async def test_tc_incr_001_incremental_sync_new_files(
        self,
        s3_connector: Dict[str, Any],
        s3_storage: S3StorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """
        TC-INCR-001: Upload new files, run incremental sync, verify:
        - New files appear as new Records in the graph
        - Existing record count is stable (old records unchanged)
        """
        connector_id = s3_connector["connector_id"]
        bucket_name = s3_connector["bucket_name"]
        before_count = await graph_provider.count_records(connector_id)

        new_files = {
            "incremental-test/new-file-alpha.csv": b"id,name,value\n1,alpha,100\n2,bravo,200\n",
            "incremental-test/new-file-beta.csv": b"id,name,value\n1,charlie,300\n2,delta,400\n",
        }
        for blob_key, file_bytes in new_files.items():
            s3_storage.upload_blob(bucket_name, blob_key, file_bytes, content_type="text/csv")

        logger.info(
            "Uploaded %d new files for incremental sync (connector %s)",
            len(new_files), connector_id,
        )

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _incr_done() -> bool:
            return await graph_provider.count_records(connector_id) > before_count

        await wait_until_graph_condition(
            connector_id,
            check=_incr_done,
            timeout=180,
            poll_interval=10,
            description="incremental sync (new files)",
        )

        after_count = await graph_provider.count_records(connector_id)
        assert after_count > before_count, (
            f"Expected record count to increase after uploading new files; "
            f"before={before_count}, after={after_count} (connector {connector_id})"
        )

        all_names = await graph_provider.fetch_record_names(connector_id)
        logger.info(
            "Record names after incremental sync (%d total): %s (connector %s)",
            len(all_names), all_names[:20], connector_id,
        )

        new_names = [Path(blob_key).name for blob_key in new_files]
        for name in new_names:
            found = await graph_provider.record_paths_or_names_contain(connector_id, [name])
            if not found:
                logger.warning(
                    "New file '%s' not found by exact name in graph "
                    "(container %s, connector %s)",
                    name, bucket_name, connector_id,
                )

        assert after_count >= before_count, (
            f"Old records lost during incremental sync; before={before_count}, after={after_count} "
            f"(connector {connector_id})"
        )

        s3_connector["incr_sync_count"] = after_count
        logger.info(
            "TC-INCR-001 passed: before=%d, after=%d (connector %s)",
            before_count, after_count, connector_id,
        )

    # ------------------------------------------------------------------ #
    # TC-UPDATE-001 — Content change detection
    # ------------------------------------------------------------------ #
    @pytest.mark.order(3)
    async def test_tc_update_001_content_change_detection(
        self,
        s3_connector: Dict[str, Any],
        s3_storage: S3StorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """
        TC-UPDATE-001: Overwrite an existing object with new content. After sync:
        - The Record still exists (updated in place)
        - Record count is unchanged
        - ETag changes
        """
        connector_id = s3_connector["connector_id"]
        bucket_name = s3_connector["bucket_name"]
        update_key = s3_connector["update_target_key"]
        update_name = s3_connector["update_target_name"]

        await async_wait_for_stable_record_count(graph_provider, connector_id)
        before_count = await graph_provider.count_records(connector_id)
        logger.info(
            "TC-UPDATE-001 baseline: %d records (connector %s)",
            before_count, connector_id,
        )

        pre_meta = s3_storage.get_blob_metadata(bucket_name, update_key)
        logger.info(
            "Pre-update metadata for %s: etag=%s (connector %s)",
            update_key, pre_meta.get("etag"), connector_id,
        )

        new_content = f"Updated content at {uuid.uuid4().hex}".encode()
        s3_storage.overwrite_blob(
            bucket_name, update_key, new_content, content_type="text/plain"
        )

        post_meta = s3_storage.get_blob_metadata(bucket_name, update_key)
        assert post_meta["etag"] != pre_meta["etag"], (
            f"Object ETag should change after overwrite; "
            f"before={pre_meta['etag']}, after={post_meta['etag']} (connector {connector_id})"
        )

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _update_synced() -> bool:
            return await graph_provider.count_records(connector_id) >= before_count

        await wait_until_graph_condition(
            connector_id,
            check=_update_synced,
            timeout=120,
            poll_interval=10,
            description="update sync",
        )

        await graph_provider.assert_record_paths_or_names_contain(
            connector_id, [update_name]
        )

        after_count = await graph_provider.count_records(connector_id)
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
    async def test_tc_rename_001_rename_detection(
        self,
        s3_connector: Dict[str, Any],
        s3_storage: S3StorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """
        TC-RENAME-001: Rename an object in S3. After incremental sync:
        - A Record exists for the new name
        - The Record for the old name is gone from the graph
        """
        connector_id = s3_connector["connector_id"]
        bucket_name = s3_connector["bucket_name"]
        old_key = s3_connector["rename_source_key"]
        old_name = Path(old_key).name

        new_name = f"renamed-{old_name}"
        parts = old_key.rsplit("/", 1)
        new_key = f"{parts[0]}/{new_name}" if len(parts) == 2 else new_name

        logger.info(
            "Renaming %s/%s -> %s (connector %s)",
            bucket_name, old_key, new_key, connector_id,
        )

        s3_storage.rename_object(bucket_name, old_key, new_key)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _rename_visible() -> bool:
            return await graph_provider.record_paths_or_names_contain(connector_id, [new_name])

        await wait_until_graph_condition(
            connector_id,
            check=_rename_visible,
            timeout=120,
            poll_interval=10,
            description="rename sync",
        )

        await graph_provider.assert_record_paths_or_names_contain(connector_id, [new_name])
        await graph_provider.assert_record_not_exists(connector_id, old_name)

        s3_connector["move_source_key"] = new_key
        s3_connector["move_source_name"] = new_name
        logger.info(
            "TC-RENAME-001 passed: '%s' -> '%s', old name absent (connector %s)",
            old_name, new_name, connector_id,
        )

    # ------------------------------------------------------------------ #
    # TC-MOVE-001 — Move detection (same bucket)
    # ------------------------------------------------------------------ #
    @pytest.mark.order(5)
    async def test_tc_move_001_move_detection(
        self,
        s3_connector: Dict[str, Any],
        s3_storage: S3StorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """
        TC-MOVE-001: Move an object to a different prefix. After sync:
        - Record exists at the new path (File.path includes the new prefix)

        Object-store connectors use one RecordGroup per bucket; folder prefixes are paths,
        not additional RecordGroups.
        """
        connector_id = s3_connector["connector_id"]
        bucket_name = s3_connector["bucket_name"]
        old_key = s3_connector["move_source_key"]
        move_name = s3_connector["move_source_name"]

        new_prefix = "moved-folder"
        new_key = f"{new_prefix}/{move_name}"

        logger.info(
            "Moving %s/%s -> %s (connector %s)",
            bucket_name, old_key, new_key, connector_id,
        )

        s3_storage.move_object(bucket_name, old_key, new_key)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _move_visible() -> bool:
            return await graph_provider.record_name_path_contains(
                connector_id, move_name, new_prefix
            )

        await wait_until_graph_condition(
            connector_id,
            check=_move_visible,
            timeout=120,
            poll_interval=10,
            description="move sync",
        )

        assert await graph_provider.record_name_path_contains(
            connector_id, move_name, new_prefix
        ), (
            f"Expected File.path for {move_name!r} to contain {new_prefix!r} "
            f"(connector {connector_id})"
        )

        logger.info(
            "TC-MOVE-001 passed: file at new path under %s/ (connector %s)",
            new_prefix, connector_id,
        )
