"""Dropbox QA fixes driven through the real write path, on both backends.

* STORAGE-KB-02 / BASE-01: a stub group main wrote (``orgId ""``, no edges) is
  repaired when a record is filed under it again.
* STORAGE-KB-01: a shared folder's group carries its members, and a file whose
  members are all inherited reaches them through the group.
* STORAGE-KB-04: the path lookup a Dropbox deletion resolves through.
* R1-05: the people a deletion one member saw is checked with are the ones the
  graph grants the file, through the shared folder it inherits from.
"""

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import AppUserGroup, FileRecord, RecordGroup, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType

from .processor_harness import build_processor
from .test_write_path import ORG, _seed_app, _seed_user, _suffix, backend  # noqa: F401

pytestmark = pytest.mark.integration


def _group(external_id: str, connector_id: str, *, org_id: str = ORG, name: str | None = None) -> RecordGroup:
    return RecordGroup(
        org_id=org_id, name=name or external_id, external_group_id=external_id,
        connector_name=Connectors.DROPBOX, connector_id=connector_id, group_type=RecordGroupType.DRIVE,
    )


def _file(external_id: str, connector_id: str, group_external: str, path: str, *, folder: bool = False) -> FileRecord:
    return FileRecord(
        org_id=ORG, record_name=path.rsplit("/", 1)[-1], external_record_id=external_id,
        record_type=RecordType.FILE, external_record_group_id=group_external,
        record_group_type=RecordGroupType.DRIVE.value, version=0, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.DROPBOX, connector_id=connector_id, is_file=not folder, path=path,
        mime_type="text/directory" if folder else "text/plain",
    )


async def test_a_stub_main_left_detached_is_hung_under_the_app_on_reuse(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    stub = _group(f"ns-{_suffix()}", connector_id, org_id="")
    await backend.provider.batch_upsert_nodes(
        [stub.to_arango_base_record_group()], collection=CollectionNames.RECORD_GROUPS.value,
    )

    await processor.on_new_records([(_file(f"id:{_suffix()}", connector_id, stub.external_group_id, "/a.txt"), [])])

    stored = await backend.provider.get_record_group_by_external_id(connector_id, stub.external_group_id)
    assert stored.id == stub.id and stored.org_id == ORG
    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value, CollectionNames.APPS.value, connector_id,
        CollectionNames.RECORD_GROUPS.value, stub.id,
    )
    for to_collection, to_id in ((CollectionNames.APPS.value, connector_id), (CollectionNames.ORGS.value, ORG)):
        assert await backend.edge_exists(
            CollectionNames.BELONGS_TO.value, CollectionNames.RECORD_GROUPS.value, stub.id, to_collection, to_id,
        )
    assert not await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.RECORD_GROUPS.value, stub.id,
        CollectionNames.APPS.value, connector_id,
    ), "a repaired stub must not open to everyone who can reach the App"


async def test_a_shared_folder_group_carries_its_owner_to_a_file_with_no_grant_of_its_own(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    email = f"owner-{_suffix()}@example.com"
    user_id = await _seed_user(backend, email)
    namespace = f"ns-{_suffix()}"
    # The stub the processor would have minted first, then the folder announced.
    await processor.on_new_records([(_file(f"id:{_suffix()}", connector_id, namespace, "/x.txt"), [])])
    group = _group(namespace, connector_id, name="Test Folder")
    await processor.on_new_record_groups([(group, [Permission(email=email, type=PermissionType.OWNER, entity_type=EntityType.USER)])])
    record = _file(f"id:{_suffix()}", connector_id, namespace, "/cover letter.pdf")
    await processor.on_new_records([(record, [])])

    stored = await backend.provider.get_record_group_by_external_id(connector_id, namespace)
    assert stored.name == "Test Folder"
    assert await backend.edge_exists(
        CollectionNames.PERMISSION.value, CollectionNames.USERS.value, user_id,
        CollectionNames.RECORD_GROUPS.value, stored.id,
    )
    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.RECORDS.value, record.id,
        CollectionNames.RECORD_GROUPS.value, stored.id,
    )


async def test_the_path_lookup_finds_a_deleted_folder_and_what_is_below_it(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    home, other = f"dbmid-{_suffix()}", f"ns-{_suffix()}"
    await processor.on_new_record_groups([(_group(home, connector_id), []), (_group(other, connector_id), [])])
    folder = _file(f"id:{_suffix()}", connector_id, home, "/docs", folder=True)
    child = _file(f"id:{_suffix()}", connector_id, home, "/docs/a.txt")
    deep = _file(f"id:{_suffix()}", connector_id, home, "/docs/sub/b.txt")
    sibling = _file(f"id:{_suffix()}", connector_id, home, "/docsx/c.txt")
    elsewhere = _file(f"id:{_suffix()}", connector_id, other, "/docs/a.txt")
    await processor.on_new_records([(r, []) for r in (folder, child, deep, sibling, elsewhere)])

    found = await backend.provider.get_file_records_under_path(connector_id, home, "/docs")
    single = await backend.provider.get_file_records_under_path(connector_id, home, "/docs/a.txt")

    assert [r.id for r in found] == [deep.id, child.id, folder.id]
    assert [r.external_record_id for r in single] == [child.external_record_id]


async def test_a_file_in_a_shared_folder_names_the_folder_members_a_deletion_asks(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    owner, member = f"owner-{_suffix()}@example.com", f"member-{_suffix()}@example.com"
    await _seed_user(backend, owner)
    await _seed_user(backend, member)
    group_id = f"g:{_suffix()}"
    await processor.on_new_user_groups([(AppUserGroup(
        app_name=Connectors.DROPBOX, connector_id=connector_id, source_user_group_id=group_id,
        name="Everyone", org_id=ORG,
    ), [])])
    home = f"dbmid-{_suffix()}"
    await processor.on_new_record_groups([(_group(home, connector_id), [
        Permission(email=owner, type=PermissionType.OWNER, entity_type=EntityType.USER),
    ])])
    folder = _file(f"id:{_suffix()}", connector_id, home, "/shared", folder=True)
    child = _file(f"id:{_suffix()}", connector_id, home, "/shared/a.txt")
    child.parent_external_record_id = folder.external_record_id
    await processor.on_new_records([(folder, [
        Permission(email=owner, type=PermissionType.OWNER, entity_type=EntityType.USER),
        Permission(email=member, type=PermissionType.WRITE, entity_type=EntityType.USER),
        Permission(external_id=group_id, type=PermissionType.READ, entity_type=EntityType.GROUP),
    ])])
    await processor.on_new_records([(child, [])])

    users = await backend.provider.get_users_with_permission_to_node(
        child.id, CollectionNames.RECORDS.value, raise_on_error=True,
    )
    on_child = await backend.provider.get_groups_with_permission_to_node(
        child.id, CollectionNames.RECORDS.value, raise_on_error=True,
    )
    on_folder = await backend.provider.get_groups_with_permission_to_node(
        folder.id, CollectionNames.RECORDS.value, raise_on_error=True,
    )
    parent = await backend.provider.get_record_by_external_id(connector_id, child.parent_external_record_id)

    assert {u.email for u in users} >= {owner, member}
    assert on_child == []
    assert [g.source_user_group_id for g in on_folder] == [group_id]
    assert parent.id == folder.id
