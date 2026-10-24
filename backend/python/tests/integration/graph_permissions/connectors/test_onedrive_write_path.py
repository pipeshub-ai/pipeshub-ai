"""OneDrive organization-scoped links, written by the real processor into both graphs.

Found in a live run of PR #3880 (OD-2): an "organization" sharing link was
stored as an org grant, so a guest who cannot open such a link saw the file in
PipesHub. Creating the link does not open the file to the organization at all,
and Graph lists no grantees on an organization link, so it grants nobody. A
specific-people link grants only the person it lists.
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.identity import Identity
from msgraph.generated.models.permission import Permission as GraphPermission
from msgraph.generated.models.share_point_identity_set import SharePointIdentitySet
from msgraph.generated.models.sharing_link import SharingLink

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.models.entities import (
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType

from ..processor_harness import build_processor
from ..write_path_support import ORG, _seed_app, _seed_user, _suffix

pytestmark = pytest.mark.integration

DRIVE = "drive-ana"


async def test_an_organization_link_grants_no_one_and_a_link_grants_the_people_it_lists(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id, name="OneDrive", app_type=Connectors.ONEDRIVE.value, app_group="Microsoft")
    listed_email, colleague_email = f"ana-{_suffix()}@acme.com", f"ben-{_suffix()}@acme.com"
    listed_id = await _seed_user(backend, listed_email)
    colleague_id = await _seed_user(backend, colleague_email)
    connector = OneDriveConnector(logging.getLogger("onedrive-it"), processor, MagicMock(), AsyncMock(),
                                  connector_id, "team", "admin")

    listed = SharePointIdentitySet(user=Identity(id="u-ana", additional_data={"email": listed_email}))
    grants = await connector._convert_to_permissions([
        # Graph's shape for an organization link: roles and scope, no grantees.
        GraphPermission(id="p-org", roles=["write"], link=SharingLink(scope="organization", type="edit")),
        GraphPermission(id="p-users", roles=["write"], link=SharingLink(scope="users", type="edit"),
                        granted_to_identities_v2=[listed]),
    ])
    assert [(g.entity_type, g.email) for g in grants] == [(EntityType.USER, listed_email)], \
        "only the person a link lists is granted; the organization link adds no group"
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

    async def edge(from_collection: str, from_id: str, to_collection: str, to_id: str) -> bool:
        return await backend.edge_exists(CollectionNames.PERMISSION.value, from_collection, from_id, to_collection, to_id)

    assert await edge(CollectionNames.USERS.value, listed_id, CollectionNames.RECORDS.value, record.id)
    assert not await edge(CollectionNames.USERS.value, colleague_id, CollectionNames.RECORDS.value, record.id), \
        "a colleague the links do not list has no access through them"
    assert not await edge(CollectionNames.ORGS.value, ORG, CollectionNames.RECORDS.value, record.id), \
        "an organization link is not a grant to every PipesHub user"
