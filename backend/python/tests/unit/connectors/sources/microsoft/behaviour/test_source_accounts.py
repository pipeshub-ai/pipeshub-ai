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
            SimpleNamespace(id=i, source_user_id=s, email=self.email_of(i)) for i, s in self.links.items()])
        self.processor.get_user_by_email = AsyncMock(side_effect=self._by_email)
        self.processor.remove_user_access_to_connector = AsyncMock(side_effect=self._remove)
        self.processor.on_new_app_users = AsyncMock(side_effect=self._link)
        self.processor.withdraw_app_user_access = AsyncMock(side_effect=self._withdraw)

    def email_of(self, user_id: str) -> str | None:
        return next((e for e, n in self.nodes.items() if n == user_id), None)

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
    directory.deleted: dict[str, str] = {}      # the directory's deleted items: address -> object id

    async def deleted_users() -> dict[str, str]:
        return dict(directory.deleted)

    accounts = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"), deleted_users=deleted_users)
    return directory, accounts, _Saved(config)


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
    assert (store.values()["withdrawn"], store.values()["reused"]) == ({}, {"ana@x.com": "new-oid"})


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



@pytest.mark.asyncio
async def test_a_run_whose_links_cannot_be_read_writes_no_grant_until_one_can(world) -> None:
    """sh7X: an earlier run's listing must not vouch for an object id while this run could not check the links."""
    directory, accounts, _ = world
    await accounts.admit([_user("ana@x.com", "a")])
    assert accounts.may_grant("ana@x.com", "a")
    directory.processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    await accounts.admit([_user("ana@x.com", "b")])
    assert not accounts.may_grant("ana@x.com", "a") and not accounts.may_grant("ana@x.com", "b")
    with pytest.raises(RuntimeError):
        await accounts.ensure_loaded()             # the grant writers stop: nothing is rewritten this run
    directory.processor.get_app_user_links = AsyncMock(side_effect=lambda _c: [
        SimpleNamespace(id=i, source_user_id=sid, email=directory.email_of(i)) for i, sid in directory.links.items()])
    await accounts.admit([_user("ana@x.com", "a")])
    await accounts.ensure_loaded()
    assert accounts.may_grant("ana@x.com", "a")


