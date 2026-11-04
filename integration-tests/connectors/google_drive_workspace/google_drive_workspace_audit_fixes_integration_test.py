# pyright: ignore-file

"""Google Drive Workspace — fixes from the connector audit, against a real tenant.

Each test is one behaviour the simulator-based audit found broken, checked on the real API where the
simulator could only follow the documentation:

- a file moved from one Shared Drive to another is kept and filed under its new drive (Drive reports it
  `removed` in the old drive's feed, which used to delete it);
- renaming or sharing a Google Doc does not re-index it (Drive moves `version` on both);
- turning on a connector whose saved service account key is not a usable key file says what to fix, instead
  of a generic "try again".

One connector scoped to two fresh Shared Drives (``drive_workspace_audit_connector``); the key checks make
their own connectors and never reach Google.
"""

from __future__ import annotations

import json
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

from app.config.constants.arangodb import (  # type: ignore[import-not-found]  # noqa: E402
    ProgressStatus,
)
from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    ENV_ADMIN_EMAIL,
    _create_permission,
    connector_filters,
    move_drive_item,
    rename_drive_item,
    sync_until,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.indexing_wait import wait_until_record_indexing_completed  # noqa: E402
from pipeshub_client import PipeshubClient, PipeshubClientError  # noqa: E402

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
            if record is None or record.record_name != "audit-doc-renamed":
                return False
            if not second_user:
                return True
            # The share can land after the rename. Waiting for it means a re-index caused by the share
            # is still in front of the assertions.
            return await graph_provider.user_has_direct_record_permission(connector_id, second_user, doc_id)

        await sync_until(pipeshub_client, graph_provider, connector_id, _renamed,
                         description="audit-doc renamed in the graph")
        after = await graph_provider.get_record_by_external_id(connector_id, doc_id)
        assert after.indexing_status == ProgressStatus.COMPLETED.value, (
            f"the Doc was queued for re-indexing by a rename (indexing status {after.indexing_status})"
        )
        assert after.virtual_record_id == before.virtual_record_id, "the Doc's indexed content was replaced"


_CORRUPT_KEY = json.dumps({
    "type": "service_account", "project_id": "p", "private_key_id": "k", "client_id": "1",
    "client_email": "sa@p.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token",
    "private_key": "-----BEGIN PRIVATE KEY-----\\nbm90IGEga2V5\\n-----END PRIVATE KEY-----\\n",
})


class TestDriveWorkspaceUnreadableKey:
    @pytest.mark.parametrize("saved_key", ["this is not a key file", _CORRUPT_KEY], ids=["not-json", "corrupt-key"])
    async def test_tc_drv_aud_003_a_key_that_is_not_a_key_file_is_named_when_turned_on(
        self,
        saved_key: str,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-DRV-AUD-003: turning on a connector with an unusable key is refused with what to fix."""
        admin_email = os.getenv(ENV_ADMIN_EMAIL, "").strip() or "admin@example.com"
        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=f"drive-ws-bad-key-{uuid.uuid4().hex[:8]}",
            scope="team",
            config={"auth": {"adminEmail": admin_email, "serviceAccountJson": saved_key},
                    "filters": connector_filters()},
            auth_type="CUSTOM",
        )
        connector_id = instance.connector_id
        try:
            with pytest.raises(PipeshubClientError) as refused:
                pipeshub_client.toggle_sync(connector_id, enable=True)
            message = str(refused.value)
            assert "HTTP 400" in message, f"turning on a connector with a bad key was not refused as such: {message}"
            assert "not a valid JSON key file" in message, f"the refusal does not say the key is the problem: {message}"
            for internal in ("PEM", "Expecting value", "delegated credentials", "Something went wrong"):
                assert internal not in message, f"the refusal shows {internal!r}: {message}"
            connector = pipeshub_client.get_connector(connector_id)
            assert not connector.get("isActive"), "a connector with a bad key was left on"
        finally:
            pipeshub_client.delete_connector(connector_id)
