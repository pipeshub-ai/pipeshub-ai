"""A new source account that takes a deleted account's address (a new source id, the same address) reads nothing
PipesHub granted the old one, on every connector whose users are accounts of the source. Microsoft, for one: "The new
account is assigned a new ID value even though the UPN is the same"
(https://learn.microsoft.com/en-us/troubleshoot/sharepoint/sharing-and-permissions/fix-site-user-id-mismatch)."""

import asyncio
import contextlib
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
    grant_fence,
    source_accounts_key,
)
from app.models.entities import AppUser
from app.models.permission import EntityType, Permission, PermissionType
from tests.support.settle import settle

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

    async def compare_and_set_config(self, key: str, expected: object, new: object) -> bool:
        if self.saved.get(key) != expected:
            return False
        return await self.set_config(key, new)


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
async def test_accounts_reported_unusable_are_withdrawn_and_a_failure_does_not_raise() -> None:
    """Only the stored links whose account the source reported unusable go; a failed read is logged, not raised, so
    the rewrites after it still run."""
    processor, _ = _processor(_Config())
    links = [SimpleNamespace(id=f"n-{s}", source_user_id=s, email=f"{s}@x.com") for s in ("a", "b", "c")]
    processor.get_app_user_links = AsyncMock(return_value=links)
    processor.withdraw_app_user_access = AsyncMock()

    await processor.withdraw_reported_unusable(CONNECTOR, {"b", "z"})

    processor.withdraw_app_user_access.assert_awaited_once_with(CONNECTOR, [links[1]])
    processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    await processor.withdraw_reported_unusable(CONNECTOR, {"b"})
    processor.withdraw_app_user_access.reset_mock()
    await processor.withdraw_reported_unusable(CONNECTOR, set())
    processor.withdraw_app_user_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_admission_for_an_address_waits_for_the_first_ones_link_write() -> None:
    """A is admitted and its link write is slow; B, listed for the same address meanwhile, must be admitted after that
    write, or A's late write leaves the stored link naming A while the saved holder is B."""
    processor, tx = _processor(_Config())
    links: dict[str, str] = {}
    processor.get_app_user_links = AsyncMock(side_effect=lambda _c: [
        SimpleNamespace(id="u-ana", source_user_id=sid, email="ana@x.com") for sid in links.values()])
    processor.remove_user_access_to_connector = AsyncMock(return_value=0)
    a_writing, release, order = asyncio.Event(), asyncio.Event(), []

    async def upsert(users: list, org_id: str) -> None:
        sid = users[0].source_user_id
        if sid == "a":
            a_writing.set()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(release.wait(), 0.2)
        links["ana@x.com"] = sid
        order.append(sid)

    tx.batch_upsert_app_users = AsyncMock(side_effect=upsert)

    async def b() -> None:
        await a_writing.wait()
        task = asyncio.ensure_future(processor.on_new_app_users([_user("ana@x.com", "b")]))
        await asyncio.sleep(0.05)
        release.set()
        await task

    await asyncio.gather(processor.on_new_app_users([_user("ana@x.com", "a")]), b())

    assert order == ["a", "b"]
    assert links["ana@x.com"] == "b"


@pytest.mark.asyncio
async def test_linking_waits_when_the_stored_links_cannot_be_read() -> None:
    """Nobody is linked, and the call fails: a caller that took it as done would move its checkpoint past users
    it never linked, and an unchanged user is not listed again."""
    processor, tx = _processor(_Config())
    processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="could not be read"):
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
    marker = source_accounts_key(CONNECTOR)
    assert writes == [marker, source_accounts_key(CONNECTOR, _bucket("person123@x.com")), marker]
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
    marker = config.saved[source_accounts_key(CONNECTOR)]
    assert marker["seeded"] is True and marker["deletedChecked"] is True and marker["version"]
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


