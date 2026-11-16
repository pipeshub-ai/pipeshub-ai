"""An address names a user of the processor's own org only, on both graphs, through the real processor.

Several orgs can hold a user with the same address. A lookup by address that does not name the org can answer
another org's user: a connector of this org would then grant that user this org's records, or act on it as this
org's user. The other org's user is written first and its key sorts first, so an unscoped read answers it.
"""

import uuid

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import (
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
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


async def test_deleting_a_user_deactivates_only_that_orgs_user_with_the_address(backend) -> None:
    """The user-deleted event names an org; another org's user with the same address, written first, stays active and in its org."""
    import logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.messaging.kafka.handlers.entity import EntityEventService

    shared, other_org = f"shared-{_suffix()}@acme.com", f"org-other-{_suffix()}"
    foreign, mine = f"a0-foreign-{_suffix()}", f"user-{_suffix()}"
    await _user(backend, foreign, shared, other_org)
    await _user(backend, mine, shared, ORG)
    service = EntityEventService(logging.getLogger("user-deleted"), backend.provider, MagicMock())
    try:
        with patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
            assert await service._handle_user_deleted({"email": shared, "orgId": ORG})
        users = {u.get("_key") or u.get("id"): u for u in await backend.provider.get_nodes_by_filters(CollectionNames.USERS.value, {"email": shared})}
        assert users[foreign].get("isActive") is True and users[foreign].get("orgId") == other_org, "another org's user is untouched"
        assert users[mine].get("isActive") is False and users[mine].get("orgId") == ORG, "this org's user is deactivated"
    finally:
        await backend.provider.delete_nodes_and_edges([foreign, mine], CollectionNames.USERS.value)


async def test_a_group_whose_user_is_not_in_this_org_keeps_its_grants_until_the_user_is(backend) -> None:
    """Migrating a group to a user that only another org holds moves nothing, so the group and its grant stay; a later run moves them."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id, name="BookStack", app_type=Connectors.BOOKSTACK.value, app_group="BookStack")
    address, other_org = f"lead-{_suffix()}@acme.com", f"org-other-{_suffix()}"
    foreign, mine = f"a0-foreign-{_suffix()}", f"user-{_suffix()}"
    await _user(backend, foreign, address, other_org)
    try:
        group = AppUserGroup(source_user_group_id=f"g:{_suffix()}", app_name=Connectors.BOOKSTACK, connector_id=connector_id, name="Leads")
        await processor.on_new_user_groups([(group, [])])
        stored = await backend.provider.get_user_group_by_external_id(connector_id, group.source_user_group_id)
        space = f"book/{_suffix()}"
        await processor.on_new_record_groups([(RecordGroup(org_id=ORG, name=space, external_group_id=space, connector_name=Connectors.BOOKSTACK,
                                                           connector_id=connector_id, group_type=RecordGroupType.BOOK), [])])
        record = FileRecord(id=str(uuid.uuid4()), org_id=ORG, record_name="page", record_type=RecordType.FILE, external_record_group_id=space,
                            external_record_id=f"page/{_suffix()}", version=0, origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.BOOKSTACK,
                            connector_id=connector_id, is_file=True, extension="md", mime_type="text/markdown", inherit_permissions=False)
        await processor.on_new_records([(record, [Permission(external_id=group.source_user_group_id, type=PermissionType.WRITE,
                                                             entity_type=EntityType.GROUP)])])
        assert f"{CollectionNames.GROUPS.value}/{stored.id}" in await _granted(backend, record.id)

        await processor.migrate_group_to_user_by_external_id(group.source_user_group_id, address, connector_id)

        assert await backend.provider.get_user_group_by_external_id(connector_id, group.source_user_group_id) is not None, "the group is kept"
        holders = await _granted(backend, record.id)
        assert f"{CollectionNames.GROUPS.value}/{stored.id}" in holders, "its grant is kept"
        assert f"{CollectionNames.USERS.value}/{foreign}" not in holders, "another org's user receives nothing"

        await _user(backend, mine, address, ORG)
        await processor.migrate_group_to_user_by_external_id(group.source_user_group_id, address, connector_id)

        assert await backend.provider.get_user_group_by_external_id(connector_id, group.source_user_group_id) is None
        assert await _granted(backend, record.id) == {f"{CollectionNames.USERS.value}/{mine}"}
    finally:
        await backend.provider.delete_nodes_and_edges([foreign, mine], CollectionNames.USERS.value)
