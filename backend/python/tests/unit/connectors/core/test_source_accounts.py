"""A new source account that takes a deleted account's address (a new source id, the same address) reads nothing
PipesHub granted the old one, on every connector whose users are accounts of the source. Microsoft, for one: "The new
account is assigned a new ID value even though the UPN is the same"
(https://learn.microsoft.com/en-us/troubleshoot/sharepoint/sharing-and-permissions/fix-site-user-id-mismatch)."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_processor.source_accounts import (
    BUCKETS,
    SourceAccounts,
    _bucket,
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
        self.processor.get_app_user_links = AsyncMock(side_effect=self._links)
        self.processor.get_user_by_email = AsyncMock(side_effect=self._by_email)
        self.processor.remove_user_access_to_connector = AsyncMock(side_effect=self._remove)
        self.deleted: dict[str, str] = {}    # the source's deleted accounts: address -> source id

    async def _links(self, _connector_id: str) -> list[SimpleNamespace]:
        email_of = {node: e for e, node in self.nodes.items()}
        return [SimpleNamespace(id=i, source_user_id=s, email=email_of.get(i)) for i, s in self.links.items()]

    async def deleted_users(self) -> dict[str, str]:
        return dict(self.deleted)

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
    accounts = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"),
                              deleted_users=directory.deleted_users)
    return directory, accounts, config


async def _admit(directory: _Directory, accounts: SourceAccounts, users: list[AppUser]) -> None:
    """What ``on_new_app_users`` does: the check, then the link."""
    if await accounts.before_link(users):
        directory.link(users)


def _withdraw(directory: _Directory, sid: str) -> None:
    """``withdraw_app_user_access``: the link goes, the grants stay (a restored account gets them back)."""
    directory.links = {node: s for node, s in directory.links.items() if s != sid}


def _saved(config: _Config) -> dict:
    """Every bucket of the saved state, merged."""
    merged: dict[str, dict] = {"linked": {}, "reused": {}}
    for bucket in range(BUCKETS):
        for part, values in (config.saved.get(source_accounts_key(CONNECTOR, bucket)) or {}).items():
            merged[part].update(values)
    return merged


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
    _withdraw(directory, "old-oid")          # deleted: a later run
    await _admit(directory, accounts, [_user("bo@x.com", "bo-oid")])
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"}


@pytest.mark.asyncio
async def test_a_restored_account_keeps_its_grants(world) -> None:
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])
    _withdraw(directory, "ana-oid")

    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])

    assert ("remove", "ana@x.com") not in directory.calls
    assert accounts.may_grant("ana@x.com", "ana-oid") and accounts.may_grant("ana@x.com", None)


@pytest.mark.asyncio
async def test_a_grant_reaches_only_the_account_that_holds_the_address_now(world) -> None:
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    await accounts.ensure_loaded()

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
    assert _saved(config) == {"reused": {}, "linked": {"ana@x.com": "a", "bo@x.com": "b"}}


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
    _withdraw(directory, "old-oid")
    directory.links["node-ana@x.com"] = "new-oid"            # linked again, as the new account, without the check
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"}


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
    processor.get_app_user_links = AsyncMock(return_value=[SimpleNamespace(id="u-ana", source_user_id="old", email="ana@x.com")])
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
    config.saved[source_accounts_key(CONNECTOR, _bucket("ana@x.com"))] = {"linked": {}, "reused": {"ana@x.com": "new"}}
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


@pytest.mark.asyncio
async def test_a_large_directory_never_writes_a_large_value_and_one_change_rewrites_one_bucket(world) -> None:
    directory, accounts, config = world
    people = [_user(f"person{i}@x.com", f"acc-{i}") for i in range(50_000)]
    await _admit(directory, accounts, people)
    biggest = max(len(json.dumps(v)) for k, v in config.saved.items() if k.startswith(source_accounts_key(CONNECTOR)))
    assert biggest < 32_000, biggest

    writes: list[str] = []
    real_set = config.set_config

    async def counting(key: str, value: object, **kw: object) -> bool:
        writes.append(key)
        return await real_set(key, value, **kw)

    config.set_config = counting
    people[123] = _user("person123@x.com", "acc-123-new")
    await _admit(directory, accounts, people)
    assert writes == [source_accounts_key(CONNECTOR, _bucket("person123@x.com")), source_accounts_key(CONNECTOR)]
    assert len(json.dumps(config.saved[source_accounts_key(CONNECTOR)])) < 100     # the marker: a version bump
    assert accounts.reused == {"person123@x.com": "acc-123-new"}


@pytest.mark.asyncio
async def test_an_account_withdrawn_before_it_was_ever_listed_again_is_compared_from_the_stored_links(world) -> None:
    """Linked by an earlier version (no state yet), withdrawn before any listing names it: the first run notes the
    account every stored link names, so a new account on the address is still caught."""
    directory, accounts, config = world
    directory.link([_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])     # linked before this state
    await _admit(directory, accounts, [_user("bo@x.com", "bo-oid")])                  # ana is not listed any more
    _withdraw(directory, "old-oid")
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")


@pytest.mark.asyncio
async def test_a_run_whose_links_cannot_be_read_writes_no_grant_until_one_can(world) -> None:
    """An earlier run's listing must not vouch for a source id while this run could not check the links."""
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "a")])
    await accounts.ensure_loaded()
    assert accounts.may_grant("ana@x.com", "a")
    working = directory.processor.get_app_user_links
    directory.processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    await _admit(directory, accounts, [_user("ana@x.com", "b")])
    assert not accounts.may_grant("ana@x.com", "a") and not accounts.may_grant("ana@x.com", "b")
    with pytest.raises(RuntimeError):
        await accounts.ensure_loaded(["ana@x.com"])     # the grant writers stop: nothing is rewritten this run
    directory.processor.get_app_user_links = working
    await _admit(directory, accounts, [_user("ana@x.com", "a")])
    await accounts.ensure_loaded(["ana@x.com"])
    assert accounts.may_grant("ana@x.com", "a")


