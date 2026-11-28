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


async def links_of(provider: Any, user_key: str) -> set[tuple[str, str | None]]:  # noqa: ANN401
    """``(target, via)`` of the user's authenticatedAs links."""
    edges = await provider.get_edges_from_node(f"{CollectionNames.USERS.value}/{user_key}", CollectionNames.AUTHENTICATED_AS.value, raise_on_error=True)
    return {(e.get("_to") or f"{e.get('to_collection')}/{e.get('to_id')}", e.get("via")) for e in edges}


async def authenticator_survives_relinks(processor: Any, provider: Any, connector_id: str, org_id: str,  # noqa: ANN401
                                         emails: dict[str, str], users: dict[str, str], cal_user_id: str) -> None:
    """cal signs the connector in as its own account, which its address also links (two links of one pair: authenticator and
    directory). A second live account with the address, a withdrawal, a re-listing and a rename each relink the address; only the
    directory link may come and go, the authenticator link always stays. Run after ``write``."""
    from app.connectors.core.base.data_processor.source_accounts import account_node_id

    target = f"{ACCOUNTS_COLL}/{account_node_id(org_id, connector_id, 's-cal')}"
    signed_in, listed = (target, "authenticator"), (target, "directory")
    cal = users["cal"]
    await processor.link_authenticator_to_source_user(connector_id, cal_user_id, emails["cal"], "s-cal", APP)
    assert {signed_in, listed} <= await links_of(provider, cal), "signed in as the account its address links"

    await processor.on_new_app_users([account(connector_id, emails, "s-cal2", "cal")])
    got = await links_of(provider, cal)
    assert signed_in in got and listed not in got, f"a second live account with the address: the directory link goes, not the sign-in: {got}"

    await processor.withdraw_app_user_access(connector_id, [account(connector_id, emails, "s-cal2", "cal").model_copy(update={"id": cal})])
    await processor.on_new_app_users([account(connector_id, emails, "s-cal")])
    got = await links_of(provider, cal)
    assert {signed_in, listed} <= got, f"the directory link comes back beside the sign-in, not over it: {got}"

    await processor.on_new_app_users([account(connector_id, emails, "s-cal", "ana2")])
    got = await links_of(provider, cal)
    assert signed_in in got and listed not in got, f"renamed away from cal's address: {got}"
    assert listed in await links_of(provider, users["ana2"]), "the new address links the renamed account"

    await processor.withdraw_app_user_access(connector_id, [account(connector_id, emails, "s-cal", "ana2").model_copy(update={"id": users["ana2"]})])
    got = await links_of(provider, cal)
    assert signed_in in got, f"a withdrawal relinks the addresses; the sign-in stays: {got}"
    # a relink that chose s-cal before the withdrawal (a user event racing the sync) writes after it: the store decides again and links nobody
    await provider.set_directory_link(users["ana2"], connector_id, emails["ana2"], org_id)
    assert listed not in await links_of(provider, users["ana2"]), "a withdrawn account is never linked again"


async def another_orgs_user_is_not_linked(provider: Any, org_id: str, connector_id: str, users: dict[str, str], foreign_key: str) -> None:  # noqa: ANN401
    """After ``write``: ana's address is linked to this org's ana only, never to another org's user with the same address."""
    from app.connectors.core.base.data_processor.source_accounts import account_node_id

    assert await links_of(provider, foreign_key) == set(), "another org's user with the address is linked to nothing here"
    assert (f"{ACCOUNTS_COLL}/{account_node_id(org_id, connector_id, 's-ana')}", "directory") in await links_of(provider, users["ana"])