@pytest.mark.asyncio
async def test_concurrent_user_syncs_of_one_connector_keep_every_address_they_link(world) -> None:
    """Two listings of one connector at once (users read in parallel): the second runs while the first is still
    saving a bucket both addresses fall in. Neither save may drop the other's entry."""
    directory, accounts, config = world
    first = "a0@x.com"
    second = next(f"a{i}@x.com" for i in range(1, 10_000) if _bucket(f"a{i}@x.com") == _bucket(first))
    bucket_key = source_accounts_key(CONNECTOR, _bucket(first))
    real_set = config.set_config
    saving, second_done = asyncio.Event(), asyncio.Event()

    async def slow_set(key: str, value: object, **kw: object) -> bool:
        if key == bucket_key and not saving.is_set():
            saving.set()       # the first save is under way; let the other listing run now (bounded, not a deadlock)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(second_done.wait(), 0.2)
        return await real_set(key, value, **kw)

    async def second_listing() -> None:
        await saving.wait()
        await accounts.before_link([_user(second, "acc-2")])
        second_done.set()

    config.set_config = slow_set
    await asyncio.gather(accounts.before_link([_user(first, "acc-1")]), second_listing())

    assert _saved(config)["linked"] == {first: "acc-1", second: "acc-2"}


@pytest.mark.asyncio
async def test_the_link_write_runs_after_the_check_under_the_same_lock_and_not_after_a_failed_read(world) -> None:
    directory, accounts, _ = world
    held = []

    async def link() -> None:
        held.append(accounts.lock.locked())

    assert await accounts.before_link([_user("ana@x.com", "a")], write=link)
    assert held == [True], "the SourceAccount write is serialized with the connector's other listings"
    directory.processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    assert await accounts.before_link([_user("ana@x.com", "a")], write=link) is False
    assert held == [True], "nobody is linked when the stored links could not be read"




@pytest.mark.asyncio
async def test_two_processes_saving_one_bucket_keep_both_addresses(world) -> None:
    """Two processes (no shared lock) link addresses in one bucket at once: the later save finds the bucket changed,
    reads it again and keeps the other process's entry."""
    directory, accounts, config = world
    other = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))     # another replica
    first = "a0@x.com"
    second = next(f"a{i}@x.com" for i in range(1, 10_000) if _bucket(f"a{i}@x.com") == _bucket(first))
    bucket_key = source_accounts_key(CONNECTOR, _bucket(first))
    real_cas = config.compare_and_set_config
    saving, second_done = asyncio.Event(), asyncio.Event()

    async def slow_cas(key: str, expected: object, new: object) -> bool:
        if key == bucket_key and not saving.is_set():
            saving.set()       # the first replica read the bucket and is about to save it
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(second_done.wait(), 0.2)
        return await real_cas(key, expected, new)

    async def second_replica() -> None:
        await saving.wait()
        await other.before_link([_user(second, "acc-2")])
        second_done.set()

    config.compare_and_set_config = slow_cas
    await asyncio.gather(accounts.before_link([_user(first, "acc-1")]), second_replica())

    assert _saved(config)["linked"] == {first: "acc-1", second: "acc-2"}


async def _withdraw_noted(directory: _Directory, accounts: SourceAccounts, sid: str) -> None:
    """``withdraw_app_user_access``: the account each withdrawn link names is noted first, then the link goes."""
    gone = [link for link in await directory.processor.get_app_user_links(CONNECTOR) if link.source_user_id == sid]
    await accounts.note_withdrawn(gone)
    _withdraw(directory, sid)


@pytest.mark.asyncio
async def test_a_withdrawal_before_any_listing_still_notes_the_account(world) -> None:
    """A run that withdraws before it lists anyone (an incremental user_delete, a listing with no active member):
    the account is noted at withdrawal, so a new account on the address later is still a change of hands."""
    directory, accounts, config = world
    directory.link([_user("ana@x.com", "old-oid")])         # linked by an earlier version, nothing noted yet
    await _withdraw_noted(directory, accounts, "old-oid")
    directory.calls.clear()

    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])

    assert directory.calls[0] == ("remove", "ana@x.com")
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"}


