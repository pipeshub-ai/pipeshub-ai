# pyright: ignore-file

"""Google Drive personal — Shared Drives, shortcuts and a revoked token, real tenant.

GD_Testing cases marked [P] plus the Shared Drive ones the personal connector has to
handle on its own (it has no admin view; it sees exactly what the OAuth user sees):

  TC-DRV-IND-SD-001  a Shared Drive the user created syncs (GD N14)
  TC-DRV-IND-SD-002  an item in a Shared Drive another user owns syncs once the user is
                     a member (GD N17)
  TC-DRV-IND-SD-003  removing the user from that Shared Drive removes its items (GD PE5)
  TC-DRV-IND-SC-001  a shortcut to a file in a Shared Drive the user is not a member of
                     syncs the target (GD N15)
  TC-DRV-IND-AUTH-001 a revoked/invalid token fails the connection test and the
                     connector shows an error (GD N23)

The Shared Drives belonging to other users are created by impersonating them through
the Workspace service account (same tenant as the personal OAuth user).
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_individual.drive_individual_test_utils import (  # noqa: E402
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    ENV_ADMIN_EMAIL,
    ENV_SA_JSON,
    ENV_SECOND_USER,
    build_drive_datasource,
    connector_filters,
    create_drive_folder,
    create_drive_shortcut,
    create_drive_text_file,
    create_shared_drive,
    delete_drive_folder,
    delete_shared_drive,
    record_present,
    share_drive_item_with_user,
    sync_until,
    unshare_drive_item_from_user,
    wait_until_shared_drive_files_listed,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.oauth_token_helper import inject_connector_credentials  # noqa: E402
from pipeshub_client import PipeshubClient, PipeshubClientError  # noqa: E402

logger = logging.getLogger("drive-individual-sd-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_individual,
    pytest.mark.asyncio(loop_scope="session"),
]

# A third Workspace member, owner of a Shared Drive the OAuth user is not in.
ENV_THIRD_USER = "GOOGLE_DRIVE_WORKSPACE_THIRD_USER_EMAIL"
_AUTH_FAILURE_WAIT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_AUTH_FAILURE_WAIT", "180"))


async def _impersonate(email: str) -> GoogleDriveDataSource:
    try:
        sa_json = os.environ[ENV_SA_JSON].strip()
        admin = os.environ[ENV_ADMIN_EMAIL].strip()
    except KeyError as e:
        pytest.skip(f"Workspace service account env missing ({e}); cannot act as {email}")
    return await build_drive_datasource(sa_json, admin, email)


async def _admin_drive() -> GoogleDriveDataSource:
    admin = os.environ[ENV_ADMIN_EMAIL].strip()
    return await _impersonate(admin)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def leftover_shared_drives() -> Any:
    """Shared Drives a test created for a later one to delete; whatever is left is deleted at module end."""
    drive_ids: set[str] = set()
    yield drive_ids
    for drive_id in drive_ids:
        admin = await _admin_drive()
        await delete_shared_drive(admin, drive_id, admin_drive=admin)


class TestDriveIndividualSharedDrives:

    @pytest.mark.order(1)
    async def test_tc_drv_ind_sd_001_own_shared_drive_syncs(
        self,
        drive_individual_unfiltered_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-IND-SD-001 (GD N14): items in a member Shared Drive sync for the personal connector."""
        connector_id = drive_individual_unfiltered_connector["connector_id"]
        drive_id = await create_shared_drive(
            drive_individual_datasource, f"pipeshub-it-ind-sd-{uuid.uuid4().hex[:6]}"
        )
        try:
            folder = await create_drive_folder(drive_individual_datasource, "ind-sd-folder", parent_id=drive_id)
            file_id = await create_drive_text_file(
                drive_individual_datasource, "ind-sd-file.txt", parent_id=folder, content="personal sd\n"
            )
            await wait_until_shared_drive_files_listed(drive_individual_datasource, drive_id, [folder, file_id])

            async def _synced() -> bool:
                record = await graph_provider.get_record_by_external_id(connector_id, file_id)
                group = await graph_provider.get_record_group_by_external_id(connector_id, drive_id)
                return record is not None and group is not None

            await sync_until(
                pipeshub_client, graph_provider, connector_id, _synced,
                description=f"file in the user's own Shared Drive {drive_id} synced with its RecordGroup",
            )
            record = await graph_provider.get_record_by_external_id(connector_id, file_id)
            assert str(record.external_record_group_id) == drive_id, (
                f"Shared Drive file filed under {record.external_record_group_id!r}, not {drive_id!r}"
            )
        finally:
            await delete_shared_drive(drive_individual_datasource, drive_id, admin_drive=await _admin_drive())

    @pytest.mark.order(2)
    async def test_tc_drv_ind_sd_002_item_in_shared_drive_owned_by_another_user(
        self,
        drive_individual_unfiltered_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        leftover_shared_drives: set[str],
    ) -> None:
        """TC-DRV-IND-SD-002 (GD N17): an item another user owns in a Shared Drive the user joined syncs."""
        second = os.getenv(ENV_SECOND_USER, "").strip()
        if not second:
            pytest.skip(f"{ENV_SECOND_USER} not set")
        state = drive_individual_unfiltered_connector
        connector_id = state["connector_id"]
        user_email = state["test_user_email"]

        b_drive = await _impersonate(second)
        drive_id = await create_shared_drive(b_drive, f"pipeshub-it-ind-sd-b-{uuid.uuid4().hex[:6]}")
        leftover_shared_drives.add(drive_id)
        state["b_drive_id"] = drive_id
        state["b_datasource"] = b_drive
        file_id = await create_drive_text_file(b_drive, "owned-by-b.txt", parent_id=drive_id, content="b owns this\n")
        state["b_file_id"] = file_id
        share_drive_item_with_user(b_drive, drive_id, user_email, role="writer")

        await sync_until(
            pipeshub_client, graph_provider, connector_id,
            record_present(graph_provider, connector_id, file_id),
            description=f"B's file in Shared Drive {drive_id} synced for member {user_email}",
        )

    @pytest.mark.order(3)
    async def test_tc_drv_ind_sd_003_removed_from_shared_drive_drops_items(
        self,
        drive_individual_unfiltered_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        leftover_shared_drives: set[str],
    ) -> None:
        """TC-DRV-IND-SD-003 (GD PE5): removing the user from a Shared Drive removes its items for them."""
        state = drive_individual_unfiltered_connector
        drive_id = state.get("b_drive_id")
        if not drive_id:
            pytest.skip("TC-DRV-IND-SD-002 did not create B's Shared Drive")
        connector_id = state["connector_id"]
        b_drive: GoogleDriveDataSource = state["b_datasource"]
        try:
            removed = await unshare_drive_item_from_user(b_drive, drive_id, state["test_user_email"])
            assert removed, f"{state['test_user_email']} had no membership on Shared Drive {drive_id}"

            async def _gone() -> bool:
                record = await graph_provider.get_record_by_external_id(connector_id, state["b_file_id"])
                group = await graph_provider.get_record_group_by_external_id(connector_id, drive_id)
                return record is None and group is None

            try:
                await sync_until(
                    pipeshub_client, graph_provider, connector_id, _gone,
                    description=f"Shared Drive {drive_id} and its file gone after the user was removed",
                )
            except TimeoutError:
                record = await graph_provider.get_record_by_external_id(connector_id, state["b_file_id"])
                group = await graph_provider.get_record_group_by_external_id(connector_id, drive_id)
                raise AssertionError(
                    f"after removal from Shared Drive {drive_id}: file present={record is not None}, "
                    f"RecordGroup present={group is not None}"
                ) from None
        finally:
            await delete_shared_drive(b_drive, drive_id, admin_drive=await _admin_drive())
            leftover_shared_drives.discard(drive_id)

    @pytest.mark.order(4)
    async def test_tc_drv_ind_sc_001_shortcut_into_non_member_shared_drive(
        self,
        drive_individual_unfiltered_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-IND-SC-001 (GD N15): a shortcut to a file in a Shared Drive the user is not in syncs the file."""
        third = os.getenv(ENV_THIRD_USER, "").strip()
        if not third:
            pytest.skip(f"{ENV_THIRD_USER} not set (needs a Workspace member who owns a Shared Drive)")
        state = drive_individual_unfiltered_connector
        connector_id = state["connector_id"]
        user_email = state["test_user_email"]

        c_drive = await _impersonate(third)
        drive_id = await create_shared_drive(c_drive, f"pipeshub-it-ind-sd-c-{uuid.uuid4().hex[:6]}")
        holder = None
        try:
            target = await create_drive_text_file(c_drive, "c-target.txt", parent_id=drive_id, content="target\n")
            share_drive_item_with_user(c_drive, target, user_email)
            holder = await create_drive_folder(
                drive_individual_datasource, f"pipeshub-it-ind-shortcut-{uuid.uuid4().hex[:6]}"
            )
            await create_drive_shortcut(drive_individual_datasource, target, holder, "c-target-shortcut")

            await sync_until(
                pipeshub_client, graph_provider, connector_id,
                record_present(graph_provider, connector_id, target),
                description=f"shortcut target {target} in non-member Shared Drive synced",
            )
        finally:
            if holder:
                await delete_drive_folder(drive_individual_datasource, holder)
            await delete_shared_drive(c_drive, drive_id, admin_drive=await _admin_drive())


class TestDriveIndividualRevokedToken:

    async def test_tc_drv_ind_auth_001_revoked_token_fails_connection_test(
        self,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-DRV-IND-AUTH-001 (GD N23): with a revoked token the connection test fails and the connector shows it.

        Google answers a revoked refresh token and a made-up one the same way
        (``invalid_grant``), so a garbage token pair stands in for a revoked one
        without revoking the real token in ``.env.refresh_tokens``.
        """
        instance = pipeshub_client.create_connector(
            connector_type="Drive",
            instance_name=f"drive-personal-revoked-{uuid.uuid4().hex[:8]}",
            scope="personal",
            config={
                "auth": {
                    "clientId": os.environ[ENV_CLIENT_ID].strip(),
                    "clientSecret": os.environ[ENV_CLIENT_SECRET].strip(),
                },
                "filters": connector_filters(),
            },
            auth_type="OAUTH",
        )
        connector_id = instance.connector_id
        try:
            await inject_connector_credentials(
                connector_id, access_token="ya29.revoked-by-test", refresh_token="1//revoked-by-test"
            )
            toggle_error: str | None = None
            try:
                pipeshub_client.toggle_sync(connector_id, enable=True)
            except PipeshubClientError as e:
                toggle_error = str(e)

            deadline = time.monotonic() + _AUTH_FAILURE_WAIT_SEC
            connector = pipeshub_client.get_connector(connector_id)
            while toggle_error is None and time.monotonic() < deadline:
                if connector.get("isAuthenticated") is False or not connector.get("isActive"):
                    break
                time.sleep(5)
                connector = pipeshub_client.get_connector(connector_id)
            logger.info(
                "revoked-token connector: toggle_error=%r isActive=%r isAuthenticated=%r status=%r",
                toggle_error, connector.get("isActive"), connector.get("isAuthenticated"), connector.get("status"),
            )
            assert toggle_error is not None or connector.get("isAuthenticated") is False, (
                "a connector holding a revoked token passed the connection test and still reports "
                f"isActive={connector.get('isActive')!r} isAuthenticated={connector.get('isAuthenticated')!r} "
                f"after {_AUTH_FAILURE_WAIT_SEC}s"
            )
        finally:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
            except Exception as e:  # noqa: BLE001
                logger.warning("disable revoked-token connector: %s", e)
            pipeshub_client.delete_connector(connector_id)
