"""OneDrive organization-scoped links, written by the real processor into both graphs.

Found in a live run of PR #3880 (OD-2): an "organization" sharing link was
stored as an org grant, so a guest of the Microsoft tenant, who cannot open
such a link, saw the file in PipesHub. The link now grants the tenant-members
group, which the user sync keeps to the tenant's active non-guest users.
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.permission import Permission as GraphPermission
from msgraph.generated.models.sharing_link import SharingLink

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.sources.microsoft.onedrive.connector import (
    TENANT_MEMBERS_GROUP_ID,
    OneDriveConnector,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)

from ..processor_harness import build_processor
from ..write_path_support import ORG, _seed_app, _seed_user, _suffix

pytestmark = pytest.mark.integration

DRIVE = "drive-ana"


def _app_user(email: str, connector_id: str, *, guest: bool) -> AppUser:
    return AppUser(
        app_name=Connectors.ONEDRIVE, connector_id=connector_id, source_user_id=f"u-{_suffix()}",
        email=email, full_name=email, is_active=True, is_guest=guest,
    )


async def test_an_organization_link_reaches_members_and_not_guests(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id, name="OneDrive", app_type=Connectors.ONEDRIVE.value, app_group="Microsoft")
    member_email, guest_email = f"ana-{_suffix()}@acme.com", f"gus-{_suffix()}@partner.com"
    member_id = await _seed_user(backend, member_email)
    guest_id = await _seed_user(backend, guest_email)
    connector = OneDriveConnector(logging.getLogger("onedrive-it"), processor, MagicMock(), AsyncMock(),
                                  connector_id, "team", "admin")

    await connector._sync_tenant_members_group([
        _app_user(member_email, connector_id, guest=False),
        _app_user(guest_email, connector_id, guest=True),
    ])
    grants = await connector._convert_to_permissions([
        GraphPermission(id="p-org", roles=["write"], link=SharingLink(scope="organization", type="edit")),
    ])
    group = RecordGroup(org_id=ORG, name="Ana's OneDrive", external_group_id=DRIVE, connector_name=Connectors.ONEDRIVE,
                        connector_id=connector_id, group_type=RecordGroupType.DRIVE, inherit_permissions=False)
    await processor.on_new_record_groups([(group, [])])
    record = FileRecord(
        id=str(uuid.uuid4()), org_id=ORG, record_name="Meeting notes.loop", record_type=RecordType.FILE,
        record_group_type=RecordGroupType.DRIVE.value, external_record_group_id=DRIVE,
        external_record_id=f"item-{_suffix()}", version=0, origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.ONEDRIVE, connector_id=connector_id, is_file=True,
        extension="loop", mime_type="application/octet-stream",
    )
    await processor.on_new_records([(record, grants)])

    tenant_group = await backend.provider.get_user_group_by_external_id(connector_id, TENANT_MEMBERS_GROUP_ID)
    assert tenant_group is not None

    async def edge(from_collection: str, from_id: str, to_collection: str, to_id: str) -> bool:
        return await backend.edge_exists(CollectionNames.PERMISSION.value, from_collection, from_id, to_collection, to_id)

    assert await edge(CollectionNames.GROUPS.value, tenant_group.id, CollectionNames.RECORDS.value, record.id)
    assert await edge(CollectionNames.USERS.value, member_id, CollectionNames.GROUPS.value, tenant_group.id)
    assert not await edge(CollectionNames.USERS.value, guest_id, CollectionNames.GROUPS.value, tenant_group.id), \
        "a guest of the tenant cannot open an organization link"
    assert not await edge(CollectionNames.ORGS.value, ORG, CollectionNames.RECORDS.value, record.id), \
        "an organization link is not a grant to every PipesHub user"