@pytest.mark.asyncio
async def test_an_account_renamed_onto_a_deleted_accounts_address_is_compared_by_address(world) -> None:
    """sh7Y: B moves from bo@ to ana@ after A (ana@) was deleted. B's id is linked already, to bo@'s user, which says
    nothing about ana@: ana@'s user was last linked to A."""
    directory, accounts, store = world
    await accounts.admit([_user("ana@x.com", "a"), _user("bo@x.com", "b")])
    await _withdraw_gone(directory, accounts, "a")
    directory.calls.clear()

    await accounts.admit([_user("ana@x.com", "b")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert store.values()["reused"] == {"ana@x.com": "b"}
    assert not accounts.may_grant("ana@x.com", "a") and not accounts.may_grant("ana@x.com", None)


@pytest.mark.asyncio
async def test_an_address_an_earlier_version_already_linked_to_its_new_account_is_found_in_deleted_items(world) -> None:
    """swj_: the stored link already names the new id (written before this state was kept) and nothing is saved; the
    deleted account in the directory's deleted items still holds the address under the old id."""
    directory, accounts, store = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    directory.deleted = {"ana@x.com": "old-oid"}

    await accounts.admit([_user("ana@x.com", "new-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert store.values()["reused"] == {"ana@x.com": "new-oid"} and store.values()["deletedChecked"] is True
    assert not accounts.may_grant("ana@x.com", "old-oid") and not accounts.may_grant("ana@x.com", None)
    assert accounts.may_grant("ana@x.com", "new-oid")
    directory.calls.clear()
    directory.deleted = {}
    await accounts.admit([_user("ana@x.com", "new-oid")])      # read once: no second removal
    assert ("remove", "ana@x.com") not in directory.calls


@pytest.mark.asyncio
async def test_deleted_items_that_cannot_be_read_are_read_again_next_run(world) -> None:
    directory, accounts, store = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    accounts.deleted_users = AsyncMock(side_effect=RuntimeError("403"))
    await accounts.admit([_user("ana@x.com", "new-oid")])
    assert store.values()["deletedChecked"] is False


@pytest.mark.asyncio
async def test_a_restored_account_keeps_its_old_id_until_it_is_linked(world) -> None:
    """sh7a: the link write fails after the comparison; the withdrawn id must still be there for the next run."""
    directory, accounts, store = world
    await accounts.admit([_user("ana@x.com", "a")])
    await _withdraw_gone(directory, accounts, "a")
    directory.processor.on_new_app_users = AsyncMock(side_effect=RuntimeError("write failed"))
    with pytest.raises(RuntimeError):
        await accounts.admit([_user("ana@x.com", "a")])
    assert store.values()["withdrawn"] == {"node-ana@x.com": "a"}


@pytest.mark.asyncio
async def test_while_deleted_items_cannot_be_read_a_kept_address_gets_no_object_less_grant(world) -> None:
    """uYi7: an address whose link names the listed id may still be one an earlier version moved to a new account; until
    deleted items are read, a grant that names no account (a SharePoint principal) is refused for it."""
    directory, accounts, store = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    accounts.deleted_users = AsyncMock(side_effect=RuntimeError("503"))
    await accounts.admit([_user("ana@x.com", "new-oid")])
    assert not accounts.may_grant("ana@x.com", None) and accounts.may_grant("ana@x.com", "new-oid")
    assert store.values()["unverified"] == ["ana@x.com"]

    fresh = SourceAccounts(directory.processor, CONNECTOR, store.config, logging.getLogger("t"))     # a reindex worker
    await fresh.ensure_loaded()
    assert not fresh.may_grant("ana@x.com", None)

    accounts.deleted_users = AsyncMock(return_value={})
    await accounts.admit([_user("ana@x.com", "new-oid")])
    assert accounts.may_grant("ana@x.com", None) and store.values()["unverified"] == []


@pytest.mark.asyncio
async def test_deleted_items_evidence_waits_for_the_listing_that_shows_its_address(world) -> None:
    """uYi_: the deleted users are read once; an address absent from that run's listing keeps its evidence until a later
    listing shows it."""
    directory, accounts, store = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    directory.deleted = {"ana@x.com": "old-oid"}
    await accounts.admit([_user("bo@x.com", "bo-oid")])                 # ana@ is not in this listing
    assert store.values()["deleted"] == {"ana@x.com": "old-oid"}
    directory.deleted = {}
    directory.calls.clear()

    await accounts.admit([_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert store.values()["deleted"] == {} and store.values()["reused"] == {"ana@x.com": "new-oid"}


@pytest.mark.asyncio
async def test_a_withdrawn_account_is_also_remembered_by_address_across_a_user_merge(world) -> None:
    """uYjB: a verified sign-in merges the synced user into the login user (another id); the withdrawn record of the old
    account still applies to that address."""
    directory, accounts, store = world
    await accounts.admit([_user("ana@x.com", "old-oid")])
    await _withdraw_gone(directory, accounts, "old-oid")
    directory.nodes["ana@x.com"] = "login-user"                        # the merge: the stub is gone
    directory.calls.clear()

    await accounts.admit([_user("ana@x.com", "new-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert store.values()["withdrawnByAddress"] == {}


@pytest.mark.asyncio
async def test_a_state_another_instance_saved_is_read_again_before_a_later_grant(world, monkeypatch) -> None:
    """uYjI: a reindex worker loaded the state; a sync elsewhere admits a new account for ana@; the worker's next grant
    decision, once its copy is older than STATE_MAX_AGE_SECONDS, reads the new state."""
    from app.connectors.sources.microsoft.common import source_accounts as module

    directory, accounts, store = world
    worker = SourceAccounts(directory.processor, CONNECTOR, store.config, logging.getLogger("t"))
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    await worker.ensure_loaded()
    assert worker.may_grant("ana@x.com", "old-oid")
    await accounts.admit([_user("ana@x.com", "old-oid")])
    await accounts.admit([_user("ana@x.com", "new-oid")])                # another instance re-links ana@
    now[0] += module.STATE_MAX_AGE_SECONDS + 1
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_recheck_reads_the_state_again_and_drops_refused_user_grants(world) -> None:
    """uYjG: a batch judged before an admission is checked again just before it is written."""
    from app.models.permission import EntityType, Permission, PermissionType

    directory, accounts, store = world
    worker = SourceAccounts(directory.processor, CONNECTOR, store.config, logging.getLogger("t"))
    await worker.ensure_loaded()
    grants = [Permission(external_id="old-oid", email="ana@x.com", type=PermissionType.READ, entity_type=EntityType.USER),
              Permission(external_id="g-1", email=None, type=PermissionType.READ, entity_type=EntityType.GROUP),
              Permission(external_id="bo-oid", email="bo@x.com", type=PermissionType.READ, entity_type=EntityType.USER)]
    await accounts.admit([_user("ana@x.com", "old-oid")])
    await accounts.admit([_user("ana@x.com", "new-oid")])                # admitted while the batch was being read
    assert [p.external_id for p in await worker.recheck(grants)] == ["g-1", "bo-oid"]