@pytest.mark.asyncio
async def test_a_withdrawal_before_the_first_listing_keeps_the_reused_addresses_an_earlier_version_saved(world) -> None:
    directory, accounts, config = world
    config.saved[source_accounts_key(CONNECTOR)] = {"reused": {"ana@x.com": "new-oid"}}    # one value, not seeded
    directory.link([_user("bo@x.com", "bo-oid")])
    await _withdraw_noted(directory, accounts, "bo-oid")

    worker = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))     # another process
    await worker.ensure_loaded()
    assert not worker.admits("ana@x.com", None) and worker.admits("ana@x.com", "new-oid")

    await _admit(directory, accounts, [_user("cy@x.com", "cy-oid")])                  # the first listing migrates it
    assert _saved(config)["reused"] == {"ana@x.com": "new-oid"}
    assert _saved(config)["linked"]["bo@x.com"] == "bo-oid"


@pytest.mark.asyncio
async def test_an_address_with_unreconciled_deleted_evidence_takes_only_its_linked_account(world) -> None:
    """Deleted accounts were read, but no listing has shown ana@ yet: a grant naming no account (a SharePoint
    principal) or the deleted account waits; one naming the account ana@ is linked to goes through."""
    directory, accounts, config = world
    directory.link([_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])
    directory.deleted = {"ana@x.com": "old-oid"}
    await _admit(directory, accounts, [_user("bo@x.com", "bo-oid")])                 # ana@ not in this listing

    worker = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))     # another process
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", None) and not worker.may_grant("ana@x.com", "old-oid")
    assert worker.may_grant("ana@x.com", "new-oid") and worker.may_grant("bo@x.com", None)


@pytest.mark.asyncio
async def test_an_address_still_linked_to_its_deleted_account_takes_no_grant_until_listed_again(world) -> None:
    """The link and the deleted evidence name the same account (another listing read the deleted accounts before this
    address was listed again): its grants are refused, by the gate and by recheck, until a listing shows it, which
    clears the evidence for a restored account."""
    directory, accounts, config = world
    directory.link([_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    directory.deleted = {"ana@x.com": "old-oid"}
    await _admit(directory, accounts, [_user("bo@x.com", "bo-oid")])                 # ana@ not in this listing
    await accounts.ensure_loaded()
    assert not accounts.admits("ana@x.com", "old-oid")
    assert await accounts.recheck([_grant("ana@x.com", "old-oid")]) == []

    await _admit(directory, accounts, [_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])   # restored
    assert accounts.admits("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_the_processor_gate_applies_the_deleted_evidence_too(world) -> None:
    """ana@ is linked to new-oid and the deleted accounts still list old-oid with it, with no re-used entry: a grant
    naming old-oid must not reach the PipesHub user linked to new-oid through the gate every connector's grants use."""
    directory, accounts, config = world
    directory.link([_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])
    directory.deleted = {"ana@x.com": "old-oid"}
    await _admit(directory, accounts, [_user("bo@x.com", "bo-oid")])                 # ana@ not in this listing
    processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
    processor.logger = logging.getLogger("t")
    processor.source_accounts = lambda _connector_id: accounts

    assert not await processor._holds_address(CONNECTOR, "ana@x.com", "old-oid", "node-ana")
    assert not await processor._holds_address(CONNECTOR, "ana@x.com", None, "node-ana")
    assert await processor._holds_address(CONNECTOR, "ana@x.com", "new-oid", "node-ana")


@pytest.mark.asyncio
async def test_recheck_does_not_refuse_a_site_principal_id_at_an_address_nobody_else_held(world) -> None:
    """A SharePoint principal's grant carries its site user id, not the Entra object id: recheck refuses it only where
    the address passed to another account, not because the ids differ in kind."""
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid"), _user("bo@x.com", "old-oid")])
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid"), _user("bo@x.com", "new-oid")])
    site = [Permission(external_id="17", email="ana@x.com", type=PermissionType.READ, entity_type=EntityType.USER),
            Permission(external_id="18", email="bo@x.com", type=PermissionType.READ, entity_type=EntityType.USER)]
    assert [p.external_id for p in await accounts.recheck(site)] == ["17"]


@pytest.mark.asyncio
async def test_a_replacement_whose_link_failed_is_reconciled_when_the_first_account_is_listed_again(world) -> None:
    """B was compared in (ana@ saved as re-used, held by B) but its link write failed; A is listed again: the saved
    holder follows the account listed now."""
    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "a-oid")])
    assert await accounts.before_link([_user("ana@x.com", "b-oid")])      # the link write then fails
    await _admit(directory, accounts, [_user("ana@x.com", "a-oid")])
    await accounts.ensure_loaded()
    assert accounts.may_grant("ana@x.com", "a-oid") and not accounts.may_grant("ana@x.com", "b-oid")


