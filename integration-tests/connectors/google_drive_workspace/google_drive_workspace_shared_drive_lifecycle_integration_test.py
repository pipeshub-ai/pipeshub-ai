# pyright: ignore-file

"""Google Drive Workspace — Shared Drive lifecycle and group permissions, real tenant.

GD_Testing "Permission edge cases": a file shared with a Google Group, a group's
membership changing, a Shared Drive renamed, and a Shared Drive deleted; plus GD N21
(permanent folder delete) inside a Shared Drive, which syncs through a different
change feed than My Drive. One
connector scoped to one fresh Shared Drive (``drive_workspace_sd_lifecycle_connector``);
the delete test runs last because it removes that drive.
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

from google.auth.exceptions import RefreshError  # type: ignore[import-not-found]  # noqa: E402

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    ENV_ADMIN_EMAIL,
    ENV_SA_JSON,
    ENV_SECOND_USER,
    build_admin_group_member_client,
    delete_drive_item_permanently,
    delete_shared_drive,
    list_group_user_member_emails,
    rename_shared_drive,
    share_drive_item_with_group,
    sync_until,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

logger = logging.getLogger("drive-workspace-sd-lifecycle-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]


class TestDriveWorkspaceSharedDriveLifecycle:
    """Group permissions, then rename, then delete, on one fresh Shared Drive."""

    @pytest.mark.order(1)
    async def test_tc_drv_sd_lc_001_group_share_grants_the_group(
        self,
        drive_workspace_sd_lifecycle_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-SD-LC-001 (GD PE1): a share with a Google Group gives the group a PERMISSION edge."""
        state = drive_workspace_sd_lifecycle_connector
        group_email = state.get("group_email")
        if not group_email:
            pytest.skip("the tenant has no Google Group with members")
        connector_id = state["connector_id"]
        file_id = state["group_file_id"]

        share_drive_item_with_group(drive_workspace_datasource, file_id, group_email)

        async def _group_granted() -> bool:
            return await graph_provider.group_has_record_permission(connector_id, group_email, file_id)

        await sync_until(
            pipeshub_client, graph_provider, connector_id, _group_granted,
            description=f"group {group_email} holds PERMISSION on group-share.txt",
        )
        members = await graph_provider.count_group_members(connector_id, group_email)
        assert members > 0, f"group {group_email} has no member edges in the graph"
        state["group_shared"] = True

    @pytest.mark.order(1)
    @pytest.mark.xfail(
        strict=True,
        reason="B3: a group member who can open the file but not read its permission list gets a "
        "direct USER edge from the permissions fallback (team connector _fetch_permissions), "
        "so the group share fans out to members who sync.",
    )
    async def test_tc_drv_sd_lc_001b_group_share_does_not_fan_out_to_members(
        self,
        drive_workspace_sd_lifecycle_connector: dict[str, Any],
        drive_workspace_admin_datasource: Any,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-SD-LC-001b (GD PE1): members reached only through the group get no direct edge."""
        state = drive_workspace_sd_lifecycle_connector
        if not state.get("group_shared"):
            pytest.skip("TC-DRV-SD-LC-001 did not share the file with the group")
        if not state.get("second_user_email"):
            # B3 only shows through a group member who syncs; without one the xfail would pass.
            pytest.skip(f"{ENV_SECOND_USER} not set, so no group member syncs")
        connector_id = state["connector_id"]
        file_id = state["group_file_id"]
        organizer = state["test_user_email"].lower()
        fanned_out = [
            address
            for address in await list_group_user_member_emails(drive_workspace_admin_datasource, state["group_email"])
            if address.lower() != organizer
            and await graph_provider.user_has_direct_record_permission(connector_id, address, file_id)
        ]
        assert not fanned_out, f"group share fanned out to direct USER edges for {fanned_out}"

    @pytest.mark.order(2)
    async def test_tc_drv_sd_lc_002_group_membership_change_follows(
        self,
        drive_workspace_sd_lifecycle_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-SD-LC-002 (GD PE2): removing a member from the group removes their access through it."""
        state = drive_workspace_sd_lifecycle_connector
        group_email = state.get("group_email")
        if not group_email:
            pytest.skip("the tenant has no Google Group with members")
        try:
            directory = build_admin_group_member_client(
                os.environ[ENV_SA_JSON].strip(), os.environ[ENV_ADMIN_EMAIL].strip()
            )
        except RefreshError as e:
            pytest.skip(
                "changing group membership needs admin.directory.group.member in the service "
                f"account's domain-wide delegation, which this tenant does not grant: {e}"
            )
        connector_id = state["connector_id"]
        members = directory.members().list(groupKey=group_email).execute().get("members") or []
        organizer = state["test_user_email"].lower()
        victim = next(
            (m["email"] for m in members if m.get("type") == "USER" and m["email"].lower() != organizer),
            None,
        )
        if victim is None:
            pytest.skip(f"group {group_email} has no member other than the organizer")
        assert await graph_provider.group_has_member(connector_id, group_email, victim), (
            f"{victim} is not a member of {group_email} in the graph before removal"
        )

        directory.members().delete(groupKey=group_email, memberKey=victim).execute()
        try:
            async def _member_gone() -> bool:
                return not await graph_provider.group_has_member(connector_id, group_email, victim)

            await sync_until(
                pipeshub_client, graph_provider, connector_id, _member_gone,
                description=f"{victim} no longer a member of {group_email} in the graph",
            )
        finally:
            directory.members().insert(
                groupKey=group_email, body={"email": victim, "role": "MEMBER"}
            ).execute()

    @pytest.mark.order(3)
    async def test_tc_drv_sd_lc_003_permanent_folder_delete_in_shared_drive(
        self,
        drive_workspace_sd_lifecycle_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-SD-LC-003 (GD N21, Shared Drive): deleting a folder for good removes its descendants."""
        state = drive_workspace_sd_lifecycle_connector
        connector_id = state["connector_id"]
        subtree = {"sd-folder": state["folder_id"], "sd-file.txt": state["file_id"]}

        await delete_drive_item_permanently(drive_workspace_datasource, state["folder_id"])

        async def _gone() -> bool:
            for external_id in subtree.values():
                if await graph_provider.get_record_by_external_id(connector_id, external_id) is not None:
                    return False
            return True

        try:
            await sync_until(
                pipeshub_client, graph_provider, connector_id, _gone,
                description="Shared Drive folder and its file removed after permanent delete",
            )
        except TimeoutError:
            left = [
                label for label, eid in subtree.items()
                if await graph_provider.get_record_by_external_id(connector_id, eid) is not None
            ]
            raise AssertionError(
                f"after deleting sd-folder/ for good in a Shared Drive, still in the graph: {left}"
            ) from None
        state["folder_deleted"] = True

    @pytest.mark.order(4)
    async def test_tc_drv_sd_lc_004_shared_drive_rename_updates_record_group(
        self,
        drive_workspace_sd_lifecycle_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-SD-LC-004 (GD PE3): renaming a Shared Drive renames its RecordGroup."""
        state = drive_workspace_sd_lifecycle_connector
        connector_id = state["connector_id"]
        drive_id = state["drive_id"]
        group_before = await graph_provider.get_record_group_by_external_id(connector_id, drive_id)
        assert group_before is not None, "Shared Drive RecordGroup missing before rename"

        new_name = f"pipeshub-it-sd-lc-renamed-{uuid.uuid4().hex[:6]}"
        await rename_shared_drive(drive_workspace_datasource, drive_id, new_name)

        async def _renamed() -> bool:
            group = await graph_provider.get_record_group_by_external_id(connector_id, drive_id)
            return group is not None and group.name == new_name

        await sync_until(
            pipeshub_client, graph_provider, connector_id, _renamed,
            description=f"RecordGroup for {drive_id} renamed to {new_name}",
        )
        group_after = await graph_provider.get_record_group_by_external_id(connector_id, drive_id)
        assert group_after.id == group_before.id, "RecordGroup was replaced instead of renamed"
        state["drive_name"] = new_name

    @pytest.mark.order(5)
    async def test_tc_drv_sd_lc_005_shared_drive_delete_removes_group_and_records(
        self,
        drive_workspace_sd_lifecycle_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-SD-LC-005 (GD PE4): deleting a Shared Drive removes its RecordGroup and records."""
        state = drive_workspace_sd_lifecycle_connector
        connector_id = state["connector_id"]
        drive_id = state["drive_id"]
        item_ids = [state["group_file_id"]]
        if not state.get("folder_deleted"):
            item_ids += [state["folder_id"], state["file_id"]]

        await delete_shared_drive(drive_workspace_datasource, drive_id, admin_drive=state["admin_drive"])
        state["drive_deleted"] = True

        async def _gone() -> bool:
            if await graph_provider.get_record_group_by_external_id(connector_id, drive_id) is not None:
                return False
            for external_id in item_ids:
                if await graph_provider.get_record_by_external_id(connector_id, external_id) is not None:
                    return False
            return True

        try:
            await sync_until(
                pipeshub_client, graph_provider, connector_id, _gone,
                description=f"deleted Shared Drive {drive_id}: RecordGroup and records removed",
            )
        except TimeoutError:
            group = await graph_provider.get_record_group_by_external_id(connector_id, drive_id)
            left = [
                eid for eid in item_ids
                if await graph_provider.get_record_by_external_id(connector_id, eid) is not None
            ]
            raise AssertionError(
                f"after deleting Shared Drive {drive_id}: RecordGroup still present={group is not None}, "
                f"records still present={left}"
            ) from None
