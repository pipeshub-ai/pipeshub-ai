"""A new Entra account that takes a deleted account's address (a new object id, the same UPN) reads nothing PipesHub
granted the old one. Microsoft: "The new account is assigned a new ID value even though the UPN is the same", and
SharePoint and OneDrive keep only the old ID
(https://learn.microsoft.com/en-us/troubleshoot/sharepoint/sharing-and-permissions/fix-site-user-id-mismatch)."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from ms_graph_fakes import FakeConfigService

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.common.source_accounts import (
    SourceAccounts,
    source_accounts_key,
)
from app.models.entities import AppUser

CONNECTOR = "c1"


def _user(email: str, sid: str) -> AppUser:
    return AppUser(app_name=Connectors.ONEDRIVE, connector_id=CONNECTOR, source_user_id=sid, email=email, full_name=email)


class _Directory:
    """PipesHub's side: one user per address, and the account each user is linked to (``userAppRelation``)."""

    def __init__(self) -> None:
        self.nodes: dict[str, str] = {}      # email -> PipesHub user id
        self.links: dict[str, str] = {}      # PipesHub user id -> source account id
        self.calls: list[tuple] = []
        self.processor = MagicMock()
        self.processor.get_app_user_links = AsyncMock(side_effect=lambda _c: [
            SimpleNamespace(id=i, source_user_id=s) for i, s in self.links.items()])
        self.processor.get_user_by_email = AsyncMock(side_effect=self._by_email)
        self.processor.remove_user_access_to_connector = AsyncMock(side_effect=self._remove)
        self.processor.on_new_app_users = AsyncMock(side_effect=self._link)
        self.processor.withdraw_app_user_access = AsyncMock(side_effect=self._withdraw)

    async def _by_email(self, email: str, **_: object) -> object | None:
        node = self.nodes.get(email.lower())
        return SimpleNamespace(id=node, email=email) if node else None

    async def _remove(self, connector_id: str, email: str) -> int:
        self.calls.append(("remove", email))
        return 3

    async def _link(self, users: list[AppUser]) -> None:
        self.calls.append(("link", [u.source_user_id for u in users]))
        for u in users:
            node = self.nodes.setdefault(u.email.lower(), f"node-{u.email}")
            self.links[node] = u.source_user_id

    async def _withdraw(self, connector_id: str, users: list) -> None:
        for u in users:
            self.links.pop(u.id, None)


class _Saved:
    def __init__(self, config: FakeConfigService) -> None:
        self.config = config

    def values(self) -> dict:
        return self.config.saved.get(source_accounts_key(CONNECTOR)) or {}


@pytest.fixture
def world() -> tuple[_Directory, SourceAccounts, _Saved]:
    config = FakeConfigService(CONNECTOR, {})
    directory = _Directory()
    return directory, SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t")), _Saved(config)


async def _withdraw_gone(directory: _Directory, accounts: SourceAccounts, sid: str) -> None:
    await accounts.withdraw([link for link in await directory.processor.get_app_user_links(CONNECTOR) if link.source_user_id == sid])


@pytest.mark.asyncio
async def test_an_address_taken_by_a_new_account_in_the_same_run_loses_the_old_accounts_grants_first(world) -> None:
    directory, accounts, _ = world
    await accounts.admit([_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    directory.calls.clear()

    await accounts.admit([_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls == [("remove", "ana@x.com"), ("link", ["new-oid", "bo-oid"])]


@pytest.mark.asyncio
async def test_an_address_taken_after_the_old_account_was_withdrawn_loses_its_grants_too(world) -> None:
    directory, accounts, store = world
    await accounts.admit([_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    await _withdraw_gone(directory, accounts, "old-oid")          # deleted: a later run
    await accounts.admit([_user("bo@x.com", "bo-oid")])
    directory.calls.clear()

    await accounts.admit([_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert store.values() == {"withdrawn": {}, "reused": {"ana@x.com": "new-oid"}}


@pytest.mark.asyncio
async def test_a_restored_account_keeps_its_grants(world) -> None:
    directory, accounts, store = world
    await accounts.admit([_user("ana@x.com", "ana-oid")])
    await _withdraw_gone(directory, accounts, "ana-oid")
    assert store.values()["withdrawn"] == {"node-ana@x.com": "ana-oid"}

    await accounts.admit([_user("ana@x.com", "ana-oid")])

    assert ("remove", "ana@x.com") not in directory.calls
    assert store.values()["withdrawn"] == {}
    assert accounts.may_grant("ana@x.com", "ana-oid") and accounts.may_grant("ana@x.com", None)


@pytest.mark.asyncio
async def test_a_grant_reaches_only_the_account_that_holds_the_address_now(world) -> None:
    _, accounts, _ = world
    await accounts.admit([_user("ana@x.com", "old-oid")])
    await accounts.admit([_user("ana@x.com", "new-oid")])

    assert accounts.may_grant("ANA@x.com", "new-oid")
    assert not accounts.may_grant("ana@x.com", "old-oid")       # a Graph grant to the deleted account
    assert not accounts.may_grant("ana@x.com", None)            # a SharePoint principal: old or new cannot be told
    assert accounts.may_grant("guest@elsewhere.com", "ext-1")   # not in the directory: as before
    assert accounts.may_grant("bo@x.com", None)


@pytest.mark.asyncio
async def test_the_first_run_looks_nobody_up(world) -> None:
    directory, accounts, _ = world
    await accounts.admit([_user("ana@x.com", "a"), _user("bo@x.com", "b")])
    directory.processor.get_user_by_email.assert_not_awaited()
    directory.processor.remove_user_access_to_connector.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_read_links_nobody(world) -> None:
    directory, accounts, _ = world
    directory.processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    await accounts.admit([_user("ana@x.com", "a")])
    directory.processor.on_new_app_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_grant_removal_links_nobody(world) -> None:
    directory, accounts, _ = world
    await accounts.admit([_user("ana@x.com", "old-oid")])
    directory.processor.remove_user_access_to_connector = AsyncMock(side_effect=RuntimeError("db down"))
    directory.calls.clear()
    with pytest.raises(RuntimeError):
        await accounts.admit([_user("ana@x.com", "new-oid")])
    assert directory.calls == []
    assert directory.links == {"node-ana@x.com": "old-oid"}


@pytest.mark.asyncio
async def test_what_it_knows_outlives_a_full_resync(world) -> None:
    """A full resync deletes the sync points and reads every grant again by address; the state is not one of them."""
    directory, accounts, store = world
    await accounts.admit([_user("ana@x.com", "old-oid")])
    await accounts.admit([_user("ana@x.com", "new-oid")])
    again = SourceAccounts(directory.processor, CONNECTOR, store.config, logging.getLogger("t"))   # a new connector
    await again.admit([_user("ana@x.com", "new-oid")])
    assert not again.may_grant("ana@x.com", None) and not again.may_grant("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_a_state_that_cannot_be_saved_links_nobody(world) -> None:
    directory, accounts, store = world
    store.config.set_config = AsyncMock(return_value=False)     # the real service answers False, it does not raise
    with pytest.raises(RuntimeError):
        await accounts.admit([_user("ana@x.com", "a")])
    directory.processor.on_new_app_users.assert_not_awaited()
