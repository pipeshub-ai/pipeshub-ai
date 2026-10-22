"""Dropbox content edits and deletes, written by the real processor into both graphs.

Found in a live run of PR #3880: a content edit wiped the file's grants
(DB-07b), and deletes never left the graph (DB-09, DP-03/04).
"""

import uuid

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.sources.dropbox.deletes import apply_deleted_entry
from app.models.entities import (
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType

from ..processor_harness import build_processor
from ..write_path_support import ORG, _seed_app, _seed_user, _suffix

pytestmark = pytest.mark.integration

MEMBER = "dbmid:member"


async def _drive(backend, processor, connector_id: str) -> None:
    await _seed_app(backend, connector_id, name="Dropbox", app_type=Connectors.DROPBOX.value, app_group="Dropbox")
    group = RecordGroup(
        org_id=ORG, name="Member drive", external_group_id=MEMBER, connector_name=Connectors.DROPBOX,
        connector_id=connector_id, group_type=RecordGroupType.DRIVE, inherit_permissions=True,
    )
    await processor.on_new_record_groups([(group, [])])


def _item(path: str, *, connector_id: str, parent: FileRecord | None = None, is_file: bool = True) -> FileRecord:
    return FileRecord(
        id=str(uuid.uuid4()), org_id=ORG, record_name=path.rsplit("/", 1)[-1],
        record_type=RecordType.FILE, record_group_type=RecordGroupType.DRIVE.value,
        external_record_group_id=MEMBER, external_record_id=f"id:{_suffix()}", version=0,
        origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.DROPBOX, connector_id=connector_id,
        parent_external_record_id=parent.external_record_id if parent else None,
        parent_record_type=RecordType.FILE if parent else None,
        is_file=is_file, path=path, extension="txt" if is_file else None,
        mime_type="text/plain" if is_file else "text/directory",
    )


async def _exists(backend, connector_id: str, record: FileRecord) -> bool:
    return await backend.provider.get_record_by_external_id(connector_id, record.external_record_id) is not None


async def test_a_content_edit_keeps_the_files_own_grants(backend) -> None:
    """DB-07b: the edited file kept abhishek's READER edge in Dropbox, and lost it here."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _drive(backend, processor, connector_id)
    email = f"viewer-{_suffix()}@example.com"
    user_id = await _seed_user(backend, email)
    record = _item("/pr3880/single.txt", connector_id=connector_id)
    record.rewrite_permissions = True
    await processor.on_new_records([(record, [Permission(email=email, type=PermissionType.READ, entity_type=EntityType.USER)])])

    async def granted() -> bool:
        return await backend.edge_exists(
            CollectionNames.PERMISSION.value, CollectionNames.USERS.value, user_id,
            CollectionNames.RECORDS.value, record.id,
        )

    assert await granted()
    record.version += 1
    await processor.on_record_content_update(record)
    assert await granted(), "a content edit must not take the file's grants away"


async def test_records_are_found_by_the_path_their_sync_wrote(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _drive(backend, processor, connector_id)
    folder = _item("/pr3880/open", connector_id=connector_id, is_file=False)
    await processor.on_new_records([(folder, [])])

    found = await backend.provider.get_records_by_file_path(connector_id, "/pr3880/open")
    assert [r.external_record_id for r in found] == [folder.external_record_id]
    assert await backend.provider.get_records_by_file_path(f"other-{connector_id}", "/pr3880/open") == []


async def test_a_deleted_folder_leaves_with_its_subtree_and_spares_its_sibling(backend) -> None:
    """DB-09 / DP-04: the folder and the file in it stayed listed after Dropbox deleted them."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _drive(backend, processor, connector_id)
    folder = _item("/pr3880/restricted", connector_id=connector_id, is_file=False)
    child = _item("/pr3880/restricted/r.txt", connector_id=connector_id, parent=folder)
    sibling = _item("/pr3880/a.txt", connector_id=connector_id)
    await processor.on_new_records([(folder, []), (child, []), (sibling, [])])

    async def gone(_record) -> bool:
        return False

    done = await apply_deleted_entry(processor, connector_id, "/pr3880/restricted", gone, processor.logger)

    assert done is True
    assert not await _exists(backend, connector_id, folder)
    assert not await _exists(backend, connector_id, child), "Dropbox may report only the folder; its file goes too"
    assert await _exists(backend, connector_id, sibling)


async def test_a_delete_reported_for_a_move_keeps_the_record(backend) -> None:
    """Dropbox reports a move as a delete at the old path; the item's id still resolves."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _drive(backend, processor, connector_id)
    moved = _item("/pr3880/sub/b.txt", connector_id=connector_id)
    await processor.on_new_records([(moved, [])])

    async def still_there(_record) -> bool:
        return True

    assert await apply_deleted_entry(processor, connector_id, "/pr3880/sub/b.txt", still_there, processor.logger)
    assert await _exists(backend, connector_id, moved)


async def test_a_member_added_by_an_event_is_a_user_grant_on_the_group(backend) -> None:
    """DB-03: a member added between full syncs got a GROUP-typed edge, which access queries never follow."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _drive(backend, processor, connector_id)
    email = f"member-{_suffix()}@example.com"
    user_id = await _seed_user(backend, email)
    group = AppUserGroup(source_user_group_id=f"g:{_suffix()}", app_name=Connectors.DROPBOX, connector_id=connector_id, name="Testers")
    await processor.on_new_user_groups([(group, [])])

    assert await processor.on_user_group_member_added(group.source_user_group_id, email, PermissionType.WRITE, connector_id)

    stored = await backend.provider.get_user_group_by_external_id(connector_id, group.source_user_group_id)
    edge = await backend.provider.get_edge(
        user_id, CollectionNames.USERS.value, stored.id, CollectionNames.GROUPS.value, CollectionNames.PERMISSION.value,
    )
    assert edge is not None and edge["type"] == "USER"