async def role_memberships_stay_with_the_old_account(processor: Any, provider: Any, connector_id: str, org_id: str,  # noqa: ANN401
                                                     emails: dict[str, str], users: dict[str, str]) -> None:
    """After ``write`` with the setting on: ana's account is a member of a role. When a new account takes ana's address, the membership
    stays on the old account, which nobody is linked to while both are stored or once it is withdrawn; the new holder gets none of it."""
    from app.config.constants.arangodb import CollectionNames
    from app.connectors.core.base.data_processor.source_accounts import account_node_id
    from app.models.entities import AppRole

    role = AppRole(app_name=APP, connector_id=connector_id, source_role_id=f"r-{uuid.uuid4().hex[:8]}", name="Reviewers", org_id=org_id)
    await processor.on_new_app_roles([(role, [account(connector_id, emails, "s-ana")])])

    async def roles_of(node: str) -> set[str]:
        edges = await provider.get_edges_from_node(node, CollectionNames.PERMISSION.value, raise_on_error=True)
        return {e.get("_to") or f"{e.get('to_collection')}/{e.get('to_id')}" for e in edges
                if CollectionNames.ROLES.value in str(e.get("_to") or e.get("to_collection"))}

    async def linked_to(node: str) -> set[str]:
        edges = await provider.get_edges_to_node(node, CollectionNames.AUTHENTICATED_AS.value, raise_on_error=True)
        return {e.get("_from") or f"{e.get('from_collection')}/{e.get('from_id')}" for e in edges}

    ana = f"{CollectionNames.USERS.value}/{users['ana']}"
    old, new = (f"{ACCOUNTS_COLL}/{account_node_id(org_id, connector_id, sid)}" for sid in ("s-ana", "s-ana-new"))
    held = await roles_of(old)
    assert held and await roles_of(ana) == set() and ana in await linked_to(old), "the membership is the account's, read through ana's link"

    newcomer = account(connector_id, emails, "s-ana-new", "ana")
    await processor.on_new_app_users([newcomer])
    assert await linked_to(old) == set() and await linked_to(new) == set(), "two live accounts hold the address: neither is linked"
    assert await roles_of(new) == set() and await roles_of(ana) == set() and await roles_of(old) == held

    await processor.withdraw_app_user_access(connector_id, [account(connector_id, emails, "s-ana").model_copy(update={"id": users["ana"]})])
    await processor.on_new_app_users([newcomer])
    assert await linked_to(old) == set(), "a withdrawn account's memberships grant nobody"
    assert ana in await linked_to(new), "the address now links the new account"
    assert await roles_of(new) == set() and await roles_of(ana) == set(), "the new holder has none of the old account's memberships"
    assert await roles_of(old) == held, "the old account keeps its memberships after withdrawal"


async def a_deleted_user_is_not_linked_again(processor: Any, provider: Any, connector_id: str, emails: dict[str, str], users: dict[str, str],  # noqa: ANN401
                                            cal_user_id: str) -> None:
    """While cal is deleted: the next listing of its account and its own sign-in as that account link nothing."""
    await processor.on_new_app_users([account(connector_id, emails, "s-cal")])
    await processor.link_authenticator_to_source_user(connector_id, cal_user_id, emails["cal"], "s-cal", APP)
    assert not [link for link in await links_of(provider, users["cal"]) if link[0].startswith(f"{ACCOUNTS_COLL}/")], \
        "a deleted user is linked to no source account until restored"


async def revocations_reach_the_account(processor: Any, provider: Any, connector_id: str, emails: dict[str, str],  # noqa: ANN401
                                        records: dict[str, FileRecord]) -> None:
    """After ``write`` with the setting on: a grant and a membership revoked by address are taken from the account that holds them."""
    from app.config.constants.arangodb import CollectionNames
    from app.connectors.core.base.data_processor.source_accounts import account_node_id

    org_id = records["by-id"].org_id

    async def targets(source_id: str) -> set[str]:
        node = f"{ACCOUNTS_COLL}/{account_node_id(org_id, connector_id, source_id)}"
        edges = await provider.get_edges_from_node(node, CollectionNames.PERMISSION.value, raise_on_error=True)
        return {e.get("_to") or f"{e.get('to_collection')}/{e.get('to_id')}" for e in edges}

    by_id = f"{CollectionNames.RECORDS.value}/{records['by-id'].id}"
    assert by_id in await targets("s-ana")
    await processor.delete_permission_from_record(records["by-id"].id, emails["ana"], connector_id)
    assert by_id not in await targets("s-ana"), "the revoked grant is taken from the account"

    granted_by = await provider.get_edges_to_node(f"{CollectionNames.RECORDS.value}/{records['by-group'].id}", CollectionNames.PERMISSION.value,
                                                  raise_on_error=True)
    in_group = next(h for e in granted_by if (h := e.get("_from") or f"{e.get('from_collection')}/{e.get('from_id')}").startswith("groups/"))
    group_id = (await provider.get_document(in_group.split("/", 1)[1], CollectionNames.GROUPS.value))["externalGroupId"]
    assert in_group in await targets("s-cal")
    assert await processor.on_user_group_member_removed(group_id, emails["cal"], connector_id)
    assert in_group not in await targets("s-cal"), "the removed membership is taken from the account"