@pytest.mark.asyncio
async def test_a_replica_with_nothing_to_change_never_writes_an_older_marker_version_back(world) -> None:
    """Replica B reads the marker; replica A then saves a change of hands and a new version; B reads the links and the
    bucket after A, so it has nothing to change, but it notes its first deleted-account read. It must keep A's version
    (other processes reload on it) and still record the flag."""
    directory, _, config = world
    accounts = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))   # replica A: no deleted read
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    replica_a_done = asyncio.Event()

    async def links_after_a(connector_id: str) -> list[SimpleNamespace]:
        await replica_a_done.wait()
        return await directory._links(connector_id)

    processor_b = MagicMock(get_app_user_links=AsyncMock(side_effect=links_after_a),
                            remove_user_access_to_connector=directory.processor.remove_user_access_to_connector)
    replica_b = SourceAccounts(processor_b, CONNECTOR, config, logging.getLogger("t"),
                               deleted_users=AsyncMock(return_value={}))

    async def replica_a() -> None:
        await asyncio.sleep(0)       # replica B has read the marker by now
        await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
        replica_a_done.set()

    await asyncio.gather(replica_b.before_link([_user("ana@x.com", "new-oid")]), replica_a())

    marker = config.saved[source_accounts_key(CONNECTOR)]
    assert marker["version"] == accounts._version, "replica A's version stands"
    assert marker["deletedChecked"] is True


def _other_bucket(email: str) -> str:
    return next(f"o{i}@x.com" for i in range(10_000) if _bucket(f"o{i}@x.com") != _bucket(email))


@pytest.mark.asyncio
async def test_a_newer_version_seen_while_rechecking_other_addresses_reloads_every_loaded_bucket(world) -> None:
    """Process P loaded ana's bucket; process Q gives ana to a new account; P's recheck of another address takes Q's
    version. P must still read ana's bucket again before judging a grant to her."""
    directory, p, config = world
    other = _other_bucket("ana@x.com")
    await _admit(directory, p, [_user("ana@x.com", "old-oid"), _user(other, "o-oid")])
    await p.ensure_loaded(["ana@x.com", other])
    assert p.may_grant("ana@x.com", "old-oid")
    q = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))
    await _admit(directory, q, [_user("ana@x.com", "new-oid")])

    await p.recheck([_grant(other, "o-oid")])
    await p.ensure_loaded(["ana@x.com"])

    assert not p.may_grant("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_a_failed_reload_after_a_newer_version_is_tried_again(world) -> None:
    """The newer version is taken, then the bucket read fails: the next check must read the bucket again rather than
    trust the copy loaded under the older version."""
    directory, p, config = world
    await _admit(directory, p, [_user("ana@x.com", "old-oid")])
    await p.ensure_loaded(["ana@x.com"])
    q = SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t"))
    await _admit(directory, q, [_user("ana@x.com", "new-oid")])
    real_get = config.get_config
    bucket_key = source_accounts_key(CONNECTOR, _bucket("ana@x.com"))

    async def bucket_down(key: str, *a: object, **kw: object) -> object:
        if key == bucket_key:
            raise ConnectionError("down")
        return await real_get(key, *a, **kw)

    config.get_config = bucket_down
    p.loaded_at = 0.0
    with pytest.raises(ConnectionError):
        await p.ensure_loaded(["ana@x.com"])
    config.get_config = real_get
    await p.ensure_loaded(["ana@x.com"])

    assert not p.may_grant("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_a_change_of_hands_waits_for_a_grant_write_under_way_and_removes_after_it(world) -> None:
    """A write judged ana's grant for the old account and has not committed yet; the new account is admitted meanwhile.
    The removal must come after that write, or the old account's grant is committed onto the new holder's user."""
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    directory.calls.clear()
    judged, release = asyncio.Event(), asyncio.Event()

    async def write() -> None:
        async with grant_fence().shared():
            await accounts.ensure_loaded(["ana@x.com"])
            assert accounts.holds("ana@x.com", "old-oid")
            judged.set()
            await settle(release.wait())
            directory.calls.append(("write", "ana@x.com"))

    async def admit() -> None:
        await judged.wait()
        admission = asyncio.ensure_future(_admit(directory, accounts, [_user("ana@x.com", "new-oid")]))
        await asyncio.sleep(0.05)       # the admission has had every chance to remove first
        release.set()
        await admission

    await asyncio.gather(write(), admit())

    assert [c[0] for c in directory.calls] == ["write", "remove", "link"]


@pytest.mark.asyncio
async def test_a_listing_with_no_change_of_hands_does_not_wait_for_grant_writes(world) -> None:
    directory, accounts, _ = world
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])
    async with grant_fence().shared():
        await settle(_admit(directory, accounts, [_user("ana@x.com", "ana-oid")]))


