# pyright: ignore-file

"""Google Drive Workspace — fixes from the connector audit, against a real tenant.

Each test is one behaviour the simulator-based audit found broken, checked on the real API where the
simulator could only follow the documentation:

- a file moved from one Shared Drive to another is kept and filed under its new drive (Drive reports it
  `removed` in the old drive's feed, which used to delete it);
- renaming or sharing a Google Doc does not re-index it (Drive moves `version` on both).

One connector scoped to two fresh Shared Drives (``drive_workspace_audit_connector``).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import (  # type: ignore[import-not-found]  # noqa: E402
    ProgressStatus,
)
from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    _create_permission,
    move_drive_item,
    rename_drive_item,
    sync_until,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.indexing_wait import wait_until_record_indexing_completed  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

logger = logging.getLogger("drive-workspace-audit-fixes-test")

_INDEXING_WAIT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_INDEXING_WAIT", "300"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]


class TestDriveWorkspaceAuditFixes:
    @pytest.mark.order(1)
    async def test_tc_drv_aud_001_file_moved_between_shared_drives_is_kept(
        self,
        drive_workspace_audit_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-AUD-001: moving a file to another Shared Drive keeps one record, filed under the new drive."""
        state = drive_workspace_audit_connector
        connector_id = state["connector_id"]
        file_id = state["move_file_id"]
        before = await graph_provider.get_record_by_external_id(connector_id, file_id)
        assert before is not None, "move-me.txt is not in the graph before the move"

        await move_drive_item(drive_workspace_datasource, file_id, new_parent_id=state["drive_b"],
                              old_parent_id=state["drive_a"])

        async def _filed_under_b() -> bool:
            return await graph_provider.record_belongs_to_external_group(connector_id, file_id, state["drive_b"])

        await sync_until(pipeshub_client, graph_provider, connector_id, _filed_under_b,
                         description="move-me.txt filed under the second Shared Drive")
        # Two more syncs: whichever drive's feed is read last, the record must survive it.
        for _ in range(2):
            await sync_until(pipeshub_client, graph_provider, connector_id, _filed_under_b,
                             description="move-me.txt still filed under the second Shared Drive")
        after = await graph_provider.get_record_by_external_id(connector_id, file_id)
        assert after is not None, "the moved file was deleted"
        assert after.id == before.id, "the moved file was stored again instead of moved"

    @pytest.mark.order(2)
    async def test_tc_drv_aud_002_renaming_and_sharing_a_google_doc_does_not_reindex_it(
        self,
        drive_workspace_audit_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-AUD-002: a rename and a new share move the Doc's Drive version but not its content.

        A re-index shows as the record leaving COMPLETED in the same write as the rename (the connector resets
        its indexing status when it decides the content changed). The stored record version is not the signal:
        the processor raises it whenever the Drive version moves.
        """
        state = drive_workspace_audit_connector
        connector_id = state["connector_id"]
        doc_id = state["doc_id"]
        before = await wait_until_record_indexing_completed(
            graph_provider, connector_id, doc_id, timeout=_INDEXING_WAIT_SEC,
            description="TC-DRV-AUD-002 audit-doc indexed", pipeshub_client=pipeshub_client,
        )

        await rename_drive_item(drive_workspace_datasource, doc_id, "audit-doc-renamed")
        second_user = state.get("second_user_email")
        if second_user:
            _create_permission(drive_workspace_datasource, doc_id,
                               {"type": "user", "role": "reader", "emailAddress": second_user},
                               sendNotificationEmail=False)

        async def _renamed() -> bool:
            record = await graph_provider.get_record_by_external_id(connector_id, doc_id)
            return record is not None and record.record_name == "audit-doc-renamed"

        await sync_until(pipeshub_client, graph_provider, connector_id, _renamed,
                         description="audit-doc renamed in the graph")
        after = await graph_provider.get_record_by_external_id(connector_id, doc_id)
        assert after.indexing_status == ProgressStatus.COMPLETED.value, (
            f"the Doc was queued for re-indexing by a rename (indexing status {after.indexing_status})"
        )
        assert after.virtual_record_id == before.virtual_record_id, "the Doc's indexed content was replaced"