@pytest.mark.asyncio
async def test_an_account_renamed_onto_a_deleted_accounts_address_is_compared_by_address(world) -> None:
    """B moves from bo@ to ana@ after A (ana@) was deleted. B's id is linked already, to bo@'s user, which says nothing
    about ana@: ana@ was last linked to A."""
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "a"), _user("bo@x.com", "b")])
    _withdraw(directory, "a")
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "b")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["reused"] == {"ana@x.com": "b"}
    assert not accounts.may_grant("ana@x.com", "a") and not accounts.may_grant("ana@x.com", None)


@pytest.mark.asyncio
async def test_an_address_an_earlier_version_already_linked_to_its_new_account_is_found_in_deleted_users(world) -> None:
    """The stored link already names the new id (written before this state was kept) and nothing is saved; the
    source still lists the deleted account with the address under the old id."""
    directory, accounts, config = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    directory.deleted = {"ana@x.com": "old-oid"}

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"}
    assert config.saved[source_accounts_key(CONNECTOR)] == {"seeded": True, "deletedChecked": True, "version": 1}
    assert not accounts.may_grant("ana@x.com", "old-oid") and not accounts.may_grant("ana@x.com", None)
    assert accounts.may_grant("ana@x.com", "new-oid")
    directory.calls.clear()
    directory.deleted = {"ana@x.com": "old-oid"}
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])      # read once: no second removal
    assert ("remove", "ana@x.com") not in directory.calls


@pytest.mark.asyncio
async def test_deleted_users_that_cannot_be_read_are_read_again_next_run(world) -> None:
    directory, accounts, config = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    accounts.deleted_users = AsyncMock(side_effect=RuntimeError("403"))
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    assert config.saved[source_accounts_key(CONNECTOR)]["deletedChecked"] is False
    accounts.deleted_users = AsyncMock(return_value={"ana@x.com": "old-oid"})
    directory.calls.clear()
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    assert directory.calls[0] == ("remove", "ana@x.com")


@pytest.mark.asyncio
async def test_a_restored_account_whose_link_write_failed_keeps_its_grants_next_run(world) -> None:
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "a")])
    _withdraw(directory, "a")
    assert await accounts.before_link([_user("ana@x.com", "a")])        # compared, then the link write fails
    directory.calls.clear()
    await _admit(directory, accounts, [_user("ana@x.com", "a")])
    assert ("remove", "ana@x.com") not in directory.calls


