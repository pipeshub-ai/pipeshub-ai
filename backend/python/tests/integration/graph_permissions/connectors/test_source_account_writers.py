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
