"""An address names a user of the processor's own org only, on both graphs, through the real processor.

Several orgs can hold a user with the same address. A lookup by address that does not name the org can answer
another org's user: a connector of this org would then grant that user this org's records, or act on it as this
org's user. The other org's user is written first and its key sorts first, so an unscoped read answers it.
"""

import uuid

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import FileRecord, RecordGroup, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType

from ..processor_harness import build_processor
from ..write_path_support import ORG, TS, _seed_app, _suffix

pytestmark = pytest.mark.integration


async def _user(backend, key: str, email: str, org: str) -> None:
    await backend.provider.batch_upsert_nodes([{"_key": key, "email": email, "orgId": org, "isActive": True,
                                                "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], collection=CollectionNames.USERS.value)


async def _granted(backend, record_id: str) -> set[str]:
    edges = await backend.provider.get_edges_to_node(f"{CollectionNames.RECORDS.value}/{record_id}", CollectionNames.PERMISSION.value)
    return {e.get("_from") or f"{e.get('from_collection')}/{e.get('from_id')}" for e in edges}


async def test_a_grant_by_address_reaches_only_this_orgs_user(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id, name="BookStack", app_type=Connectors.BOOKSTACK.value, app_group="BookStack")
    shared, only_elsewhere = f"shared-{_suffix()}@acme.com", f"elsewhere-{_suffix()}@acme.com"
    other_org = f"org-other-{_suffix()}"
    foreign, foreign_only, mine = f"a0-foreign-{_suffix()}", f"a0-foreign-only-{_suffix()}", f"user-{_suffix()}"
    await _user(backend, foreign, shared, other_org)
    await _user(backend, foreign_only, only_elsewhere, other_org)
    await _user(backend, mine, shared, ORG)
    try:
        space = f"book/{_suffix()}"
        await processor.on_new_record_groups([(RecordGroup(org_id=ORG, name=space, external_group_id=space, connector_name=Connectors.BOOKSTACK,
                                                           connector_id=connector_id, group_type=RecordGroupType.BOOK), [])])
        record = FileRecord(id=str(uuid.uuid4()), org_id=ORG, record_name="page", record_type=RecordType.FILE, external_record_group_id=space,
                            external_record_id=f"page/{_suffix()}", version=0, origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.BOOKSTACK,
                            connector_id=connector_id, is_file=True, extension="md", mime_type="text/markdown", inherit_permissions=False)
        grants = [Permission(email=e, type=PermissionType.READ, entity_type=EntityType.USER) for e in (shared, only_elsewhere)]
        await processor.on_new_records([(record, grants)])

        holders = await _granted(backend, record.id)
        assert f"{CollectionNames.USERS.value}/{mine}" in holders
        assert not holders & {f"{CollectionNames.USERS.value}/{foreign}", f"{CollectionNames.USERS.value}/{foreign_only}"}, \
            "another org's user is never granted this org's record"
        assert (await processor.get_user_by_email(shared)).id == mine, "the address names this org's user"
        assert await processor.get_user_by_email(only_elsewhere) is None, "an address only another org holds names nobody here"
    finally:
        await backend.provider.delete_nodes_and_edges([foreign, foreign_only], CollectionNames.USERS.value)