@pytest.mark.asyncio
async def test_a_saved_state_that_is_not_a_mapping_is_refused(world) -> None:
    directory, accounts, config = world
    config.saved[source_accounts_key(CONNECTOR)] = ["ana@x.com"]
    assert await accounts.before_link([_user("ana@x.com", "a")]) is False
    with pytest.raises(RuntimeError):
        await accounts.ensure_loaded()


@pytest.mark.asyncio
async def test_while_deleted_users_cannot_be_read_no_grant_naming_no_account_is_written(world) -> None:
    """An address whose link names the listed id may still be one an earlier version moved to a new account; until the
    deleted accounts are read, a grant that names no account (a SharePoint principal) waits, in every instance."""
    directory, accounts, config = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    accounts.deleted_users = AsyncMock(side_effect=RuntimeError("503"))
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    await accounts.ensure_loaded()
    assert not accounts.may_grant("ana@x.com", None) and accounts.may_grant("ana@x.com", "new-oid")

    worker = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))     # a reindex worker
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", None)

    accounts.deleted_users = AsyncMock(return_value={})
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    assert accounts.may_grant("ana@x.com", None)


@pytest.mark.asyncio
async def test_deleted_evidence_waits_for_the_listing_that_shows_its_address(world) -> None:
    """The deleted accounts are read once; an address absent from that run's listing keeps its evidence until a later
    listing shows it."""
    directory, accounts, config = world
    directory.nodes["ana@x.com"] = "node-ana@x.com"
    directory.links["node-ana@x.com"] = "new-oid"
    directory.deleted = {"ana@x.com": "old-oid"}
    await _admit(directory, accounts, [_user("bo@x.com", "bo-oid")])                 # ana@ is not in this listing
    assert config.saved[source_accounts_key(CONNECTOR, _bucket("ana@x.com"))]["deleted"] == {"ana@x.com": "old-oid"}
    directory.deleted = {}
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert "deleted" not in config.saved[source_accounts_key(CONNECTOR, _bucket("ana@x.com"))]
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"}


@pytest.mark.asyncio
async def test_a_withdrawn_account_is_remembered_by_address_across_a_user_merge(world) -> None:
    """A verified sign-in merges the synced user into the login user (another id): the address still remembers the
    withdrawn account."""
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    _withdraw(directory, "old-oid")
    directory.nodes["ana@x.com"] = "login-user"                        # the merge: the synced user is gone
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")


@pytest.mark.asyncio
async def test_a_state_another_instance_changed_is_read_again_before_a_later_grant(world, monkeypatch) -> None:
    """A reindex worker loaded the state; a sync elsewhere admits a new account for ana@; the worker's next decision,
    once its copy is older than STATE_MAX_AGE_SECONDS, reads the change (and only a change)."""
    from app.connectors.core.base.data_processor import source_accounts as module

    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    worker = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    await worker.ensure_loaded()
    assert worker.may_grant("ana@x.com", "old-oid")
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])        # another instance re-links ana@
    await worker.ensure_loaded()
    assert worker.may_grant("ana@x.com", "old-oid")                           # still within the trusted window
    now[0] += module.STATE_MAX_AGE_SECONDS + 1
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", "old-oid") and worker.may_grant("ana@x.com", "new-oid")


@pytest.mark.asyncio
async def test_recheck_reads_the_state_again_and_drops_refused_user_grants(world) -> None:
    """A batch judged before an admission is checked again just before it is written."""
    directory, accounts, config = world
    worker = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))
    await worker.ensure_loaded()
    grants = [Permission(external_id="old-oid", email="ana@x.com", type=PermissionType.READ, entity_type=EntityType.USER),
              Permission(external_id="g-1", email=None, type=PermissionType.READ, entity_type=EntityType.GROUP),
              Permission(external_id="bo-oid", email="bo@x.com", type=PermissionType.READ, entity_type=EntityType.USER)]
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])         # admitted while the batch was read
    assert [p.external_id for p in await worker.recheck(grants)] == ["g-1", "bo-oid"]
