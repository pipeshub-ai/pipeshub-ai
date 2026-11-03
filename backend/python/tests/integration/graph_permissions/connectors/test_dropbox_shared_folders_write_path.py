"""Dropbox shared folders written by the real processor into both graphs (fix H).

A shared folder is one record group under its own name, granted to its members;
the items in it carry no grant of their own and inherit it. Streaming finds who
can open a file through that inheritance: the users granted above it, or the
groups granted above it whose members the connector expands.
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox.shared_folders import SharedFolder
from app.models.entities import AppUserGroup, FileRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType

from ..processor_harness import build_processor
from ..write_path_support import ORG, _seed_app, _seed_user, _suffix

pytestmark = pytest.mark.integration


def _connector(processor, connector_id: str) -> DropboxConnector:
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        connector = DropboxConnector(
            logger=logging.getLogger("dropbox-shared-folder-it"), data_entities_processor=processor,
            data_store_provider=MagicMock(), config_service=AsyncMock(), connector_id=connector_id,
            scope="team", created_by="admin",
        )
    connector.data_source = AsyncMock()
    return connector


def _file(name: str, connector_id: str, group: str, parent: FileRecord | None = None, *, is_file: bool = True) -> FileRecord:
    return FileRecord(
        id=str(uuid.uuid4()), org_id=ORG, record_name=name, record_type=RecordType.FILE,
        record_group_type=RecordGroupType.DRIVE.value, external_record_group_id=group,
        external_record_id=f"id:{_suffix()}", version=0, origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.DROPBOX, connector_id=connector_id,
        parent_external_record_id=parent.external_record_id if parent else None,
        parent_record_type=RecordType.FILE if parent else None,
        is_file=is_file, path=f"/{name}", extension="txt" if is_file else None,
        mime_type="text/plain" if is_file else "text/directory",
    )


async def _setup(backend) -> tuple[DataSourceEntitiesProcessor, str, DropboxConnector]:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id, name="Dropbox", app_type=Connectors.DROPBOX.value, app_group="Dropbox")
    return processor, connector_id, _connector(processor, connector_id)


async def test_a_shared_folder_is_one_named_group_and_its_files_inherit_it(backend) -> None:
    processor, connector_id, connector = await _setup(backend)
    viewer_email = f"viewer-{_suffix()}@example.com"
    viewer_id = await _seed_user(backend, viewer_email)
    group = AppUserGroup(source_user_group_id=f"g:{_suffix()}", app_name=Connectors.DROPBOX, connector_id=connector_id, name="Everyone")
    await processor.on_new_user_groups([(group, [])])
    connector._shared_folder_members = AsyncMock(return_value=[
        Permission(email=viewer_email, type=PermissionType.READ, entity_type=EntityType.USER),
        Permission(external_id=group.source_user_group_id, type=PermissionType.READ, entity_type=EntityType.GROUP),
    ])
    folder_id = f"sf-{_suffix()}"
    await connector._sync_shared_folder_groups({folder_id: SharedFolder(
        folder_id=folder_id, name="Korean files", parent_id=None, inherits=False, is_team_folder=False, access={"dbmid:a": "owner"},
    )})
    sub = _file("sub", connector_id, folder_id, is_file=False)
    doc = _file("ko.md", connector_id, folder_id, parent=sub)
    await processor.on_new_records([(sub, []), (doc, [])])

    stored = await backend.provider.get_record_group_by_external_id(connector_id, folder_id)
    assert stored is not None and stored.name == "Korean files", "named after the folder, not its namespace id"
    assert await backend.edge_exists(
        CollectionNames.PERMISSION.value, CollectionNames.USERS.value, viewer_id, CollectionNames.RECORD_GROUPS.value, stored.id,
    )
    assert not await backend.edge_exists(
        CollectionNames.PERMISSION.value, CollectionNames.USERS.value, viewer_id, CollectionNames.RECORDS.value, doc.id,
    ), "no grant is copied onto an item"
    users = await backend.provider.get_users_with_permission_to_node(doc.id, CollectionNames.RECORDS.value)
    assert viewer_email in {u.email for u in users}, "streaming finds the member through inheritance"
    groups = await processor.get_groups_with_access_to_record(doc)
    assert group.source_user_group_id in {g.source_user_group_id for g in groups}


async def test_a_nested_folder_inherits_only_when_dropbox_says_it_does(backend) -> None:
    processor, connector_id, connector = await _setup(backend)
    connector._shared_folder_members = AsyncMock(return_value=[])
    top, open_child, closed_child = (f"sf-{_suffix()}" for _ in range(3))

    def shared(folder_id: str, name: str, parent: str | None, inherits: bool) -> SharedFolder:
        return SharedFolder(folder_id=folder_id, name=name, parent_id=parent, inherits=inherits,
                            is_team_folder=False, access={"dbmid:a": "owner"})

    await connector._sync_shared_folder_groups({
        open_child: shared(open_child, "Open", top, True),
        top: shared(top, "Top", None, False),
        closed_child: shared(closed_child, "Restricted", top, False),
    })

    groups = {fid: await backend.provider.get_record_group_by_external_id(connector_id, fid) for fid in (top, open_child, closed_child)}
    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.RECORD_GROUPS.value, groups[open_child].id,
        CollectionNames.RECORD_GROUPS.value, groups[top].id,
    )
    assert not await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.RECORD_GROUPS.value, groups[closed_child].id,
        CollectionNames.RECORD_GROUPS.value, groups[top].id,
    ), "a no_inherit folder does not take its parent's members"
