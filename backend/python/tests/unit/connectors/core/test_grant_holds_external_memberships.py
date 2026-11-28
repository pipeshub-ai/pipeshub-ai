"""A grant is written only after the flagged app memberships it keeps (its user's or person's own, or the members' of the group, role or team
it comes from) are locked in the same transaction (the lock the external-collaborator reaper takes), so a reaper cannot remove a membership on
the strength of a grant it could not see yet. In a transaction the statement that writes the grant takes the lock (``lock_grants``)."""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore
from app.core.org_context import OrgContext, bind
from app.models.entities import Person


def _store(held: list | None = None) -> tuple[GraphTransactionStore, MagicMock, list[str]]:
    calls: list[str] = []

    def rec(name: str, value: object = None) -> AsyncMock:
        return AsyncMock(side_effect=lambda *a, **k: calls.append(name) or value)

    provider = MagicMock(
        lock_external_app_memberships=rec("lock", held or []), batch_create_edges=rec("write", True), replace_edges_to=rec("write"),
        replace_record_permissions=rec("write"), create_edges_if_absent=rec("write"),
        get_user_by_source_id=AsyncMock(return_value=MagicMock(id="u1", email="u1@example.com")),
        get_user_group_by_external_id=AsyncMock(return_value=MagicMock(id="g1")),
    )
    store = GraphTransactionStore(provider, "txn-1", explicit=True)
    store.graph_provider = provider
    return store, provider, calls


def _grant(fc: str, fi: str, tc: str = "records", ti: str = "r1") -> dict:
    return {"from_collection": fc, "from_id": fi, "to_collection": tc, "to_id": ti, "type": "USER", "role": "READER"}


EDGES = [_grant("users", "u2"), _grant("person", "p1"), _grant("groups", "g1"), _grant("users", "u2", ti="r2"),
         {"_from": "users/u1", "_to": "groups/g1", "type": "USER"}]
HELD = [("groups", "g1", "records", "r1"), ("person", "p1", "records", "r1"), ("users", "u1", "groups", "g1"), ("users", "u2", "records", "r1"),
        ("users", "u2", "records", "r2")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("write", "method"), [
    (lambda s: s.batch_create_edges(EDGES, "permission"), "batch_create_edges"),
    (lambda s: s.replace_edges_to("r1", "records", EDGES, "permission"), "replace_edges_to"),
    (lambda s: s.replace_record_permissions("r1", EDGES, None, inherit=False), "replace_record_permissions"),
    (lambda s: s.create_edges_if_absent(EDGES, "permission"), "create_edges_if_absent"),
])
async def test_in_a_transaction_the_statement_that_writes_a_grant_locks_the_memberships_it_keeps(write, method) -> None:
    store, provider, calls = _store()
    await write(store)
    assert getattr(provider, method).await_args.kwargs["lock_grants"] == HELD
    assert calls == ["write"]      # no statement of its own: the grant's statement takes the lock


@pytest.mark.asyncio
async def test_a_group_membership_locks_its_member_s_memberships() -> None:
    store, provider, calls = _store()
    assert await store.create_user_group_membership("src-u1", "ext-g1", "app1")
    assert provider.batch_create_edges.await_args.kwargs["lock_grants"] == [("users", "u1", "groups", "g1")]
    assert calls == ["write"]


