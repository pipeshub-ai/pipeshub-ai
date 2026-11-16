"""One connector's writes through the real processor, with its accounts kept as SourceAccount nodes or not, on any graph store.

The store's own test seeds the PipesHub users (``PEOPLE``, plus the creator, a user with a userId) and the app, then calls ``write``.
``readers`` asks the store's access check who reads each record; ``new_kinds`` lists what the writes left that the code before the setting
never writes.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

APP = Connectors.CONFLUENCE
PEOPLE = ("ana", "ana2", "ben", "cal", "dup")
# the accounts the source lists: two of them share dup's address
ACCOUNTS = {"s-ana": "ana", "s-ben": "ben", "s-cal": "cal", "s-d1": "dup", "s-d2": "dup"}


def account(connector_id: str, emails: dict[str, str], source_id: str, person: str | None = None) -> AppUser:
    person = person or ACCOUNTS[source_id]
    return AppUser(app_name=APP, connector_id=connector_id, source_user_id=source_id, email=emails[person], full_name=person, is_active=True)


def grant(email: str, source_id: str | None, role: PermissionType = PermissionType.READ) -> Permission:
    return Permission(email=email, external_id=source_id, type=role, entity_type=EntityType.USER)


async def enable(processor: Any, connector_id: str) -> None:  # noqa: ANN401
    # the setting's key, written out: the contract an operator sets (source_accounts.graph_mode_key)
    await processor.config_service.set_config(f"/services/connectors/{connector_id}/sourceAccounts/graph", {"enabled": True})


async def write(processor: Any, connector_id: str, org_id: str, emails: dict[str, str], users: dict[str, str], creator: str) -> dict[str, FileRecord]:  # noqa: ANN401
    """The listing, a group, records granted by account id, by an id another account shares the address of and by address only, the
    creator's sign-in as cal's account, and ben withdrawn. Returns the records by name."""
    await processor.on_new_app_users([account(connector_id, emails, sid) for sid in ACCOUNTS])
    group = AppUserGroup(source_user_group_id=f"g-{uuid.uuid4().hex[:8]}", app_name=APP, connector_id=connector_id, name="Readers")
    await processor.on_new_user_groups([(group, [account(connector_id, emails, "s-ben"), account(connector_id, emails, "s-cal")])])
    space = RecordGroup(org_id=org_id, name="Space", external_group_id=f"space-{uuid.uuid4().hex[:8]}", connector_name=APP,
                        connector_id=connector_id, group_type=RecordGroupType.DRIVE, inherit_permissions=False)
    await processor.on_new_record_groups([(space, [])])
    records: dict[str, FileRecord] = {}
    for name in ("by-id", "shared-address", "by-address", "by-group"):
        records[name] = FileRecord(
            id=str(uuid.uuid4()), org_id=org_id, record_name=name, record_type=RecordType.FILE, record_group_type=RecordGroupType.DRIVE.value,
            external_record_group_id=space.external_group_id, external_record_id=f"{name}-{uuid.uuid4().hex[:8]}", version=0,
            origin=OriginTypes.CONNECTOR.value, connector_name=APP, connector_id=connector_id, is_file=True, extension="pdf",
            mime_type="application/pdf", inherit_permissions=False, rewrite_permissions=True,
        )
    await processor.on_new_records([
        (records["by-id"], [grant(emails["ana"], "s-ana", PermissionType.OWNER)]),
        (records["shared-address"], [grant(emails["dup"], "s-d2")]),
        (records["by-address"], [grant(emails["ben"], None)]),
        (records["by-group"], [Permission(external_id=group.source_user_group_id, type=PermissionType.READ, entity_type=EntityType.GROUP)]),
    ])
    await processor.link_authenticator_to_source_user(connector_id, creator, emails["cal"], "s-cal", APP)
    await processor.withdraw_app_user_access(connector_id, [account(connector_id, emails, "s-ben").model_copy(update={"id": users["ben"]})])
    return records


async def readers(records: dict[str, FileRecord], users: dict[str, str], allowed: Callable[[str, str], Awaitable[bool]]) -> dict[str, set[str]]:
    """Who reads each record: the people of ``users`` (person -> user key) the access check admits."""
    return {name: {p for p, key in users.items() if await allowed(key, rec.id)} for name, rec in records.items()}


def new_kinds(accounts: list[dict], links: list[dict], grants_from: set[str]) -> list[str]:
    """What only the setting writes: SourceAccount nodes, links with a ``via`` or to a SourceAccount, grants held by a SourceAccount."""
    out = [f"source account {a.get('sourceUserId')}" for a in accounts]
    out += [f"link {link.get('_to')} via {link.get('via')}" for link in links
            if link.get("via") or str(link.get("_to", "")).startswith(f"{ACCOUNTS_COLL}/")]
    out += [f"a grant held by {holder}" for holder in sorted(grants_from) if holder.startswith(f"{ACCOUNTS_COLL}/")]
    return out


ACCOUNTS_COLL = CollectionNames.SOURCE_ACCOUNTS.value


async def state(provider: Any, connector_id: str, users: dict[str, str], records: dict[str, FileRecord]) -> tuple[list[dict], list[dict], set[str]]:  # noqa: ANN401
    """The connector's SourceAccount nodes, the users' authenticatedAs links, and every holder of a grant on the records."""
    accounts = await provider.get_nodes_by_filters(ACCOUNTS_COLL, {"connectorId": connector_id}, raise_on_error=True)
    links = [e for key in users.values()
             for e in await provider.get_edges_from_node(f"{CollectionNames.USERS.value}/{key}", CollectionNames.AUTHENTICATED_AS.value, raise_on_error=True)]
    holders = {e.get("_from") or f"{e.get('from_collection')}/{e.get('from_id')}" for r in records.values()
               for e in await provider.get_edges_to_node(f"{CollectionNames.RECORDS.value}/{r.id}", CollectionNames.PERMISSION.value, raise_on_error=True)}
    return accounts, links, holders


async def delete_and_restore(provider: Any, config_service: Any, org_id: str, email: str, user_id: str,  # noqa: ANN401
                             check: Callable[[], Awaitable[None]], check_restored: Callable[[], Awaitable[None]]) -> None:
    """The user is soft-deleted and restored through the entity events, with ``check`` run while deleted and ``check_restored`` after."""
    import logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.messaging.kafka.handlers.entity import EntityEventService

    container = MagicMock()
    container.config_service = lambda: config_service
    service = EntityEventService(logging.getLogger("source-accounts"), provider, container)
    # the side effects these events also have (caches, the user's own collection, the All team, other connectors) are not under test
    with patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()), \
         patch.object(service, "_get_or_create_knowledge_base", AsyncMock()), \
         patch.object(service, "_get_or_create_all_team_and_add_user", AsyncMock()), \
         patch.object(service, "_schedule_connector_link", MagicMock()):
        assert await service._handle_user_deleted({"email": email, "orgId": org_id})
        await check()
        assert await service._handle_user_added({"email": email, "orgId": org_id, "userId": user_id})
        await check_restored()


async def mark_indexed(provider: Any, record: FileRecord) -> None:  # noqa: ANN401
    """The search scope holds indexed records only; a new record is written queued for indexing."""
    await provider.batch_upsert_nodes([{"_key": record.id, "indexingStatus": "COMPLETED", "virtualRecordId": record.id}], CollectionNames.RECORDS.value)