async def a_deactivated_signer_keeps_no_link(processor: Any, provider: Any, connector_id: str, emails: dict[str, str],  # noqa: ANN401
                                             users: dict[str, str], cal_user_id: str) -> None:
    """After ``write``: cal signs in as its account, then is deactivated without its links being removed (a sync that read it live
    racing its delete). The next sign-in removes the link instead of keeping it."""
    from app.config.constants.arangodb import CollectionNames

    def signed_in(links: set[tuple[str, str | None]]) -> bool:
        return any(via == "authenticator" for _, via in links)

    await processor.link_authenticator_to_source_user(connector_id, cal_user_id, emails["cal"], "s-cal", APP)
    assert signed_in(await links_of(provider, users["cal"]))
    await provider.batch_upsert_nodes([{"_key": users["cal"], "isActive": False}], CollectionNames.USERS.value)
    await processor.link_authenticator_to_source_user(connector_id, cal_user_id, emails["cal"], "s-cal", APP)
    assert not signed_in(await links_of(provider, users["cal"])), "a deactivated user keeps no authenticator link"


async def a_stale_address_links_nothing(provider: Any, connector_id: str, org_id: str, emails: dict[str, str], users: dict[str, str]) -> None:  # noqa: ANN401
    """After ``write``: cal's address changes to one no account holds, and the relink of the new address leaves cal unlinked. A relink
    that read cal under the old address and writes afterwards links nothing: the user no longer holds that address."""
    from app.config.constants.arangodb import CollectionNames

    moved = f"moved-{uuid.uuid4().hex[:8]}@acme.com"
    await provider.batch_upsert_nodes([{"_key": users["cal"], "email": moved}], CollectionNames.USERS.value)
    await provider.set_directory_link(users["cal"], connector_id, moved, org_id)
    assert not [x for x in await links_of(provider, users["cal"]) if x[1] == "directory"], "no account holds the new address"
    await provider.set_directory_link(users["cal"], connector_id, emails["cal"], org_id)
    assert not [x for x in await links_of(provider, users["cal"]) if x[1] == "directory"], "the stale address links nothing"


async def an_address_change_moves_the_directory_link(provider: Any, config_service: Any, org_id: str, connector_id: str,  # noqa: ANN401
                                                     emails: dict[str, str], users: dict[str, str], user_id: str) -> None:
    """After ``write`` with the setting on: cal's PipesHub address changes (the user-updated event). The link to the account at the old
    address goes, and the account at the new address is linked when it is the only live one there."""
    import contextlib
    import logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.connectors.core.base.data_processor.source_accounts import account_node_id
    from app.services.messaging.kafka.handlers.entity import EntityEventService

    def directory(links: set[tuple[str, str | None]]) -> set[str]:
        return {to for to, via in links if via == "directory"}

    old = f"{ACCOUNTS_COLL}/{account_node_id(org_id, connector_id, 's-cal')}"
    assert directory(await links_of(provider, users["cal"])) == {old}
    container = MagicMock()
    container.config_service = lambda: config_service
    service = EntityEventService(logging.getLogger("source-accounts"), provider, container)
    with patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
        moved = f"moved-{uuid.uuid4().hex[:8]}@acme.com"
        # the address is stored, then the move fails: the event is delivered again and must still move the link
        with patch.object(service, "_relink_source_accounts", AsyncMock(side_effect=RuntimeError("relink failed"))), \
                contextlib.suppress(RuntimeError):
            await service.process_event("userUpdated", {"userId": user_id, "orgId": org_id, "email": moved})
        assert ((await provider.get_user_by_user_id(user_id)) or {}).get("email") == moved, "the address was stored before the move failed"
        assert directory(await links_of(provider, users["cal"])) == {old}
        assert await service.process_event("userUpdated", {"userId": user_id, "orgId": org_id, "email": moved})
        assert directory(await links_of(provider, users["cal"])) == set(), "no account holds the new address: the old link goes"
        assert await service.process_event("userUpdated", {"userId": user_id, "orgId": org_id, "email": emails["cal"]})
        assert directory(await links_of(provider, users["cal"])) == {old}, "back at its address: the one live account there is linked again"