@pytest.mark.asyncio
async def test_a_record_group_grant_locks_its_users_memberships() -> None:
    from app.models.permission import EntityType, Permission, PermissionType
    store, provider, calls = _store()
    store.get_user_by_email = AsyncMock(return_value=MagicMock(id="u1"))
    await store.batch_upsert_record_group_permissions(
        "rg1", [Permission(email="u1@example.com", type=PermissionType.READ, entity_type=EntityType.USER)], "app1", "org1")
    assert provider.batch_create_edges.await_args.kwargs["lock_grants"] == [("users", "u1", "recordGroups", "rg1")]
    assert calls == ["write"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("edges", "collection"), [
    ([_grant("groups", "g1", "groups", "g2"), _grant("organizations", "o1"), _grant("roles", "x", "apps", "kb1")], "permission"),
    ([_grant("users", "u1", "apps", "app1")], "userAppRelation"),
    ([_grant("users", "u1", "apps", "kb1"), _grant("person", "p1", "agentTemplates", "t1")], "permission"),  # no grant the reaper counts
    ([], "permission"),
])
async def test_no_lock_when_the_write_holds_no_grant_the_reaper_counts(edges, collection) -> None:
    store, provider, _ = _store()
    await store.batch_create_edges(edges, collection)
    provider.lock_external_app_memberships.assert_not_awaited()
    assert all("lock_grants" not in c.kwargs for c in provider.batch_create_edges.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("edge_shape", ["generic", "arango"])
async def test_without_a_transaction_a_person_removed_between_its_upsert_and_its_grant_is_created_again(edge_shape) -> None:
    """Each statement commits alone: the reaper removed p1 after the principal lookup upserted it, so the grant's edge found no person. The store
    sees p1 gone, creates it again from its document and writes the grant to the person it got."""
    store, provider, calls = _store()
    store.explicit = False
    doc = {"_key": "p1", "email": "p1@example.com", "orgId": "org1"}
    provider.get_document = AsyncMock(side_effect=[doc, None, {**doc, "_key": "p2"}])
    store.upsert_person_by_email = AsyncMock(return_value="p2")
    grant = _grant("person", "p1") if edge_shape == "generic" else {"_from": "person/p1", "_to": "records/r1", "type": "USER"}
    with bind(OrgContext("org1", None, "job", "job:grant-test", "t")):      # the person is made again in the bound org
        await store.batch_create_edges([grant, _grant("users", "u1")], "permission")
    first, second = (c.args[0] for c in provider.batch_create_edges.await_args_list)
    assert first[0] == grant and first[1] == second[1]
    assert second[0] == ({**grant, "from_id": "p2"} if edge_shape == "generic" else {**grant, "_from": "person/p2"})
    assert (store.upsert_person_by_email.await_args.args[0].email, store.upsert_person_by_email.await_args.args[0].org_id) == ("p1@example.com", "org1")
    assert calls == ["lock", "write", "write", "lock"]


@pytest.mark.asyncio
async def test_without_a_transaction_a_person_removed_every_time_fails_the_write() -> None:
    from app.exceptions.graph_db_exceptions import GraphWriteError
    store, provider, _ = _store()
    store.explicit = False
    provider.get_document = AsyncMock(side_effect=[{"_key": "p1", "email": "p@example.com", "orgId": "o"}, None, None, None])  # read, then gone each time
    store.upsert_person_by_email = AsyncMock(side_effect=["p2", "p3", "p4"])
    with bind(OrgContext("o", None, "job", "job:grant-test", "t")), pytest.raises(GraphWriteError):
        await store.batch_create_edges([_grant("person", "p1")], "permission")


@pytest.mark.asyncio
async def test_in_a_transaction_the_person_is_not_read_again() -> None:
    store, provider, calls = _store()
    provider.get_document = AsyncMock()
    await store.batch_create_edges([_grant("person", "p1")], "permission")
    provider.get_document.assert_not_awaited()
    assert calls == ["write"]


@pytest.mark.asyncio
async def test_a_write_conflict_is_logged_by_its_type_and_code_never_its_text(monkeypatch, caplog) -> None:
    """The store's rollback line and the retry's lines name the conflict, not the driver's message (which carries keys and values)."""
    import logging

    from app.connectors.core.base.data_store import graph_data_store
    from app.connectors.core.base.data_store.graph_data_store import (
        GraphDataStore,
        retry_on_deadlock,
    )

    class Conflict(Exception):
        code = "Neo.TransientError.Transaction.DeadlockDetected"

    provider = MagicMock(begin_transaction=AsyncMock(return_value="t"), rollback_transaction=AsyncMock(), commit_transaction=AsyncMock(),
                         is_write_conflict=MagicMock(return_value=True), is_transient_error=MagicMock(return_value=True),
                         opens_explicit_transaction=MagicMock(return_value=True), lock_external_app_memberships=AsyncMock(return_value=[]))
    provider.logger = logging.getLogger("conflict-text-test")
    store = GraphDataStore(MagicMock(), provider)
    store.logger = provider.logger
    monkeypatch.setattr(graph_data_store, "_retry_delay", lambda _attempt: 0)

    class Writer:
        data_store_provider = store

        @retry_on_deadlock(max_retries=2)
        async def run(self) -> None:
            async with store.transaction() as _:
                raise Conflict("write-write conflict on key secret-key of user someone@example.com")

    with caplog.at_level(logging.WARNING), pytest.raises(Conflict):
        await Writer().run()
    lines = [r.getMessage() for r in caplog.records]
    assert lines and all("secret-key" not in m and "example.com" not in m for m in lines)
    assert any("Conflict Neo.TransientError.Transaction.DeadlockDetected" in m for m in lines)


@pytest.mark.asyncio
async def test_without_a_transaction_a_failed_read_of_the_person_raises_instead_of_making_it_again() -> None:
    """A read that fails is not a removed person: it raises, and nothing is created again or written twice."""
    from app.exceptions.graph_db_exceptions import GraphQueryError
    store, provider, calls = _store()
    store.explicit = False
    store._people_written["p1"] = Person(email="p1@example.com", org_id="org1")
    provider.get_document = AsyncMock(side_effect=GraphQueryError("store down"))
    store.upsert_person_by_email = AsyncMock()
    with pytest.raises(GraphQueryError):
        await store.batch_create_edges([_grant("person", "p1")], "permission")
    assert all(c.kwargs.get("raise_on_error") is True for c in provider.get_document.await_args_list)
    store.upsert_person_by_email.assert_not_awaited()
    assert calls == ["lock", "write"]


@pytest.mark.asyncio
async def test_without_a_transaction_a_person_removed_right_after_its_upsert_is_made_again_from_what_was_upserted() -> None:
    """The reaper removed p1 before the store could read it: the person the store upserted is what it is made again from."""
    store, provider, _ = _store()
    store.explicit = False
    provider.upsert_person_by_email = AsyncMock(side_effect=["p1", "p2"])
    provider.get_document = AsyncMock(side_effect=lambda key, *a, **k: {"_key": key} if key == "p2" else None)
    assert await store.upsert_person_by_email(Person(email="p1@example.com", org_id="org1"), raise_on_error=True) == "p1"
    await store.batch_create_edges([_grant("person", "p1")], "permission")
    again = provider.upsert_person_by_email.await_args_list[1].args[0]
    assert (again.email, again.org_id) == ("p1@example.com", "org1")
    assert provider.batch_create_edges.await_args_list[-1].args[0][0]["from_id"] == "p2"


@pytest.mark.asyncio
async def test_without_a_transaction_a_membership_removed_between_the_lock_and_the_grant_is_made_again() -> None:
    """u1's flagged membership of app1 was held before the write and is gone after it: the reaper removed it while the grant was being written."""
    store, provider, calls = _store()
    store.explicit = False
    provider.lock_external_app_memberships = AsyncMock(side_effect=[[("users", "u1", "app1"), ("users", "u1", "app2")], [("users", "u1", "app2")]])
    store.ensure_app_membership = AsyncMock()
    await store.batch_create_edges([_grant("users", "u1")], "permission")
    store.ensure_app_membership.assert_awaited_once_with("u1", "users", "app1", is_external=True)


@pytest.mark.asyncio
async def test_in_a_transaction_the_lock_is_taken_once_and_nothing_is_made_again() -> None:
    store, provider, _ = _store(held=[("users", "u1", "app1")])
    store.ensure_app_membership = AsyncMock()
    await store.batch_create_edges([_grant("users", "u1"), _grant("groups", "g1")], "permission")
    provider.lock_external_app_memberships.assert_not_awaited()      # the write's statement took it
    provider.batch_create_edges.assert_awaited_once()
    store.ensure_app_membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_grant_whose_app_lock_conflicts_every_time_fails_after_the_bounded_retries(monkeypatch) -> None:
    """ArangoDB answers a second writer of an app's lock with a write conflict: the writer runs again with jittered back-off, a bounded number
    of times, and then the conflict is raised, never swallowed."""
    import logging

    from app.connectors.core.base.data_store import graph_data_store
    from app.connectors.core.base.data_store.graph_data_store import (
        _RETRY_ATTEMPTS,
        GraphDataStore,
        retry_on_deadlock,
    )

    class Conflict(Exception):
        code = 1200
    delays: list[float] = []
    monkeypatch.setattr(graph_data_store, "_retry_delay", lambda attempt: delays.append(graph_data_store._RETRY_BASE_DELAY * 2 ** attempt) or 0)
    provider = MagicMock(begin_transaction=AsyncMock(return_value="t"), rollback_transaction=AsyncMock(), commit_transaction=AsyncMock(),
                         batch_create_edges=AsyncMock(side_effect=Conflict("write-write conflict")),
                         is_write_conflict=MagicMock(return_value=True), is_transient_error=MagicMock(return_value=True),
                         opens_explicit_transaction=MagicMock(return_value=True), lock_external_app_memberships=AsyncMock(return_value=[]))
    provider.logger = logging.getLogger("app-lock-conflict-test")
    store = GraphDataStore(provider.logger, provider)

    class Writer:
        data_store_provider = store

        @retry_on_deadlock()
        async def run(self) -> None:
            async with store.transaction() as tx:
                await tx.batch_create_edges([_grant("users", "u1")], "permission")

    with pytest.raises(Conflict):
        await Writer().run()
    assert provider.batch_create_edges.await_count == _RETRY_ATTEMPTS
    assert provider.batch_create_edges.await_args.kwargs["lock_grants"] == [("users", "u1", "records", "r1")]
    assert len(delays) == _RETRY_ATTEMPTS - 1 and delays == sorted(delays)      # back-off between attempts, none after the last
    assert provider.rollback_transaction.await_count == _RETRY_ATTEMPTS


@pytest.mark.asyncio
async def test_without_a_transaction_a_failed_read_after_the_grant_makes_every_held_membership_again_and_raises() -> None:
    """The grant is committed when the read after it fails: every membership held before it is made again (create-only), then the error is raised."""
    store, provider, _ = _store()
    store.explicit = False
    provider.lock_external_app_memberships = AsyncMock(side_effect=[[("users", "u1", "app1"), ("users", "u1", "app2")], ConnectionError("down")])
    store.ensure_app_membership = AsyncMock()
    with pytest.raises(ConnectionError):
        await store.batch_create_edges([_grant("users", "u1")], "permission")
    assert store.ensure_app_membership.await_args_list == [
        call("u1", "users", "app1", is_external=True), call("u1", "users", "app2", is_external=True)]


def _locking_provider(fail_first: bool = False) -> tuple[MagicMock, list[set[str]]]:
    """A provider whose grant write records the held locks it was given (a copy) and then locks app1, as a store's head does once it succeeds;
    the first write raises a conflict with ``fail_first``."""
    import logging
    seen: list[set[str]] = []

    class Conflict(Exception):
        code = 1200

    async def write(*_a: object, lock_held: set[str], **_k: object) -> bool:
        seen.append(set(lock_held))
        if fail_first and len(seen) == 1:
            raise Conflict("write-write conflict")
        lock_held.add("app1")
        return True
    provider = MagicMock(begin_transaction=AsyncMock(side_effect=lambda **_: f"t{len(seen)}"), rollback_transaction=AsyncMock(),
                         commit_transaction=AsyncMock(), batch_create_edges=AsyncMock(side_effect=write),
                         is_write_conflict=MagicMock(return_value=True), is_transient_error=MagicMock(return_value=True),
                         opens_explicit_transaction=MagicMock(return_value=True), lock_external_app_memberships=AsyncMock(return_value=[]))
    provider.logger = logging.getLogger("app-lock-held-test")
    return provider, seen


@pytest.mark.asyncio
async def test_a_transaction_hands_the_locks_it_holds_to_each_grant_statement_and_drops_them_when_it_ends() -> None:
    """Within one transaction the second grant statement is told app1's lock is held (its lock write is skipped); a second transaction starts
    with none, and none is left in a store after its commit."""
    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
    provider, seen = _locking_provider()
    store = GraphDataStore(provider.logger, provider)
    async with store.transaction() as tx:
        await tx.batch_create_edges([_grant("users", "u1")], "permission")
        await tx.batch_create_edges([_grant("users", "u2")], "permission")
        assert tx._app_locks == {"app1"}
    assert tx._app_locks == set()
    async with store.transaction() as other:
        await other.batch_create_edges([_grant("users", "u1")], "permission")
    assert other is not tx and other._app_locks is not tx._app_locks
    assert seen == [set(), {"app1"}, set()]      # once per app in a transaction; the next transaction takes it again


@pytest.mark.asyncio
async def test_after_a_rollback_and_retry_the_lock_is_taken_again(monkeypatch) -> None:
    """A conflict rolls the transaction back: the retry's transaction holds no lock of the first one, so its grant statement writes the lock."""
    from app.connectors.core.base.data_store import graph_data_store
    from app.connectors.core.base.data_store.graph_data_store import (
        GraphDataStore,
        retry_on_deadlock,
    )
    monkeypatch.setattr(graph_data_store, "_retry_delay", lambda _attempt: 0)
    provider, seen = _locking_provider(fail_first=True)
    store = GraphDataStore(provider.logger, provider)

    class Writer:
        data_store_provider = store

        @retry_on_deadlock()
        async def run(self) -> None:
            async with store.transaction() as tx:
                await tx.batch_create_edges([_grant("users", "u1")], "permission")
                await tx.batch_create_edges([_grant("users", "u2")], "permission")

    await Writer().run()
    assert seen == [set(), set(), {"app1"}]      # the failed attempt, then the retry: locked again, then held
    assert provider.rollback_transaction.await_count == 1


@pytest.mark.asyncio
async def test_a_rolled_back_store_holds_no_lock() -> None:
    store, provider, _ = _store()
    store._app_locks.add("app1")
    provider.rollback_transaction = AsyncMock()
    await store.rollback()
    assert store._app_locks == set()



@pytest.mark.asyncio
async def test_every_membership_is_made_again_though_one_repair_fails_and_the_first_failure_is_raised() -> None:
    store, _, _ = _store()
    made: list[str] = []

    async def make(key: str, coll: str, app: str, *, is_external: bool) -> None:
        made.append(app)
        if app == "app1":
            raise ConnectionError("down")
    store.ensure_app_membership = make
    lock_failure = RuntimeError("the read after the grant failed")
    with pytest.raises(RuntimeError) as raised:
        await store._make_again([("users", "u1", "app1"), ("users", "u1", "app2")], lock_failure)
    assert raised.value is lock_failure and made == ["app1", "app2"]
    assert "1 of 2 app memberships could not be made again" in raised.value.__notes__
    with pytest.raises(ConnectionError):
        await store._make_again([("users", "u1", "app1"), ("users", "u1", "app2")])
    assert made[2:] == ["app1", "app2"]


@pytest.mark.asyncio
async def test_without_a_transaction_a_failed_read_of_the_people_after_the_grant_makes_every_held_membership_again_and_raises() -> None:
    """The grant is committed when the read of its people after it fails: every membership held before it is made again, then the read's error."""
    store, provider, _ = _store(held=[("users", "u1", "app1")])
    store.explicit = False
    provider.get_document = AsyncMock(side_effect=[{"_key": "p1", "email": "p1@example.com"}, ConnectionError("down")])
    store.ensure_app_membership = AsyncMock()
    with pytest.raises(ConnectionError):
        await store.batch_create_edges([_grant("person", "p1")], "permission")
    store.ensure_app_membership.assert_awaited_once_with("u1", "users", "app1", is_external=True)


@pytest.mark.asyncio
async def test_a_team_write_makes_again_a_membership_removed_while_it_waited_that_the_team_keeps() -> None:
    """u1's flagged memberships of app1 and app2 are read before the team write and only app2's is there after it: app1's was removed while the
    write waited for the org's lock. app1's is kept by the team's grants (external_memberships_kept), so it is made again in the transaction."""
    store, provider, calls = _store()
    provider.lock_external_app_memberships = AsyncMock(side_effect=[[("users", "u1", "app1"), ("users", "u1", "app2")], [("users", "u1", "app2")]])
    provider.external_memberships_kept = AsyncMock(return_value=[("users", "u1", "app1")])
    store.ensure_app_membership = AsyncMock()
    await store.batch_create_edges([_grant("users", "u1", tc="teams", ti="t1")], "permission")
    assert [c.kwargs.get("lock") for c in provider.lock_external_app_memberships.await_args_list] == [False, False]
    provider.external_memberships_kept.assert_awaited_once_with([("users", "u1", "app1")], transaction="txn-1")
    store.ensure_app_membership.assert_awaited_once_with("u1", "users", "app1", is_external=True)


@pytest.mark.asyncio
async def test_a_grant_on_a_record_reads_no_membership_and_a_team_write_with_none_flagged_reads_once() -> None:
    store, provider, calls = _store()
    await store.batch_create_edges([_grant("users", "u1")], "permission")
    provider.lock_external_app_memberships.assert_not_awaited()      # a record's grant pays nothing for the team path
    await store.batch_create_edges([_grant("users", "u1", tc="teams", ti="t1")], "permission")
    assert provider.lock_external_app_memberships.await_count == 1      # nothing flagged: no read after


@pytest.mark.asyncio
async def test_a_team_write_run_again_keeps_what_its_first_attempt_read() -> None:
    """The first attempt read u1's membership and conflicted (ArangoDB, behind the sweep); the runner runs it again in a new snapshot where the
    removal is committed: the second attempt reads nothing, but the runner's one set still holds what the first read, so it is made again."""
    from app.connectors.core.base.data_store import graph_data_store
    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore

    class Conflict(Exception):
        code = 1200
    import logging
    attempts: list[int] = []

    async def write(*_a: object, **_k: object) -> bool:
        attempts.append(1)
        if len(attempts) == 1:
            raise Conflict("write-write conflict")
        return True
    provider = MagicMock(begin_transaction=AsyncMock(return_value="t"), rollback_transaction=AsyncMock(), commit_transaction=AsyncMock(),
                         batch_create_edges=AsyncMock(side_effect=write), is_write_conflict=MagicMock(return_value=True),
                         is_transient_error=MagicMock(return_value=True), opens_explicit_transaction=MagicMock(return_value=True),
                         lock_external_app_memberships=AsyncMock(side_effect=[[("users", "u1", "app1")], [], []]),
                         external_memberships_kept=AsyncMock(return_value=[("users", "u1", "app1")]),
                         ensure_app_membership=AsyncMock())
    provider.logger = logging.getLogger("team-carry-test")
    graph_data_store._retry_delay, real = (lambda _a: 0), graph_data_store._retry_delay
    try:
        async def add(tx_store: object) -> None:
            await tx_store.batch_create_edges([_grant("users", "u1", tc="teams", ti="t1")], "permission")
        await GraphDataStore(provider.logger, provider).execute_idempotent_in_transaction(add, explicit=True)
    finally:
        graph_data_store._retry_delay = real
    assert len(attempts) == 2
    provider.ensure_app_membership.assert_awaited_once()
    assert provider.ensure_app_membership.await_args.args[:3] == ("u1", "users", "app1")
