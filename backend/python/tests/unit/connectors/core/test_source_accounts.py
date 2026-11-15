"""A new source account that takes a deleted account's address (a new source id, the same address) reads nothing
PipesHub granted the old one, on every connector whose users are accounts of the source. Microsoft, for one: "The new
account is assigned a new ID value even though the UPN is the same"
(https://learn.microsoft.com/en-us/troubleshoot/sharepoint/sharing-and-permissions/fix-site-user-id-mismatch)."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_processor.source_accounts import (
    SourceAccounts,
    source_accounts_key,
)
from app.models.entities import AppUser
from app.models.permission import EntityType, Permission, PermissionType

CONNECTOR = "c1"


def _user(email: str, sid: str, app: Connectors = Connectors.JIRA) -> AppUser:
    return AppUser(app_name=app, connector_id=CONNECTOR, source_user_id=sid, email=email, full_name=email)


class _Config:
    def __init__(self) -> None:
        self.saved: dict[str, object] = {}
        self.reads = 0

    async def get_config(self, key: str, default: object = None, **_: object) -> object:
        self.reads += 1
        return self.saved.get(key, default)

    async def set_config(self, key: str, value: object, **_: object) -> bool:
        self.saved[key] = value
        return True


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

    async def _by_email(self, email: str, **_: object) -> object | None:
        node = self.nodes.get(email.lower())
        return SimpleNamespace(id=node, email=email) if node else None

    async def _remove(self, connector_id: str, email: str) -> int:
        self.calls.append(("remove", email))
        return 3

    def link(self, users: list[AppUser]) -> None:
        self.calls.append(("link", [u.source_user_id for u in users]))
        for u in users:
            node = self.nodes.setdefault(u.email.lower(), f"node-{u.email}")
            self.links[node] = u.source_user_id


@pytest.fixture
def world() -> tuple[_Directory, SourceAccounts, _Config]:
    config = _Config()
    directory = _Directory()
    return directory, SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t")), config


async def _admit(directory: _Directory, accounts: SourceAccounts, users: list[AppUser]) -> None:
    """What ``on_new_app_users`` does: the check, then the link."""
    if await accounts.before_link(users):
        directory.link(users)


async def _withdraw(directory: _Directory, accounts: SourceAccounts, sid: str) -> None:
    gone = [link for link in await directory.processor.get_app_user_links(CONNECTOR) if link.source_user_id == sid]
    await accounts.note_withdrawn(gone)
    for u in gone:
        directory.links.pop(u.id, None)


def _saved(config: _Config) -> dict:
    return config.saved.get(source_accounts_key(CONNECTOR)) or {}


@pytest.mark.asyncio
async def test_an_address_taken_by_a_new_account_in_the_same_run_loses_the_old_accounts_grants_first(world) -> None:
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls == [("remove", "ana@x.com"), ("link", ["new-oid", "bo-oid"])]


@pytest.mark.asyncio
async def test_an_address_taken_after_the_old_account_was_withdrawn_loses_its_grants_too(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    await _withdraw(directory, accounts, "old-oid")          # deleted: a later run
    await _admit(directory, accounts, [_user("bo@x.com", "bo-oid")])
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["withdrawn"] == {} and _saved(config)["reused"] == {"ana@x.com": "new-oid"}


@pytest.mark.asyncio
async def test_a_restored_account_keeps_its_grants(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])
    await _withdraw(directory, accounts, "ana-oid")
    assert _saved(config)["withdrawn"] == {"node-ana@x.com": "ana-oid"}

    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])

    assert ("remove", "ana@x.com") not in directory.calls
    assert _saved(config)["withdrawn"] == {}
    assert accounts.may_grant("ana@x.com", "ana-oid") and accounts.may_grant("ana@x.com", None)


@pytest.mark.asyncio
async def test_a_grant_reaches_only_the_account_that_holds_the_address_now(world) -> None:
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])

    assert accounts.may_grant("ANA@x.com", "new-oid")
    assert not accounts.may_grant("ana@x.com", "old-oid")       # a grant to the deleted account
    assert not accounts.may_grant("ana@x.com", None)            # names no account: old or new cannot be told
    assert accounts.may_grant("guest@elsewhere.com", "ext-1")   # not in the directory: as before
    assert accounts.may_grant("bo@x.com", None)


@pytest.mark.asyncio
async def test_the_first_run_looks_nobody_up_and_notes_who_holds_each_address(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "a"), _user("bo@x.com", "b")])
    directory.processor.get_user_by_email.assert_not_awaited()
    directory.processor.remove_user_access_to_connector.assert_not_awaited()
    assert _saved(config) == {"withdrawn": {}, "reused": {}, "linked": {"ana@x.com": "a", "bo@x.com": "b"}}


@pytest.mark.asyncio
async def test_a_run_that_changes_nothing_looks_nobody_up_and_saves_nothing(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "a"), _user("bo@x.com", "b")])
    directory.processor.get_user_by_email.reset_mock()
    config.set_config = AsyncMock(side_effect=AssertionError("nothing changed, nothing saved"))
    await _admit(directory, accounts, [_user("ana@x.com", "a"), _user("bo@x.com", "b")])
    directory.processor.get_user_by_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_address_whose_stored_link_already_names_the_new_account_still_loses_the_old_grants(world) -> None:
    """The link was rewritten to the new id outside this check (an earlier release, another writer): the id this
    connector last linked the address to tells the change of hands, not the link."""
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    directory.links["node-ana@x.com"] = "new-oid"
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"}
    assert not accounts.may_grant("ana@x.com", "old-oid") and accounts.may_grant("ana@x.com", "new-oid")


@pytest.mark.asyncio
async def test_a_withdrawn_account_whose_address_was_relinked_to_a_new_account_outside_the_check_is_caught(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    await _withdraw(directory, accounts, "old-oid")
    directory.links["node-ana@x.com"] = "new-oid"            # linked again, as the new account, without the check
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"} and _saved(config)["withdrawn"] == {}


@pytest.mark.asyncio
async def test_after_a_restart_a_grant_naming_another_account_than_the_one_linked_is_not_written(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])
    again = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))   # a new process
    await again.ensure_loaded()
    assert again.may_grant("ana@x.com", "ana-oid")
    assert not again.may_grant("ana@x.com", "someone-else")


@pytest.mark.asyncio
async def test_a_stand_in_creator_id_is_not_an_account(world) -> None:
    """connector_service links a connector's creator as ``creator_<id>``: the real account id replacing it, or the
    other way round, is no change of hands."""
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "creator_u1"), _user("bo@x.com", "b")])
    await _admit(directory, accounts, [_user("ana@x.com", "acc-1"), _user("bo@x.com", "b")])
    await _admit(directory, accounts, [_user("ana@x.com", "creator_u1")])
    directory.processor.remove_user_access_to_connector.assert_not_awaited()
    assert accounts.may_grant("ana@x.com", None)


@pytest.mark.asyncio
async def test_a_failed_read_links_nobody(world) -> None:
    directory, accounts, _ = world
    directory.processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    assert await accounts.before_link([_user("ana@x.com", "a")]) is False


@pytest.mark.asyncio
async def test_a_failed_grant_removal_links_nobody(world) -> None:
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    directory.processor.remove_user_access_to_connector = AsyncMock(side_effect=RuntimeError("db down"))
    directory.calls.clear()
    with pytest.raises(RuntimeError):
        await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    assert directory.calls == []
    assert directory.links == {"node-ana@x.com": "old-oid"}


@pytest.mark.asyncio
async def test_what_it_knows_outlives_a_full_resync(world) -> None:
    """A full resync deletes the sync points and reads every grant again by address; the state is not one of them."""
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    again = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))   # a new process
    assert not again.may_grant("ana@x.com", "new-oid")          # nothing is judged before the state is read
    await again.ensure_loaded()
    assert again.reused == {"ana@x.com": "new-oid"}
    assert again.may_grant("ana@x.com", "new-oid")
    assert not again.may_grant("ana@x.com", None) and not again.may_grant("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_a_state_that_cannot_be_saved_links_nobody(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    config.set_config = AsyncMock(return_value=False)     # the real service answers False, it does not raise
    directory.calls.clear()
    with pytest.raises(RuntimeError):
        await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    assert ("link", ["new-oid"]) not in directory.calls


@pytest.mark.asyncio
async def test_a_state_that_cannot_be_read_raises() -> None:
    config = MagicMock(get_config=AsyncMock(side_effect=RuntimeError("kv down")))
    accounts = SourceAccounts(MagicMock(), CONNECTOR, config, logging.getLogger("t"))
    with pytest.raises(RuntimeError):
        await accounts.ensure_loaded()
    assert not accounts.may_grant("bo@x.com", "b")


# ---- the processor: every connector whose users are source accounts goes through it ----------------------------


def _processor(config: _Config) -> tuple[DataSourceEntitiesProcessor, MagicMock]:
    tx = MagicMock()
    tx.batch_upsert_app_users = AsyncMock()
    provider = MagicMock()
    provider.transaction.return_value.__aenter__ = AsyncMock(return_value=tx)
    provider.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    processor = DataSourceEntitiesProcessor(logging.getLogger("t"), provider, config)
    processor.org_id = "o1"
    return processor, tx


@pytest.mark.asyncio
async def test_linking_a_source_account_checks_who_held_its_address_first() -> None:
    config = _Config()
    processor, tx = _processor(config)
    processor.get_app_user_links = AsyncMock(return_value=[SimpleNamespace(id="u-ana", source_user_id="old")])
    processor.get_user_by_email = AsyncMock(return_value=SimpleNamespace(id="u-ana"))
    processor.remove_user_access_to_connector = AsyncMock(return_value=2)

    await processor.on_new_app_users([_user("ana@x.com", "new")])

    processor.remove_user_access_to_connector.assert_awaited_once_with(CONNECTOR, "ana@x.com")
    tx.batch_upsert_app_users.assert_awaited_once()
    assert processor.source_accounts(CONNECTOR).reused == {"ana@x.com": "new"}


@pytest.mark.asyncio
async def test_a_connector_that_links_pipeshub_users_is_not_checked() -> None:
    config = _Config()
    processor, tx = _processor(config)
    processor.get_app_user_links = AsyncMock(side_effect=AssertionError("not read"))

    await processor.on_new_app_users([_user("ana@x.com", "u-ana", Connectors.S3)])

    tx.batch_upsert_app_users.assert_awaited_once()


@pytest.mark.asyncio
async def test_linking_waits_when_the_stored_links_cannot_be_read() -> None:
    processor, tx = _processor(_Config())
    processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))

    await processor.on_new_app_users([_user("ana@x.com", "a")])

    tx.batch_upsert_app_users.assert_not_awaited()


def _grant(email: str, sid: str | None) -> Permission:
    return Permission(email=email, external_id=sid, type=PermissionType.READ, entity_type=EntityType.USER)


async def _granted(processor: DataSourceEntitiesProcessor, *permissions: Permission) -> list[str]:
    tx = MagicMock()
    tx.get_user_by_email = AsyncMock(side_effect=lambda e, **_: SimpleNamespace(id=f"u-{e.split('@')[0]}"))
    tx.get_person_by_email = AsyncMock(return_value=None)
    tx.upsert_person_by_email = AsyncMock(side_effect=AssertionError("no Person stands in"))
    record = SimpleNamespace(id="r1", connector_id=CONNECTOR, connector_name=Connectors.JIRA)
    edges = await processor._record_permission_edges(record, list(permissions), tx)
    return [e["from_id"] for e in edges]


@pytest.mark.asyncio
async def test_a_grant_to_a_reused_address_is_written_only_for_the_account_that_holds_it() -> None:
    config = _Config()
    config.saved[source_accounts_key(CONNECTOR)] = {"withdrawn": {}, "reused": {"ana@x.com": "new"}}
    processor, _ = _processor(config)

    assert await _granted(processor, _grant("ana@x.com", "new")) == ["u-ana"]      # the current account, by id
    assert await _granted(processor, _grant("ana@x.com", "old")) == []             # the earlier account
    assert await _granted(processor, _grant("Ana@x.com", None)) == []              # only the address: fails closed
    assert await _granted(processor, _grant("bo@x.com", None)) == ["u-bo"]         # not re-used: as before


@pytest.mark.asyncio
async def test_a_state_saved_before_holders_were_kept_fails_closed_for_its_addresses() -> None:
    config = _Config()
    config.saved[source_accounts_key(CONNECTOR)] = {"withdrawn": {}, "reused": ["ana@x.com"]}
    processor, _ = _processor(config)
    assert await _granted(processor, _grant("ana@x.com", "new")) == []
    assert await _granted(processor, _grant("bo@x.com", None)) == ["u-bo"]


@pytest.mark.asyncio
async def test_grants_are_not_written_while_the_state_cannot_be_read() -> None:
    config = MagicMock(get_config=AsyncMock(side_effect=RuntimeError("kv down")))
    processor, _ = _processor(config)
    with pytest.raises(RuntimeError):
        await _granted(processor, _grant("bo@x.com", None))