@pytest.mark.asyncio
async def test_a_grant_write_waits_for_a_change_of_hands_under_way() -> None:
    fence, order = grant_fence(), []
    admitting = asyncio.Event()

    async def admission() -> None:
        async with fence.exclusive():
            admitting.set()
            await asyncio.sleep(0.05)
            order.append("admitted")

    async def write() -> None:
        await admitting.wait()
        async with fence.shared():
            order.append("write")

    await asyncio.gather(admission(), write())
    assert order == ["admitted", "write"]


@pytest.mark.asyncio
async def test_a_change_of_hands_waits_for_a_record_grant_added_on_its_own() -> None:
    """``add_permission_to_record`` (a single record's grants, outside a sync batch) judges and writes in one
    transaction: an admission must not remove the old account's grants between the two."""
    processor, _ = _processor(_Config())
    judged, order = asyncio.Event(), []

    async def handle(*_: object) -> None:
        judged.set()
        await asyncio.sleep(0.05)
        order.append("write")

    processor._handle_record_permissions = handle

    async def admission() -> None:
        await judged.wait()
        async with grant_fence().exclusive():
            order.append("admitted")

    await asyncio.gather(processor.add_permission_to_record(MagicMock(), [_grant("ana@x.com", None)]), admission())
    assert order == ["write", "admitted"]




def _stale_worker(directory: _Directory, config: _Config, monkeypatch) -> tuple[SourceAccounts, list[float]]:
    from app.connectors.core.base.data_processor import source_accounts as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    return SourceAccounts(directory.processor, CONNECTOR, config, logging.getLogger("t")), now


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_changed_state_unread(world, monkeypatch) -> None:
    """The marker moved and the bucket read that follows it fails: the old copy must not pass a later check."""
    from app.connectors.core.base.data_processor import source_accounts as module

    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    worker, now = _stale_worker(directory, config, monkeypatch)
    await worker.ensure_loaded()
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    now[0] += module.STATE_MAX_AGE_SECONDS + 1
    real_get = config.get_config

    async def bucket_down(key: str, *a: object, **kw: object) -> object:
        if key != source_accounts_key(CONNECTOR):
            raise RuntimeError("kv down")
        return await real_get(key, *a, **kw)

    config.get_config = bucket_down
    with pytest.raises(RuntimeError):
        await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", "old-oid")
    config.get_config = real_get
    now[0] += module.STATE_MAX_AGE_SECONDS + 1
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", "old-oid") and worker.may_grant("ana@x.com", "new-oid")


