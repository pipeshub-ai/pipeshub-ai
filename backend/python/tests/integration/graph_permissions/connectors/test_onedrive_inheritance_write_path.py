"""OneDrive inherited permissions, written by the real processor and read by the real access check, on both graphs.

With a client certificate the connector stores an item that inherits with
``inherit_permissions=True`` and no grants, and a uniquely permissioned one with
its own ACL. These check that this gives each person exactly the access OneDrive
gives them, including after the folder's sharing changes, and that the moves the
connector makes on migration and on an unreadable ACL never widen access.
"""

import uuid

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import (
    AppUser,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType

from ..processor_harness import build_processor
from ..write_path_support import ORG, _seed_app, _seed_user, _suffix

pytestmark = pytest.mark.integration


def grant(email: str, role: PermissionType = PermissionType.READ) -> Permission:
    return Permission(email=email, type=role, entity_type=EntityType.USER)


class Drive:
    """Ana's OneDrive: root > Plans (shared with Ben) > plan.pdf (inherits) and budget.pdf (shared with Cal)."""

    def __init__(self, backend, processor, connector_id: str, emails: dict[str, str], users: dict[str, str]) -> None:
        self.backend, self.processor, self.connector_id = backend, processor, connector_id
        self.emails, self.users = emails, users
        self.drive = f"drive-{_suffix()}"
        self.records: dict[str, FileRecord] = {}

    def record(self, name: str, parent: str | None, *, folder: bool = False, inherits: bool) -> FileRecord:
        record = FileRecord(
            id=str(uuid.uuid4()), org_id=ORG, record_name=name, record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE.value, external_record_group_id=self.drive,
            external_record_id=f"{name}-{_suffix()}", version=0, origin=OriginTypes.CONNECTOR.value,
            connector_name=Connectors.ONEDRIVE, connector_id=self.connector_id, is_file=not folder,
            extension=None if folder else "pdf", mime_type="text/directory" if folder else "application/pdf",
            parent_external_record_id=self.records[parent].external_record_id if parent else None,
            parent_record_type=RecordType.FILE if parent else None,
            inherit_permissions=inherits, rewrite_permissions=True,
        )
        self.records[name] = record
        return record

    async def write(self, *, plan_inherits: bool = True) -> None:
        group = RecordGroup(org_id=ORG, name="Ana's OneDrive", external_group_id=self.drive,
                            connector_name=Connectors.ONEDRIVE, connector_id=self.connector_id,
                            group_type=RecordGroupType.DRIVE, inherit_permissions=False)
        await self.processor.on_new_record_groups([(group, [grant(self.emails["ana"], PermissionType.OWNER)])])
        owner = grant(self.emails["ana"], PermissionType.OWNER)
        plan_grants = [] if plan_inherits else [owner, grant(self.emails["ben"])]
        await self.processor.on_new_records([
            (self.record("root", None, folder=True, inherits=False), [owner]),
            (self.record("Plans", "root", folder=True, inherits=False), [owner, grant(self.emails["ben"])]),
            (self.record("plan.pdf", "Plans", inherits=plan_inherits), plan_grants),
            (self.record("budget.pdf", "Plans", inherits=False), [owner, grant(self.emails["cal"])]),
        ])

    async def readers(self, name: str) -> set[str]:
        node_id = self.records[name].id
        return {
            person for person, user_key in self.users.items()
            if node_id in set((await self.backend.provider.check_access(user_key, ORG, node_ids=[node_id])).node_ids)
        }


@pytest.fixture
async def drive(backend) -> Drive:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id, name="OneDrive", app_type=Connectors.ONEDRIVE.value, app_group="Microsoft")
    emails = {person: f"{person}-{_suffix()}@acme.com" for person in ("ana", "ben", "cal", "dee")}
    users = {person: await _seed_user(backend, email) for person, email in emails.items()}
    await processor.on_new_app_users([
        AppUser(app_name=Connectors.ONEDRIVE, connector_id=connector_id, source_user_id=f"u-{person}",
                email=email, full_name=person, is_active=True)
        for person, email in emails.items()
    ])
    return Drive(backend, processor, connector_id, emails, users)


async def test_an_inheriting_file_has_its_folders_audience_and_a_unique_one_only_its_own(drive: Drive) -> None:
    await drive.write()

    assert await drive.readers("plan.pdf") == {"ana", "ben"}
    assert await drive.readers("budget.pdf") == {"ana", "cal"}, "Ben can read the folder, not a file that broke inheritance"
    assert await drive.readers("Plans") == {"ana", "ben"}


async def test_a_folder_shared_later_reaches_the_inheriting_file_without_rewriting_it(drive: Drive) -> None:
    await drive.write()

    owner = grant(drive.emails["ana"], PermissionType.OWNER)
    plans = drive.records["Plans"]
    await drive.processor.on_updated_record_permissions(plans, [owner, grant(drive.emails["ben"]), grant(drive.emails["dee"])])

    assert await drive.readers("plan.pdf") == {"ana", "ben", "dee"}
    assert await drive.readers("budget.pdf") == {"ana", "cal"}


async def test_unsharing_the_folder_takes_the_inheriting_file_away_too(drive: Drive) -> None:
    """The bug inheritance fixes: a copy of the folder's grants on the file outlived the unshare."""
    await drive.write()

    await drive.processor.on_updated_record_permissions(drive.records["Plans"], [grant(drive.emails["ana"], PermissionType.OWNER)])

    assert await drive.readers("plan.pdf") == {"ana"}


async def test_migrating_a_file_onto_inheritance_keeps_its_audience_and_follows_the_folder_after(drive: Drive) -> None:
    """Stored before the certificate: a copy of the folder's grants and no inheritance."""
    await drive.write(plan_inherits=False)
    assert await drive.readers("plan.pdf") == {"ana", "ben"}

    plan = drive.records["plan.pdf"]
    plan.inherit_permissions, plan.rewrite_permissions = True, True
    await drive.processor.on_updated_record_permissions(plan, [])

    assert await drive.readers("plan.pdf") == {"ana", "ben"}
    await drive.processor.on_updated_record_permissions(drive.records["Plans"], [grant(drive.emails["ana"], PermissionType.OWNER)])
    assert await drive.readers("plan.pdf") == {"ana"}, "the copied grant is gone, so the unshare reaches it"


async def test_a_file_that_turned_unique_with_an_unreadable_list_is_hidden_not_left_open(drive: Drive) -> None:
    """SharePoint says unique, Graph's ACL can't be read: the connector drops the inherit edge with a metadata write."""
    await drive.write()

    plan = drive.records["plan.pdf"]
    plan.inherit_permissions = False
    await drive.processor.on_record_metadata_update(plan)

    assert await drive.readers("plan.pdf") == set(), "with no inherit edge and no grants of its own nobody gets the folder's audience"
