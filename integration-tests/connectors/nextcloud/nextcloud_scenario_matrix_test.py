# pyright: ignore-file

"""Nextcloud in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are plain files in this run's folder on the ``nextcloud-source`` container,
written and changed over WebDAV. The Nextcloud matrix also carries the one
scheduled-sync check: the connector allows a one-minute schedule, and its
incremental sync reads Nextcloud's activity feed, so a pickup is quick.
"""

from __future__ import annotations

import os
import posixpath
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from connectors.nextcloud.nextcloud_source_helper import NextcloudSourceHelper
from connectors.scenario_matrix import (
    FILTER_KEEPS_EXCLUDED_ITEM,
    Action,
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from connector_lifecycle import create_connector_and_await_sync, destructor
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

# The exclusion filter drops this extension; every other item is a .txt file.
FILTERED_EXTENSION = "md"


class NextcloudAdapter(ScenarioAdapter):
    source = "Nextcloud"

    def __init__(self, *, nextcloud: NextcloudSourceHelper, folder: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.nextcloud = nextcloud
        self.folder = folder

    def _item(self, role: Role, path: str, text: str, token: str) -> SourceItem:
        return SourceItem(
            role=role,
            key=path,
            record_name=posixpath.basename(path),
            text=text,
            token=token,
            external_id=self.nextcloud.file_id(path),
        )

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        extension = FILTERED_EXTENSION if role is Role.FILTERED else "txt"
        path = f"{self.folder}/{role.value}-{token}.{extension}"
        self.nextcloud.put(path, text)
        return self._item(role, path, text, token)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.nextcloud.put(item.key, text)
        return SourceItem(
            role=item.role, key=item.key, record_name=item.record_name, text=text,
            token=token, external_id=item.external_id,
        )

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        new_path = f"{self.folder}/renamed-{item.token}.txt"
        self.nextcloud.move(item.key, new_path)
        return SourceItem(
            role=item.role, key=new_path, record_name=posixpath.basename(new_path),
            text=item.text, token=item.token, external_id=item.external_id,
        )

    async def delete_item(self, item: SourceItem) -> None:
        self.nextcloud.delete(item.key)

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        assert excluded.record_name.endswith(f".{FILTERED_EXTENSION}")
        assert not any(k.record_name.endswith(f".{FILTERED_EXTENSION}") for k in kept)
        return {
            "sync": {
                "values": {
                    "file_extensions": {
                        "operator": "not_in",
                        "value": [FILTERED_EXTENSION],
                        "type": "multiselect",
                    }
                }
            }
        }

    async def cleanup(self) -> None:
        self.nextcloud.delete(self.folder)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    nextcloud_source: NextcloudSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[NextcloudAdapter, None]:
    # The sync user belongs to these suites only; a folder an interrupted run
    # left behind would otherwise become records of this connector too.
    for path in nextcloud_source.list(""):
        if path.count("/") == 1 and path.endswith("/") and path.startswith("it-"):
            nextcloud_source.delete(path)

    folder = f"it-mx-{uuid.uuid4().hex[:8]}"
    nextcloud_source.put(f"{folder}/seed.txt", "Scenario matrix seed file.")
    state: dict[str, Any] = {"resource_name": nextcloud_source.user, "folder": folder}
    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Nextcloud",
        connector_name=f"nextcloud-matrix-{uuid.uuid4().hex[:8]}",
        connector_config={
            "auth": {
                "baseUrl": os.getenv("NEXTCLOUD_CONNECTOR_URL", "http://nextcloud-source"),
                "username": nextcloud_source.user,
                "password": nextcloud_source.password,
            }
        },
        expected_records=2,
        scope="personal",
    )
    adapter = NextcloudAdapter(
        nextcloud=nextcloud_source,
        folder=folder,
        client=pipeshub_client,
        graph=graph_provider,
        connector_id=state["connector_id"],
    )
    try:
        yield adapter
    finally:
        await destructor(
            nextcloud_source, pipeshub_client, graph_provider, state, connector_type="Nextcloud"
        )


@pytest.mark.integration
@pytest.mark.nextcloud
class TestNextcloudScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Nextcloud"
    KNOWN_BUGS = {"filter_change": FILTER_KEEPS_EXCLUDED_ITEM}
    SCHEDULED_SYNC = True
    UNSUPPORTED = {
        Action.CHANGE_PERMISSION.value: (
            "the connector writes only the owner's permission: it never reads Nextcloud "
            "shares (_get_file_shares in sources/nextcloud/connector.py has no caller), and "
            "it is registered for personal scope, so a share cannot reach a second PipesHub user"
        ),
        Action.SET_INDEXING.value: (
            "the connector declares no indexing filters (no enable_manual_sync), so indexing "
            "cannot be switched to manual"
        ),
    }
