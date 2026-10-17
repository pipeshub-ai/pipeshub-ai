# pyright: ignore-file

"""Google Drive personal — item lifecycle against a real account.

The personal connector reads its own change feed and deletes through a different
path than Drive Workspace, so GD_Testing's trash / restore / permanent delete /
rename / same-name cases are run here too, on a connector scoped to a fresh tree
(``drive_individual_lifecycle_connector``, indexing on).
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    delete_drive_item_permanently,
    record_absent,
    record_present,
    rename_drive_item,
    sync_until,
    trash_drive_item,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.indexing_wait import wait_until_record_indexing_completed  # noqa: E402
from helper.vector_store import VectorStoreProbe  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

logger = logging.getLogger("drive-individual-lifecycle-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_individual,
    pytest.mark.asyncio(loop_scope="session"),
]

_INDEXING_WAIT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_INDEXING_WAIT", "600"))


class TestDriveIndividualLifecycle:

    @pytest.mark.order(1)
    async def test_tc_drv_ind_lc_001_trash_removes_record_and_embeddings(
        self,
        drive_individual_lifecycle_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        vector_store: VectorStoreProbe,
    ) -> None:
        """TC-DRV-IND-LC-001 (GD N19): trashing a file deletes its record and its vectors."""
        state = drive_individual_lifecycle_connector
        connector_id = state["connector_id"]
        file_id = state["trash_file_id"]
        record = await wait_until_record_indexing_completed(
            graph_provider, connector_id, file_id,
            timeout=_INDEXING_WAIT_SEC, pipeshub_client=pipeshub_client,
            description="trash-me.txt indexed before trash",
        )
        await vector_store.assert_embeddings_present(record.virtual_record_id)

        await trash_drive_item(drive_individual_datasource, file_id)
        await sync_until(
            pipeshub_client, graph_provider, connector_id,
            record_absent(graph_provider, connector_id, file_id),
            description=f"trash-me.txt ({file_id}) removed after trash",
        )
        await vector_store.assert_embeddings_gone(record.virtual_record_id)

    @pytest.mark.order(2)
    async def test_tc_drv_ind_lc_002_restore_resyncs_record(
        self,
        drive_individual_lifecycle_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-IND-LC-002 (GD N20): restoring from trash syncs the file again under its folder."""
        state = drive_individual_lifecycle_connector
        connector_id = state["connector_id"]
        file_id = state["trash_file_id"]
        if await graph_provider.get_record_by_external_id(connector_id, file_id) is not None:
            pytest.skip("TC-DRV-IND-LC-001 did not leave the file trashed")

        await trash_drive_item(drive_individual_datasource, file_id, trashed=False)
        await sync_until(
            pipeshub_client, graph_provider, connector_id,
            record_present(graph_provider, connector_id, file_id),
            description=f"trash-me.txt ({file_id}) back after restore",
        )
        parent = await graph_provider.get_record_parent_external_id(connector_id, file_id)
        assert parent == state["root_folder_id"], f"restored parent {parent!r} != lifecycle root"

    @pytest.mark.order(3)
    async def test_tc_drv_ind_lc_003_permanent_folder_delete_removes_descendants(
        self,
        drive_individual_lifecycle_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-IND-LC-003 (GD N21): deleting a folder for good removes every descendant."""
        state = drive_individual_lifecycle_connector
        connector_id = state["connector_id"]
        subtree = {k: state[k] for k in ("doomed_folder_id", "doomed_sub_id", "doomed_a_id", "doomed_b_id")}

        await delete_drive_item_permanently(drive_individual_datasource, state["doomed_folder_id"])

        async def _gone() -> bool:
            for external_id in subtree.values():
                if await graph_provider.get_record_by_external_id(connector_id, external_id) is not None:
                    return False
            return True

        try:
            await sync_until(
                pipeshub_client, graph_provider, connector_id, _gone,
                description="doomed/ and all its descendants removed after permanent delete",
            )
        except TimeoutError:
            left = [
                key for key, eid in subtree.items()
                if await graph_provider.get_record_by_external_id(connector_id, eid) is not None
            ]
            raise AssertionError(f"after deleting doomed/ for good, still in the graph: {left}") from None

    @pytest.mark.order(4)
    async def test_tc_drv_ind_lc_004_folder_rename_keeps_children(
        self,
        drive_individual_lifecycle_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-IND-LC-004 (GD N22): renaming a folder keeps its children's ids and edges."""
        state = drive_individual_lifecycle_connector
        connector_id = state["connector_id"]
        folder_id, child_id = state["renamable_folder_id"], state["renamable_child_id"]
        folder_before = await graph_provider.get_record_by_external_id(connector_id, folder_id)
        child_before = await graph_provider.get_record_by_external_id(connector_id, child_id)
        assert folder_before is not None and child_before is not None, (
            "renamable fixture records missing before rename"
        )

        new_name = f"renamed-{uuid.uuid4().hex[:6]}"
        await rename_drive_item(drive_individual_datasource, folder_id, new_name)

        async def _renamed() -> bool:
            folder = await graph_provider.get_record_by_external_id(connector_id, folder_id)
            return folder is not None and folder.record_name == new_name

        await sync_until(
            pipeshub_client, graph_provider, connector_id, _renamed,
            description=f"folder {folder_id} renamed to {new_name}",
        )
        folder_after = await graph_provider.get_record_by_external_id(connector_id, folder_id)
        child_after = await graph_provider.get_record_by_external_id(connector_id, child_id)
        assert folder_after is not None and folder_after.id == folder_before.id, (
            "folder record was replaced instead of renamed"
        )
        assert child_after is not None and child_after.id == child_before.id, "child record replaced"
        assert await graph_provider.get_record_parent_external_id(connector_id, child_id) == folder_id

    @pytest.mark.order(5)
    async def test_tc_drv_ind_lc_005_same_name_siblings_both_synced(
        self,
        drive_individual_lifecycle_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-IND-LC-005 (GD E2): two same-named files in one folder are two records."""
        state = drive_individual_lifecycle_connector
        connector_id = state["connector_id"]
        first = await graph_provider.get_record_by_external_id(connector_id, state["dup_first_id"])
        second = await graph_provider.get_record_by_external_id(connector_id, state["dup_second_id"])
        assert first is not None and second is not None, "a same-named sibling is missing"
        assert first.id != second.id, "same-named siblings collapsed into one record"
