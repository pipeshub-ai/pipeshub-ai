"""A connector's accounts kept as SourceAccount nodes, through the real processor and the real access check, on PostgreSQL.

The same scenario as ``tests/integration/graph_permissions/connectors/test_source_account_writers.py`` (Neo4j and ArangoDB), plus the
delete-right check: the setting on gives each listed account a node of its own; off (the default) writes nothing of that.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from tests.integration.graph_permissions import source_account_writes as scenario
from tests.support.in_memory_config import InMemoryConfigService

from .test_access import World

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

APP = "conn-accounts"


class Setup:
    def __init__(self, w: World, processor: DataSourceEntitiesProcessor, emails: dict[str, str], users: dict[str, str]) -> None:
        self.w, self.processor, self.emails, self.users = w, processor, emails, users

    async def write(self) -> dict:
        return await scenario.write(self.processor, APP, self.w.org, self.emails, self.users, "uid-creator")

    async def readers(self, records: dict) -> dict[str, set[str]]:
        async def allowed(user_key: str, node_id: str) -> bool:
            return bool(await self.w.allowed(user_key, node_id))
        return await scenario.readers(records, self.users, allowed)


@pytest.fixture
async def setup(pg_provider, pg_db, org_ctx) -> AsyncIterator[Setup]:
    org = uuid.uuid4().hex[:24]
    with org_ctx(org):
        w = World(pg_provider, org)
        await w.nodes("organizations", {"_key": org, "isActive": True, "accountType": "enterprise"})
        await w.nodes("apps", {"_key": APP, "type": scenario.APP.value, "isActive": True, "createdBy": "uid-creator"})
        emails = {person: f"{person}@acme.example" for person in scenario.PEOPLE}
        users = {person: f"u-{person}" for person in scenario.PEOPLE}
        users["creator"] = "u-creator"
        for person, key in users.items():
            await w.nodes("users", {"_key": key, "userId": f"uid-{person}", "email": emails.get(person, "creator@acme.example"), "isActive": True})
            await w.edge("belongsTo", ("users", key), ("organizations", org), entityType="ORGANIZATION")
        processor = DataSourceEntitiesProcessor(logging.getLogger("t"), GraphDataStore(logging.getLogger("t"), pg_provider), InMemoryConfigService())
        processor.org_id = org
        processor.messaging_producer = MagicMock(send_message=AsyncMock(return_value=True), send_messages=AsyncMock(return_value=[]))
        yield Setup(w, processor, emails, users)
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_with_the_setting_on_each_account_holds_its_own_grants(setup: Setup) -> None:
    await scenario.enable(setup.processor, APP)
    records = await setup.write()

    assert await setup.readers(records) == {
        "by-id": {"ana"}, "shared-address": set(), "by-address": set(), "by-group": {"cal", "creator"},
    }
    accounts, _, holders = await scenario.state(setup.w.p, APP, setup.users, records)
    assert sorted(a["sourceUserId"] for a in accounts) == sorted(scenario.ACCOUNTS)
    assert {a["sourceUserId"]: a["state"] for a in accounts}["s-ben"] == "withdrawn"
    assert any(h.startswith("sourceAccounts/") for h in holders)

    owners = {u.email for u in await setup.processor.get_users_with_permission_to_node(records["by-id"].id, CollectionNames.RECORDS.value)}
    assert setup.emails["ana"] in owners, "a reverse lookup finds the account that holds the grant"
    assert await setup.w.p.get_record_owner_source_user_email(records["by-id"].id) == setup.emails["ana"]
    refusal = await setup.w.p._refuse_without_delete_role(records["by-id"].id, {"connectorId": APP, "connectorName": scenario.APP.value},
                                                         "uid-ana", None)
    assert refusal is None, "the OWNER grant of the account ana is linked to lets ana delete"

    await setup.processor.on_new_app_users([scenario.account(APP, setup.emails, "s-ana", "ana2")])
    assert (await setup.readers(records))["by-id"] == {"ana2"}, "the renamed account's grant follows its new address"


@pytest.mark.skipif(os.environ.get("PERM_SOURCE_ACCOUNTS") == "1", reason="the in-memory configuration turns the setting on for every connector")
async def test_with_the_setting_off_nothing_new_is_written_and_everyone_reads_what_they_did(setup: Setup) -> None:
    records = await setup.write()

    accounts, links, holders = await scenario.state(setup.w.p, APP, setup.users, records)
    assert scenario.new_kinds(accounts, links, holders) == []
    assert await setup.readers(records) == {
        "by-id": {"ana"}, "shared-address": {"dup"}, "by-address": set(), "by-group": {"cal", "creator"},
    }


async def test_deleting_the_connector_removes_its_accounts(setup: Setup) -> None:
    await scenario.enable(setup.processor, APP)
    records = await setup.write()
    await setup.w.p.delete_connector_instance(APP, setup.w.org)
    accounts, links, _ = await scenario.state(setup.w.p, APP, setup.users, records)
    assert accounts == [] and [link for link in links if link.get("connectorId") == APP] == []


async def test_an_address_passed_to_a_new_account_links_it_once_the_old_one_is_withdrawn(setup: Setup) -> None:
    """The old account keeps its grants; while both are stored the address links neither, and the new holder never reads the old grants."""
    await scenario.enable(setup.processor, APP)
    records = await setup.write()
    newcomer = scenario.account(APP, setup.emails, "s-ana-new", "ana")
    await setup.processor.on_new_app_users([newcomer])
    assert (await setup.readers(records))["by-id"] == set(), "two stored accounts hold ana's address: neither is linked"

    await setup.processor.withdraw_app_user_access(APP, [scenario.account(APP, setup.emails, "s-ana").model_copy(update={"id": setup.users["ana"]})])
    await setup.processor.on_new_app_users([newcomer])
    assert (await setup.readers(records))["by-id"] == set(), "the old account's grant is not the new holder's"
    await setup.processor.on_updated_record_permissions(records["by-id"], [scenario.grant(setup.emails["ana"], "s-ana-new")])
    assert (await setup.readers(records))["by-id"] == {"ana"}, "the new account's own grant"


async def test_a_deleted_user_reads_nothing_through_its_account_and_a_restore_gives_it_back(setup: Setup) -> None:
    """Soft delete and restore through the entity events: while deleted, neither the user's check nor its search scope reaches its account's
    grants; restored, both do again. The account itself is kept."""
    await scenario.enable(setup.processor, APP)
    records = await setup.write()
    cal, grant_of_account = setup.users["cal"], records["by-group"].id

    async def reads() -> tuple[bool, bool]:
        scope = await setup.w.p.get_accessible_virtual_record_ids("uid-cal", setup.w.org)
        return bool(await setup.w.allowed(cal, grant_of_account)), grant_of_account in (scope or {}).values()

    await scenario.mark_indexed(setup.w.p, records["by-group"])
    assert await reads() == (True, True)

    async def deleted() -> None:
        assert await reads() == (False, False)

    async def restored() -> None:
        assert await reads() == (True, True)

    await scenario.delete_and_restore(setup.w.p, setup.processor.config_service, setup.w.org, setup.emails["cal"], "uid-cal", deleted, restored)
    accounts, _, _ = await scenario.state(setup.w.p, APP, setup.users, records)
    assert "s-cal" in {a["sourceUserId"] for a in accounts}
