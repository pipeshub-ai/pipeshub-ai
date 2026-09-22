# pyright: ignore-file

"""Google Drive Workspace – who can open what, asked as each person.

The test user owns four files, shared four ways, and both that user and a second
Workspace member log in to PipesHub under their own addresses. Every check opens
the record as one of them, through the same access check search and chat use.

  order 1  TC-DRV-PERM-001  — a file nobody was given stays with its owner
  order 2  TC-DRV-PERM-002  — a file shared with the second user opens for them
  order 3  TC-DRV-PERM-003  — shared with everyone in the domain (known gap, xfail)
  order 4  TC-DRV-PERM-004  — taking the share away removes their access, not the owner's
"""

from __future__ import annotations

import os
import sys
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
    unshare_drive_item,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_for_sync_completion  # noqa: E402
from helper.record_access import AccessDenied, wait_for_record_access  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "300"))


async def _resync(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
) -> None:
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, timeout=_SYNC_TIMEOUT_SEC
    )


class TestDriveWorkspacePermissions:
    @pytest.mark.order(1)
    async def test_tc_drv_perm_001_unshared_file_stays_with_owner(
        self, drive_workspace_permission_connector: dict[str, Any]
    ) -> None:
        state = drive_workspace_permission_connector
        record_id = state["private_record_id"]

        wait_for_record_access(
            state["owner"], record_id, expect_access=True,
            description="their own unshared file",
        )
        wait_for_record_access(
            state["reader"], record_id, expect_access=False,
            description="a colleague's file that was never shared with them",
        )

    @pytest.mark.order(2)
    async def test_tc_drv_perm_002_file_shared_with_user_opens_for_them(
        self, drive_workspace_permission_connector: dict[str, Any]
    ) -> None:
        state = drive_workspace_permission_connector
        record_id = state["shared_record_id"]

        wait_for_record_access(
            state["reader"], record_id, expect_access=True,
            description="a file shared with them as reader",
        )
        wait_for_record_access(
            state["owner"], record_id, expect_access=True,
            description="their own file after sharing it",
        )

    @pytest.mark.order(3)
    @pytest.mark.xfail(
        strict=True,
        raises=AccessDenied,
        reason=(
            "Domain-wide Drive shares are dropped: the connector maps them to a DOMAIN "
            "permission, and the step that writes permission edges has that branch "
            "commented out, so no colleague is ever granted access. Everyone in the "
            "domain can open the file in Drive; nobody but the owner can in PipesHub."
        ),
    )
    async def test_tc_drv_perm_003_domain_share_opens_for_colleague(
        self, drive_workspace_permission_connector: dict[str, Any]
    ) -> None:
        state = drive_workspace_permission_connector
        if "domain_share_error" in state:
            pytest.skip(f"Workspace refused a domain-wide share: {state['domain_share_error']}")

        wait_for_record_access(
            state["reader"], state["domain_record_id"], expect_access=True,
            description="a file shared with everyone in their domain",
            timeout=60,
        )

    @pytest.mark.order(4)
    async def test_tc_drv_perm_004_revoked_share_removes_access(
        self,
        drive_workspace_permission_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        state = drive_workspace_permission_connector
        record_id = state["revoke_record_id"]

        # Without first seeing access, a denial afterwards would prove nothing.
        wait_for_record_access(
            state["reader"], record_id, expect_access=True,
            description="a file shared with them, before the share is removed",
        )

        await unshare_drive_item(
            drive_workspace_datasource, state["revoke_file_id"], state["revoke_permission_id"]
        )
        await _resync(pipeshub_client, graph_provider, state["connector_id"])

        wait_for_record_access(
            state["reader"], record_id, expect_access=False,
            description="a file whose share with them was removed in Drive",
            timeout=_SYNC_TIMEOUT_SEC,
        )
        wait_for_record_access(
            state["owner"], record_id, expect_access=True,
            description="their own file after removing someone else's access",
        )
