"""A connector's accounts kept as SourceAccount nodes, through the real processor and the real access check, on Neo4j and ArangoDB.

With the setting on, each listed account is a node of its own: the address's PipesHub user reads its grants through a link, an address two
live accounts share links nobody, a renamed account moves its grants with the link, and the user who signed the connector in reads its
account. With the setting off (the default) nothing of that is written, and everyone reads what they read before.
"""

import os
import uuid

import pytest

from app.config.constants.arangodb import CollectionNames

from .. import source_account_writes as scenario
from ..processor_harness import build_processor
from ..write_path_support import ORG, TS, _seed_app, _seed_user, _suffix

pytestmark = pytest.mark.integration


class World:
    def __init__(self, backend, processor, connector_id: str, emails: dict[str, str], users: dict[str, str], creator: str) -> None:
        self.backend, self.processor, self.connector_id, self.emails, self.users, self.creator = backend, processor, connector_id, emails, users, creator

    async def write(self) -> dict:
        return await scenario.write(self.processor, self.connector_id, ORG, self.emails, self.users, self.creator)

    async def readers(self, records: dict) -> dict[str, set[str]]:
        async def allowed(user_key: str, node_id: str) -> bool:
            return node_id in set((await self.backend.provider.check_access(user_key, ORG, node_ids=[node_id])).node_ids)
        return await scenario.readers(records, self.users, allowed)


