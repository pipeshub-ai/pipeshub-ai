# pyright: ignore-file

"""Scenario-matrix plumbing shared by the bucket, container and share connectors.

S3, MinIO, GCS, Azure Blob, Azure Files and SMB differ only in how a file is
written, renamed and deleted at the source, so each gets a small subclass of
``StorageScenarioAdapter`` and the rest lives here.

Every item sits in this run's folder, under ``kept/`` except the one the filter
scenario excludes, which sits under ``filtered/``. The connector starts scoped to
the run folder (the "Folders" sync filter); the exclusion filter narrows it to
``kept/``. Narrowing that filter is the one sync filter all six connectors
share, and all six remove what falls outside it (``clean_up_scope`` in
``core/registry/folder_scope.py``, or the prune of unseen items for Azure Files
and SMB), which is what the matrix requires of every connector.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from connector_lifecycle import (
    create_connector_and_await_sync,
    destructor,
    ensure_resource_exists,
)
from connectors.scenario_matrix import Role, ScenarioAdapter, SourceItem
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.run_folder import folder_filter, new_run_folder
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

KEPT = "kept"
EXCLUDED = "filtered"

# Every connector here writes one app-level permission per item and reads no
# per-object ACL, so a share made at the source changes nobody's access.
APP_LEVEL_PERMISSIONS = (
    "the connector gives every item the same app-level permission (the owner for a "
    "personal connector, the whole org for a team one) and never reads per-object "
    "ACLs, so a share made at the source cannot change who sees an item"
)


class StorageScenarioAdapter(ScenarioAdapter):
    """Source actions against one bucket, container or share. Subclasses do the I/O."""

    def __init__(self, *, storage: Any, resource: str, folder: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.storage = storage
        self.resource = resource
        self.folder = folder

    def _write(self, key: str, text: str) -> None:
        raise NotImplementedError

    def _delete(self, key: str) -> None:
        raise NotImplementedError

    def _rename(self, key: str, new_key: str) -> None:
        raise NotImplementedError

    def key_for(self, role: Role, token: str, *, name: str | None = None) -> str:
        area = EXCLUDED if role is Role.FILTERED else KEPT
        return f"{self.folder}{area}/{name or f'{role.value}-{token}.txt'}"

    @staticmethod
    def _item(role: Role, key: str, text: str, token: str) -> SourceItem:
        return SourceItem(
            role=role, key=key, record_name=key.rsplit("/", 1)[-1], text=text, token=token,
        )

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        key = self.key_for(role, token)
        self._write(key, text)
        return self._item(role, key, text, token)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self._write(item.key, text)
        return self._item(item.role, item.key, text, token)

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        new_key = self.key_for(item.role, item.token, name=f"renamed-{item.token}.txt")
        self._rename(item.key, new_key)
        return self._item(item.role, new_key, item.text, item.token)

    async def delete_item(self, item: SourceItem) -> None:
        self._delete(item.key)

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        kept_folder = f"{self.folder}{KEPT}/"
        assert not excluded.key.startswith(kept_folder)
        assert all(k.key.startswith(kept_folder) for k in kept)
        # The save replaces the whole sync-filter block, so this is the full scope.
        return folder_filter(kept_folder)


class S3CompatibleAdapter(StorageScenarioAdapter):
    """Objects written, copied and deleted through boto3."""

    source = "S3"

    def _write(self, key: str, text: str) -> None:
        self.storage.upload_object(self.resource, key, text.encode(), "text/plain")

    def _delete(self, key: str) -> None:
        self.storage.delete_object(self.resource, key)

    def _rename(self, key: str, new_key: str) -> None:
        # S3 has no rename: copy then delete. The copy keeps the ETag, which is how
        # the connector recognises the move and keeps the same record.
        self.storage.rename_object(self.resource, key, new_key)


@asynccontextmanager
async def storage_matrix(
    adapter_cls: type[StorageScenarioAdapter],
    *,
    storage: Any,
    resource: str,
    client: PipeshubClient,
    graph: GraphProviderProtocol,
    connector_type: str,
    connector_config: dict[str, Any],
    scope: str = "personal",
) -> AsyncGenerator[StorageScenarioAdapter, None]:
    """A new connector scoped to a fresh run folder holding one seed file; torn down after."""
    ensure_resource_exists(storage, resource)
    folder = new_run_folder()
    state: dict[str, Any] = {"resource_name": resource, "folder": folder}
    adapter = adapter_cls(
        storage=storage, resource=resource, folder=folder,
        client=client, graph=graph, connector_id="",
    )
    try:
        adapter._write(f"{folder}{KEPT}/seed.txt", "Scenario matrix seed file.")
        await create_connector_and_await_sync(
            client,
            graph,
            state,
            connector_type=connector_type,
            connector_name=f"{connector_type.lower().replace(' ', '-')}-matrix-{uuid.uuid4().hex[:8]}",
            connector_config={**connector_config, "filters": folder_filter(folder)},
            expected_records=1,
            scope=scope,
        )
        adapter.connector_id = state["connector_id"]
        await wait_for_sync_completion(client, graph, adapter.connector_id, sync_start_timeout=0)
        yield adapter
    finally:
        if state.get("connector_id"):
            await destructor(storage, client, graph, state, connector_type=connector_type)
        else:
            storage.clear_objects(resource, folder)