@pytest.mark.asyncio
async def test_reading_one_bucket_under_a_newer_marker_does_not_vouch_for_the_others(world, monkeypatch) -> None:
    from app.connectors.core.base.data_processor import source_accounts as module

    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    worker, now = _stale_worker(directory, config, monkeypatch)
    await worker.ensure_loaded()
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid"), _user("bo@x.com", "bo-oid")])
    bo = Permission(external_id="bo-oid", email="bo@x.com", type=PermissionType.READ, entity_type=EntityType.USER)
    assert await worker.recheck([bo]) == [bo]             # reads bo@'s bucket only
    now[0] += module.STATE_MAX_AGE_SECONDS + 1
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_linking_one_address_under_a_newer_marker_does_not_vouch_for_the_others(world, monkeypatch) -> None:
    from app.connectors.core.base.data_processor import source_accounts as module

    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid"), _user("bo@x.com", "bo-oid")])
    worker, now = _stale_worker(directory, config, monkeypatch)
    await worker.ensure_loaded()
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    await worker.before_link([_user("bo@x.com", "bo-oid")])       # reads bo@'s bucket only
    now[0] += module.STATE_MAX_AGE_SECONDS + 1
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", "old-oid")


@pytest.mark.asyncio
async def test_a_marker_save_that_failed_after_the_bucket_save_is_published_by_the_retry(world, monkeypatch) -> None:
    """The bucket holds the new account but the marker save failed; the retry finds nothing left to change in the
    bucket and must still tell other instances to read it again."""
    from app.connectors.core.base.data_processor import source_accounts as module

    directory, accounts, config = world
    await _admit(directory, accounts, [_user("ana@x.com", "old-oid")])
    _withdraw(directory, "old-oid")
    worker, now = _stale_worker(directory, config, monkeypatch)
    await worker.ensure_loaded()
    real_set = config.set_config

    async def marker_down(key: str, value: object, **kw: object) -> bool:
        if key == source_accounts_key(CONNECTOR) and not value.get("saving"):
            return False
        return await real_set(key, value, **kw)

    config.set_config = marker_down
    with pytest.raises(RuntimeError):
        await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    config.set_config = real_set
    await _admit(directory, accounts, [_user("ana@x.com", "new-oid")])
    assert not config.saved[source_accounts_key(CONNECTOR)].get("saving")
    now[0] += module.STATE_MAX_AGE_SECONDS + 1
    await worker.ensure_loaded()
    assert not worker.may_grant("ana@x.com", "old-oid") and worker.may_grant("ana@x.com", "new-oid")


@pytest.mark.asyncio
async def test_a_connector_that_never_saved_its_state_reads_as_empty(world) -> None:
    """A store answers None for a key never saved: the first run of a new connector loads nothing and admits its users."""
    directory, accounts, config = world
    assert config.saved == {}
    await accounts.ensure_loaded()
    assert accounts.loaded and accounts.reused == {}
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])
    assert directory.calls == [("link", ["ana-oid"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["marker", "bucket"])
async def test_a_key_that_holds_an_empty_mapping_is_saved_over(world, where) -> None:
    """The compare-and-set expects exactly what was read: a key holding ``{}`` is present, not absent, so expecting None
    never matched it and every save gave up as if another process kept changing the state."""
    directory, accounts, config = world
    key = source_accounts_key(CONNECTOR) if where == "marker" else source_accounts_key(CONNECTOR, _bucket("ana@x.com"))
    config.saved[key] = {}
    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])
    assert directory.calls == [("link", ["ana-oid"])]
    assert config.saved[source_accounts_key(CONNECTOR)].get("version")
    assert _saved(config)["linked"] == {"ana@x.com": "ana-oid"}


@pytest.mark.asyncio
async def test_a_change_to_an_empty_marker_between_the_read_and_the_save_is_still_seen(world) -> None:
    directory, accounts, config = world
    key = source_accounts_key(CONNECTOR)
    config.saved[key] = {}
    real = config.compare_and_set_config
    answers: list[bool] = []

    async def other_writer_first(k: str, expected: object, new: object) -> bool:
        if k == key and not answers:
            config.saved[key] = {"seeded": True, "version": "theirs"}       # another replica saves after this run read {}
        swapped = await real(k, expected, new)
        if k == key:
            answers.append(swapped)
        return swapped
    config.compare_and_set_config = other_writer_first

    await _admit(directory, accounts, [_user("ana@x.com", "ana-oid")])
    assert answers[0] is False and answers[-1] is True
    assert config.saved[key]["version"] not in (None, "theirs")