@pytest.fixture
async def world(backend) -> World:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    emails = {person: f"{person}-{_suffix()}@acme.com" for person in scenario.PEOPLE}
    users = {person: await _seed_user(backend, email) for person, email in emails.items()}
    creator = f"uid-creator-{uuid.uuid4().hex[:8]}"
    users["creator"] = f"user-{_suffix()}"
    await backend.provider.batch_upsert_nodes([{"_key": users["creator"], "userId": creator, "email": f"creator-{_suffix()}@acme.com",
                                                "orgId": ORG, "isActive": True, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
                                              collection=CollectionNames.USERS.value)
    return World(backend, processor, connector_id, emails, users, creator)


async def test_with_the_setting_on_each_account_holds_its_own_grants(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    records = await world.write()

    assert await world.readers(records) == {
        "by-id": {"ana"},
        "shared-address": set(),             # two live accounts share dup's address: neither is linked
        "by-address": set(),                 # ben is withdrawn
        "by-group": {"cal", "creator"},      # cal's account is a member; the creator signed in as it
    }
    accounts, _, holders = await scenario.state(world.backend.provider, world.connector_id, world.users, records)
    assert sorted(a["sourceUserId"] for a in accounts) == sorted(scenario.ACCOUNTS)
    assert {a["sourceUserId"]: a["state"] for a in accounts}["s-ben"] == "withdrawn"
    assert any(h.startswith("sourceAccounts/") for h in holders)

    owners = {u.email for u in await world.processor.get_users_with_permission_to_node(records["by-id"].id, CollectionNames.RECORDS.value)}
    assert world.emails["ana"] in owners, "a reverse lookup finds the account that holds the grant"
    # withdrawn, ana's account holds for nobody: it is no one to act as, ask or compare against
    from app.connectors.core.base.data_processor.source_accounts import account_node_id
    ana_account = account_node_id(ORG, world.connector_id, "s-ana")
    assert await world.backend.provider.update_node(ana_account, CollectionNames.SOURCE_ACCOUNTS.value, {"state": "withdrawn"})
    assert world.emails["ana"] not in {u.email for u in await world.processor.get_users_with_permission_to_node(records["by-id"].id, CollectionNames.RECORDS.value)}
    first = await world.processor.get_first_user_with_permission_to_node(records["by-id"].id, CollectionNames.RECORDS.value)
    assert first is None or first.email != world.emails["ana"]
    assert await world.backend.provider.update_node(ana_account, CollectionNames.SOURCE_ACCOUNTS.value, {"state": "listed"})

    renamed = scenario.account(world.connector_id, world.emails, "s-ana", "ana2")
    await world.processor.on_new_app_users([renamed])
    assert (await world.readers(records))["by-id"] == {"ana2"}, "the renamed account's grant follows its new address"


@pytest.mark.skipif(os.environ.get("PERM_SOURCE_ACCOUNTS") == "1", reason="the harness turns the setting on for every connector")
async def test_with_the_setting_off_nothing_new_is_written_and_everyone_reads_what_they_did(world: World) -> None:
    records = await world.write()

    accounts, links, holders = await scenario.state(world.backend.provider, world.connector_id, world.users, records)
    assert scenario.new_kinds(accounts, links, holders) == []
    assert await world.readers(records) == {
        "by-id": {"ana"},
        "shared-address": {"dup"},           # the address's user takes the grant of the last-linked account
        "by-address": set(),
        "by-group": {"cal", "creator"},
    }


async def test_deleting_the_connector_removes_its_accounts(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    records = await world.write()
    await world.backend.provider.delete_connector_instance(world.connector_id, ORG)
    accounts, links, _ = await scenario.state(world.backend.provider, world.connector_id, world.users, records)
    assert accounts == [] and [link for link in links if link.get("connectorId") == world.connector_id] == []


async def test_an_address_passed_to_a_new_account_links_it_once_the_old_one_is_withdrawn(world: World) -> None:
    """The old account keeps its grants; while both are stored the address links neither, and the new holder never reads the old grants."""
    await scenario.enable(world.processor, world.connector_id)
    records = await world.write()
    newcomer = scenario.account(world.connector_id, world.emails, "s-ana-new", "ana")
    await world.processor.on_new_app_users([newcomer])
    assert (await world.readers(records))["by-id"] == set(), "two stored accounts hold ana's address: neither is linked"

    old = scenario.account(world.connector_id, world.emails, "s-ana").model_copy(update={"id": world.users["ana"]})
    await world.processor.withdraw_app_user_access(world.connector_id, [old])
    await world.processor.on_new_app_users([newcomer])      # the next listing gives the user back its connector
    assert (await world.readers(records))["by-id"] == set(), "the old account's grant is not the new holder's"
    await world.processor.on_updated_record_permissions(records["by-id"], [scenario.grant(world.emails["ana"], "s-ana-new")])
    assert (await world.readers(records))["by-id"] == {"ana"}, "the new account's own grant"


async def test_a_deleted_user_reads_nothing_through_its_account_and_a_restore_gives_it_back(world: World) -> None:
    """Soft delete and restore through the entity events: while deleted, neither the user's check nor its search scope reaches its account's
    grants; restored, both do again. The account itself is kept."""
    await scenario.enable(world.processor, world.connector_id)
    records = await world.write()
    cal, user_id = world.users["cal"], f"uid-cal-{uuid.uuid4().hex[:6]}"
    await world.backend.provider.batch_upsert_nodes([{"_key": cal, "userId": user_id, "email": world.emails["cal"], "orgId": ORG, "isActive": True,
                                                      "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], collection=CollectionNames.USERS.value)
    grant_of_account = records["by-group"].id

    async def reads() -> tuple[bool, bool]:
        checked = grant_of_account in set((await world.backend.provider.check_access(cal, ORG, node_ids=[grant_of_account])).node_ids)
        scope = await world.backend.provider.get_accessible_virtual_record_ids(user_id, ORG)
        return checked, grant_of_account in (scope or {}).values()

    await scenario.mark_indexed(world.backend.provider, records["by-group"])
    assert await reads() == (True, True)

    async def deleted() -> None:
        assert await reads() == (False, False)
        await scenario.a_deleted_user_is_not_linked_again(world.processor, world.backend.provider, world.connector_id, world.emails, world.users,
                                                          user_id)
        assert await reads() == (False, False), "nor does the next listing or its own sign-in give it back"

    async def restored() -> None:
        assert await reads() == (True, True)

    await scenario.delete_and_restore(world.backend.provider, world.processor.config_service, ORG, world.emails["cal"], user_id, deleted, restored)
    accounts, _, _ = await scenario.state(world.backend.provider, world.connector_id, world.users, records)
    assert "s-cal" in {a["sourceUserId"] for a in accounts}


async def test_a_mail_thread_owned_by_a_linked_account_is_the_users(world: World) -> None:
    from app.config.constants.arangodb import Connectors, OriginTypes
    from app.connectors.core.base.data_processor.source_accounts import account_node_id
    from app.models.entities import MailRecord, RecordType

    await scenario.enable(world.processor, world.connector_id)
    await world.write()
    mail = MailRecord(id=str(uuid.uuid4()), org_id=ORG, record_name="thread", external_record_id=f"m-{_suffix()}", version=1,
                      origin=OriginTypes.CONNECTOR, connector_name=Connectors.CONFLUENCE, connector_id=world.connector_id,
                      record_type=RecordType.MAIL, thread_id="t1", conversation_index=f"ci-{_suffix()}")
    await world.backend.provider.batch_upsert_records([mail])
    await world.backend.provider.batch_create_edges([{
        "from_id": account_node_id(ORG, world.connector_id, "s-cal"), "from_collection": CollectionNames.SOURCE_ACCOUNTS.value,
        "to_id": mail.id, "to_collection": CollectionNames.RECORDS.value, "type": "USER", "role": "OWNER", "createdAtTimestamp": TS,
    }], collection=CollectionNames.PERMISSION.value)
    ids = {person: f"uid-{person}-{_suffix()}" for person in ("cal", "ana")}
    for person, uid in ids.items():
        await world.backend.provider.batch_upsert_nodes([{"_key": world.users[person], "userId": uid, "email": world.emails[person], "orgId": ORG,
                                                          "isActive": True, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
                                                        collection=CollectionNames.USERS.value)
    owned = await world.backend.provider.get_record_by_conversation_index(world.connector_id, mail.conversation_index, "t1", ORG, ids["cal"])
    assert owned is not None and owned.id == mail.id, "cal is linked to the account that owns the thread"
    assert await world.backend.provider.get_record_by_conversation_index(world.connector_id, mail.conversation_index, "t1", ORG, ids["ana"]) is None
    # a withdrawn account links nobody: its OWNER grant and the link stay, the thread is not cal's
    assert await world.backend.provider.update_node(account_node_id(ORG, world.connector_id, "s-cal"), CollectionNames.SOURCE_ACCOUNTS.value,
                                                    {"state": "withdrawn"})
    assert await world.backend.provider.get_record_by_conversation_index(world.connector_id, mail.conversation_index, "t1", ORG, ids["cal"]) is None


async def test_the_authenticator_link_survives_every_relink_of_its_pair(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    await world.write()
    cal_user_id = f"uid-cal-{uuid.uuid4().hex[:6]}"
    await world.backend.provider.batch_upsert_nodes([{"_key": world.users["cal"], "userId": cal_user_id, "email": world.emails["cal"], "orgId": ORG,
                                                      "isActive": True, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
                                                    collection=CollectionNames.USERS.value)
    await scenario.authenticator_survives_relinks(world.processor, world.backend.provider, world.connector_id, ORG, world.emails, world.users, cal_user_id)


async def test_a_user_of_another_org_with_the_same_address_is_never_linked(world: World) -> None:
    """The other org's user is written first and its key sorts first, so an unscoped read by address answers it."""
    await scenario.enable(world.processor, world.connector_id)
    provider, ana = world.backend.provider, world.users["ana"]
    foreign = f"a0-foreign-{_suffix()}"
    await provider.delete_nodes_and_edges([ana], CollectionNames.USERS.value)
    for key, org in ((foreign, f"org-other-{_suffix()}"), (ana, ORG)):
        await provider.batch_upsert_nodes([{"_key": key, "email": world.emails["ana"], "orgId": org, "isActive": True,
                                            "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], collection=CollectionNames.USERS.value)
    try:
        records = await world.write()
        await scenario.another_orgs_user_is_not_linked(provider, ORG, world.connector_id, world.users, foreign)
        assert "ana" in (await world.readers(records))["by-id"]
    finally:
        await provider.delete_nodes_and_edges([foreign], CollectionNames.USERS.value)


async def test_an_old_accounts_role_memberships_never_reach_the_new_holder_of_its_address(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    await world.write()
    await scenario.role_memberships_stay_with_the_old_account(world.processor, world.backend.provider, world.connector_id, ORG, world.emails, world.users)


async def test_a_revoked_grant_and_a_removed_membership_are_taken_from_the_account(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    records = await world.write()
    await scenario.revocations_reach_the_account(world.processor, world.backend.provider, world.connector_id, world.emails, records)
    readers = await world.readers(records)
    assert "ana" not in readers["by-id"] and "cal" not in readers["by-group"]


async def test_a_deactivated_signer_keeps_no_authenticator_link(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    await world.write()
    cal_user_id = f"uid-cal-{uuid.uuid4().hex[:6]}"
    await world.backend.provider.batch_upsert_nodes([{"_key": world.users["cal"], "userId": cal_user_id, "email": world.emails["cal"], "orgId": ORG,
                                                      "isActive": True, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
                                                    collection=CollectionNames.USERS.value)
    await scenario.a_deactivated_signer_keeps_no_link(world.processor, world.backend.provider, world.connector_id, world.emails, world.users, cal_user_id)


async def test_a_relink_for_an_address_the_user_no_longer_holds_writes_nothing(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    await world.write()
    await scenario.a_stale_address_links_nothing(world.backend.provider, world.connector_id, ORG, world.emails, world.users)


async def test_an_address_change_moves_the_directory_link(world: World) -> None:
    await scenario.enable(world.processor, world.connector_id)
    await world.write()
    cal_user_id = f"uid-cal-{uuid.uuid4().hex[:6]}"
    await world.backend.provider.batch_upsert_nodes([{"_key": world.users["cal"], "userId": cal_user_id, "email": world.emails["cal"], "orgId": ORG,
                                                      "isActive": True, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
                                                    collection=CollectionNames.USERS.value)
    await scenario.an_address_change_moves_the_directory_link(world.backend.provider, world.processor.config_service, ORG, world.connector_id,
                                                              world.emails, world.users, cal_user_id)
