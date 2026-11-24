"""A shared drive one org reaches as a foreign drive stays apart from the org that owns it, on both graphs.

Org B owns a shared drive and syncs it; a user of org A is a member of it, so org A's Drive Workspace sync stores
the same drive id and file id as a foreign drive. Both orgs hold a user with the same address. Every node and edge
org A's foreign-drive path writes (the drive's group and its grants, the file, its people, and the removal of a
leaver's grants) must stay in org A: no edge may join an org A node to an org B node, and org B's drive, file and
user must come out as org B wrote them.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.models.entities import (
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    SourcePerson,
)
from app.models.permission import EntityType, Permission, PermissionType

from ..processor_harness import build_processor
from ..write_path_support import ORG, _seed_app, _suffix
from .test_user_by_email_org_write_path import _user

pytestmark = pytest.mark.integration

USERS, RECORDS, GROUPS = CollectionNames.USERS.value, CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value


def _end(edge: dict, side: str) -> str:
    return edge.get(f"_{side}") or f"{edge.get(f'{side}_collection')}/{edge.get(f'{side}_id')}"


async def _from(backend, node: str, edges: str) -> set[str]:
    return {_end(e, "to") for e in await backend.provider.get_edges_from_node(node, edges)}


async def _to(backend, node: str, edges: str) -> set[str]:
    return {_end(e, "from") for e in await backend.provider.get_edges_to_node(node, edges)}


def _file(org: str, connector_id: str, drive_id: str, file_id: str, **kw) -> FileRecord:
    return FileRecord(
        id=str(uuid.uuid4()), org_id=org, record_name="plan.pdf", record_type=RecordType.FILE,
        external_record_group_id=drive_id, external_record_id=file_id, version=0,
        origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        connector_id=connector_id, is_file=True, extension="pdf", mime_type="application/pdf", **kw,
    )


async def test_a_foreign_drive_and_its_people_stay_in_the_org_that_reaches_it(backend) -> None:
    org_b = f"org-owner-{_suffix()}"
    conn_a, conn_b = f"conn-a-{_suffix()}", f"conn-b-{_suffix()}"
    drive_id, file_id, kept_id = f"0APG{_suffix()}", f"file-{_suffix()}", f"file-{_suffix()}"
    shared, only_b = f"shared-{_suffix()}@partner.test", f"only-b-{_suffix()}@partner.test"
    # Org B's users are written first, with keys that sort first, so an unscoped lookup would answer them.
    user_b, only_b_user, user_a = f"a0-b-{_suffix()}", f"a0-b-only-{_suffix()}", f"user-a-{_suffix()}"
    await _user(backend, user_b, shared, org_b)
    await _user(backend, only_b_user, only_b, org_b)
    await _user(backend, user_a, shared, ORG)
    for conn, org in ((conn_b, org_b), (conn_a, ORG)):
        await _seed_app(backend, conn, name="Drive Workspace", app_type=Connectors.GOOGLE_DRIVE_WORKSPACE.value,
                        app_group="Google Workspace", org=org)
    proc_a, _ = build_processor(backend.provider, ORG)
    proc_b, _ = build_processor(backend.provider, org_b)
    record_ids: list[str] = []
    try:
        # Org B: its own drive, granted to its own user, and the file in it.
        await proc_b.on_new_record_groups([(
            RecordGroup(org_id=org_b, name="Partner drive", external_group_id=drive_id, connector_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
                        connector_id=conn_b, group_type=RecordGroupType.DRIVE),
            [Permission(email=shared, type=PermissionType.READ, entity_type=EntityType.USER)],
        )])
        file_b = _file(org_b, conn_b, drive_id, file_id)
        await proc_b.on_new_records([(file_b, [])])
        record_ids.append(file_b.id)

        # Org A: the same drive as a foreign drive its user reaches, through the connector's own write.
        connector_a = SimpleNamespace(
            data_entities_processor=proc_a, connector_name=Connectors.GOOGLE_DRIVE_WORKSPACE, connector_id=conn_a,
            # The leaver is no longer synced, so nobody is asked whether they open a file: their grants go.
            synced_users=[],
        )
        await GoogleDriveTeamConnector._write_foreign_drive(connector_a, drive_id, {"name": "Partner drive", "members": [shared]})
        file_a = _file(ORG, conn_a, drive_id, file_id, shared_with=[SourcePerson(email=shared), SourcePerson(email=only_b)])
        # A file its reader holds a grant of their own on, as a Viewer's fallback grant was stored; trashed below.
        kept_a = _file(ORG, conn_a, drive_id, kept_id)
        await proc_a.on_new_records([
            (file_a, []), (kept_a, [Permission(email=shared, type=PermissionType.READ, entity_type=EntityType.USER)]),
        ])
        record_ids += [file_a.id, kept_a.id]

        group_a = await proc_a.get_record_group_by_external_id(conn_a, drive_id)
        group_b = await proc_b.get_record_group_by_external_id(conn_b, drive_id)
        assert group_a is not None and group_b is not None and group_a.id != group_b.id
        assert file_a.id != file_b.id

        # Each org's drive is granted to that org's user only.
        assert await _to(backend, f"{GROUPS}/{group_a.id}", CollectionNames.PERMISSION.value) == {f"{USERS}/{user_a}"}
        assert await _to(backend, f"{GROUPS}/{group_b.id}", CollectionNames.PERMISSION.value) == {f"{USERS}/{user_b}"}
        # Each org's file sits under that org's drive.
        assert await _from(backend, f"{RECORDS}/{file_a.id}", CollectionNames.INHERIT_PERMISSIONS.value) == {f"{GROUPS}/{group_a.id}"}
        assert await _from(backend, f"{RECORDS}/{file_b.id}", CollectionNames.INHERIT_PERMISSIONS.value) == {f"{GROUPS}/{group_b.id}"}
        # The people the file was shared with are org A's: its user, and an org A person for the address only
        # org B holds as a user.
        people = await _from(backend, f"{RECORDS}/{file_a.id}", CollectionNames.ENTITY_RELATIONS.value)
        assert f"{USERS}/{user_a}" in people
        assert not people & {f"{USERS}/{user_b}", f"{USERS}/{only_b_user}"}
        person_ids = [p.split("/", 1)[1] for p in people if p.startswith(f"{CollectionNames.PEOPLE.value}/")]
        assert len(person_ids) == 1
        person = await backend.provider.get_person_by_email(only_b, ORG)
        assert person is not None and person.id == person_ids[0]
        assert await backend.provider.get_person_by_email(only_b, org_b) is None

        assert f"{USERS}/{user_a}" in await _to(backend, f"{RECORDS}/{kept_a.id}", CollectionNames.PERMISSION.value)
        # In the trash when the member leaves: restored, it would come back still granting them.
        await backend.provider.update_node(kept_a.id, RECORDS, {"isDeleted": True})

        # The member leaves: org A's group is revoked and their own grant on org A's file removed.
        await GoogleDriveTeamConnector._write_foreign_drive(connector_a, drive_id, {"name": "Partner drive", "members": []})
        await GoogleDriveTeamConnector._drop_leaver_grants(connector_a, drive_id, shared)
        assert await _to(backend, f"{GROUPS}/{group_a.id}", CollectionNames.PERMISSION.value) == set()
        assert f"{USERS}/{user_a}" not in await _to(backend, f"{RECORDS}/{kept_a.id}", CollectionNames.PERMISSION.value)

        # Each org's drive belongs to that org's own connector and org, and to nothing of the other's.
        apps, orgs = CollectionNames.APPS.value, CollectionNames.ORGS.value
        for group, own_app, own_org, other_app, other_org in (
            (group_a, conn_a, ORG, conn_b, org_b), (group_b, conn_b, org_b, conn_a, ORG),
        ):
            owners = await _from(backend, f"{GROUPS}/{group.id}", CollectionNames.BELONGS_TO.value)
            assert {f"{apps}/{own_app}", f"{orgs}/{own_org}"} <= owners, owners
            assert not owners & {f"{apps}/{other_app}", f"{orgs}/{other_org}"}, owners

        # Org B comes out as it wrote itself: its user still holds its drive, and no edge reaches org A.
        assert await _to(backend, f"{GROUPS}/{group_b.id}", CollectionNames.PERMISSION.value) == {f"{USERS}/{user_b}"}
        assert await _from(backend, f"{USERS}/{user_b}", CollectionNames.PERMISSION.value) == {f"{GROUPS}/{group_b.id}"}
        for node in (f"{RECORDS}/{file_b.id}", f"{GROUPS}/{group_b.id}"):
            for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                          CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.ENTITY_RELATIONS.value):
                ends = await _to(backend, node, edges) | await _from(backend, node, edges)
                assert not ends & {f"{USERS}/{user_a}", f"{RECORDS}/{file_a.id}", f"{RECORDS}/{kept_a.id}",
                                   f"{GROUPS}/{group_a.id}"}, (node, edges)
    finally:
        await backend.provider.delete_nodes_and_edges(record_ids, RECORDS)
        for org, conn in ((ORG, conn_a), (org_b, conn_b)):
            group = await build_processor(backend.provider, org)[0].get_record_group_by_external_id(conn, drive_id)
            if group is not None:
                await backend.provider.delete_nodes_and_edges([group.id], GROUPS)
        person = await backend.provider.get_person_by_email(only_b, ORG)
        if person is not None:
            await backend.provider.delete_nodes_and_edges([person.id], CollectionNames.PEOPLE.value)
        await backend.provider.delete_nodes_and_edges([user_b, only_b_user, user_a], USERS)
        await backend.provider.delete_nodes_and_edges([conn_a, conn_b], CollectionNames.APPS.value)
        await backend.provider.delete_nodes_and_edges([org_b], CollectionNames.ORGS.value)
