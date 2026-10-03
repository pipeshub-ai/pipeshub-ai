# pyright: ignore-file

"""Google Drive Workspace in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are plain-text files in the Workspace test user's My Drive, under a tree
this module creates: ``<root>/main`` holds every item except the one the filter
scenario excludes, which lives in ``<root>/filtered``. The connector is scoped to
``<root>`` with ``folder_ids``; the exclusion filter narrows it to ``<root>/main``.

Search runs as the Workspace test user (the files' owner), logged in to PipesHub
under that address; the share goes to the second Workspace user the permissions
suite already uses.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from googleapiclient.http import MediaInMemoryUpload  # type: ignore[import-not-found]

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (
    ENV_SECOND_USER,
    create_drive_folder,
    create_drive_text_file,
    delete_drive_folder,
    ensure_pipeshub_user_exists,
    rename_drive_item,
    require_drive_workspace_env,
    share_drive_item_with_user,
    unshare_drive_item,
    wait_until_drive_files_listed,
)
from connectors.scenario_matrix import (
    FILTER_KEEPS_EXCLUDED_ITEM,
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from helper.clients.users_client import UsersClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import async_poll_until, wait_for_sync_completion
from helper.second_user import SecondUser, log_in_existing_user, log_out_existing_user
from helper.source_credentials import source_unavailable
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("drive-workspace-matrix")

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "300"))


class DriveWorkspaceAdapter(ScenarioAdapter):
    source = "Google Drive Workspace"

    def __init__(
        self, *, drive: GoogleDriveDataSource, main_id: str, filtered_id: str, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.drive = drive
        self.main_id = main_id
        self.filtered_id = filtered_id

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        parent = self.filtered_id if role is Role.FILTERED else self.main_id
        name = f"{role.value}-{token}.txt"
        file_id = await create_drive_text_file(self.drive, name, parent_id=parent, content=text)
        # The connector reads the user-corpus listing, whose index lags a create.
        await wait_until_drive_files_listed(self.drive, [file_id])
        return SourceItem(role=role, key=file_id, record_name=name, text=text, token=token,
                          external_id=file_id)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.drive.client.files().update(  # type: ignore[attr-defined]
            fileId=item.key,
            media_body=MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain"),
            supportsAllDrives=True,
        ).execute()
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name, text=text,
                          token=token, external_id=item.external_id, extra=dict(item.extra))

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        name = f"renamed-{item.token}.txt"
        await rename_drive_item(self.drive, item.key, name)
        return SourceItem(role=item.role, key=item.key, record_name=name, text=item.text,
                          token=item.token, external_id=item.external_id, extra=dict(item.extra))

    async def change_permission(self, item: SourceItem, sharee: SecondUser, *, grant: bool) -> None:
        if grant:
            item.extra["permission_id"] = share_drive_item_with_user(
                self.drive, item.key, sharee.email
            )
        else:
            await unshare_drive_item(self.drive, item.key, item.extra.pop("permission_id"))

    async def delete_item(self, item: SourceItem) -> None:
        await self.drive.files_delete(fileId=item.key, supportsAllDrives=True)

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        return {
            "sync": {
                "values": {
                    "folder_ids": {"operator": "in", "type": "list", "value": [self.main_id]}
                }
            }
        }


async def _wait_for_active_user_in_graph(graph_provider: GraphProviderProtocol, email: str) -> None:
    """A grant to an address PipesHub does not know yet is skipped, not deferred."""

    async def _active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is not False else None

    await async_poll_until(_active, timeout=120, interval=2,
                           description=f"active PipesHub graph user for {email}")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    drive_workspace_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[DriveWorkspaceAdapter, None]:
    sa_json, admin_email, test_user = require_drive_workspace_env()
    second_email = os.getenv(ENV_SECOND_USER, "").strip()
    if not second_email:
        source_unavailable(
            "No second Workspace user is configured, so the share scenario has nobody to "
            "share with.",
            secrets=[ENV_SECOND_USER],
        )

    drive = drive_workspace_datasource
    connector_id: str | None = None
    root_id: str | None = None
    logged_in: list[SecondUser] = []
    try:
        users = {}
        for email in (test_user, second_email):
            users[email], _ = ensure_pipeshub_user_exists(users_client, email)
            await _wait_for_active_user_in_graph(graph_provider, email)

        root_id = await create_drive_folder(drive, f"pipeshub-it-drive-mx-{uuid.uuid4().hex[:8]}")
        main_id = await create_drive_folder(drive, "main", parent_id=root_id)
        filtered_id = await create_drive_folder(drive, "filtered", parent_id=root_id)
        await wait_until_drive_files_listed(drive, [root_id, main_id, filtered_id])

        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=f"drive-ws-matrix-{uuid.uuid4().hex[:8]}",
            scope="team",
            config={
                "auth": {"adminEmail": admin_email, "serviceAccountJson": sa_json},
                "filters": {
                    "sync": {
                        "values": {
                            "folder_ids": {"operator": "in", "type": "list", "value": [root_id]}
                        }
                    }
                },
            },
            auth_type="CUSTOM",
        )
        connector_id = instance.connector_id
        assert connector_id, "Connector must have a valid ID"
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, min_records=1, timeout=_SYNC_TIMEOUT_SEC,
        )

        owner = log_in_existing_user(pipeshub_client, users[test_user], test_user)
        logged_in.append(owner)
        sharee = log_in_existing_user(pipeshub_client, users[second_email], second_email)
        logged_in.append(sharee)

        yield DriveWorkspaceAdapter(
            drive=drive,
            main_id=main_id,
            filtered_id=filtered_id,
            client=pipeshub_client,
            graph=graph_provider,
            connector_id=connector_id,
            owner=owner,
            sharee=sharee,
        )
    finally:
        for user in logged_in:
            log_out_existing_user(pipeshub_client, user)
        if connector_id:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
                pipeshub_client.delete_connector(connector_id)
                pipeshub_client.wait(25)
                await graph_provider.assert_all_records_cleaned(
                    connector_id,
                    timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")),
                )
            except Exception as e:  # noqa: BLE001 - teardown must not mask the test result
                logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)
        # Deleting the tree removes every file in it and every share made on them.
        # root_id stays None when setup failed before the tree was made.
        if root_id:
            await delete_drive_folder(drive, root_id)


@pytest.mark.integration
@pytest.mark.google_drive_workspace
class TestDriveWorkspaceScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Google Drive Workspace"
    KNOWN_BUGS = {
        "filter_change": FILTER_KEEPS_EXCLUDED_ITEM,
        "incr_delete": (
            "A file deleted from My Drive keeps its record and its vectors: the changes "
            "feed reports it as removed, and sources/google/drive/team/connector.py "
            "(the `if is_removed:` branch of the user changes loop, ~line 2548) only calls "
            "delete_permission_from_record for that user. Nothing deletes the record; only "
            "the Shared Drive loop (~line 3144) marks removed items is_deleted."
        ),
    }
