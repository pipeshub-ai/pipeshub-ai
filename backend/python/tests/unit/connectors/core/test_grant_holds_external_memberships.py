"""A grant to a user or person is written only after that principal's flagged app memberships are locked in the same transaction (the lock
the external-collaborator reaper takes), so a reaper cannot remove a membership on the strength of a grant it could not see yet."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore


def _store() -> tuple[GraphTransactionStore, MagicMock, list[str]]:
    calls: list[str] = []

    def rec(name: str, value: object = None) -> AsyncMock:
        return AsyncMock(side_effect=lambda *a, **k: calls.append(name) or value)

    provider = MagicMock(
        lock_external_app_memberships=rec("lock"), batch_create_edges=rec("write", True), replace_edges_to=rec("write"),
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
HELD = [("person", "p1"), ("users", "u1"), ("users", "u2")]


@pytest.mark.asyncio
@pytest.mark.parametrize("write", [
    lambda s: s.batch_create_edges(EDGES, "permission"),
    lambda s: s.replace_edges_to("r1", "records", EDGES, "permission"),
    lambda s: s.replace_record_permissions("r1", EDGES, None, inherit=False),
    lambda s: s.create_edges_if_absent(EDGES, "permission"),
])
async def test_a_grant_locks_its_users_and_people_s_memberships_before_it_is_written(write) -> None:
    store, provider, calls = _store()
    await write(store)
    provider.lock_external_app_memberships.assert_awaited_once_with(HELD, transaction="txn-1")
    assert calls == ["lock", "write"]


@pytest.mark.asyncio
async def test_a_group_membership_locks_its_member_s_memberships() -> None:
    store, provider, calls = _store()
    assert await store.create_user_group_membership("src-u1", "ext-g1", "app1")
    provider.lock_external_app_memberships.assert_awaited_once_with([("users", "u1")], transaction="txn-1")
    assert calls == ["lock", "write"]


@pytest.mark.asyncio
async def test_a_record_group_grant_locks_its_users_memberships() -> None:
    from app.models.permission import EntityType, Permission, PermissionType
    store, provider, calls = _store()
    store.get_user_by_email = AsyncMock(return_value=MagicMock(id="u1"))
    await store.batch_upsert_record_group_permissions(
        "rg1", [Permission(email="u1@example.com", type=PermissionType.READ, entity_type=EntityType.USER)], "app1", "org1")
    provider.lock_external_app_memberships.assert_awaited_once_with([("users", "u1")], transaction="txn-1")
    assert calls == ["lock", "write"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("edges", "collection"), [
    ([_grant("groups", "g1"), _grant("organizations", "o1"), _grant("roles", "x")], "permission"),
    ([_grant("users", "u1", "apps", "app1")], "userAppRelation"),
    ([_grant("users", "u1", "apps", "kb1"), _grant("person", "p1", "agentTemplates", "t1")], "permission"),  # no grant the reaper counts
    ([], "permission"),
])
async def test_no_lock_when_the_write_grants_no_user_or_person(edges, collection) -> None:
    store, provider, _ = _store()
    await store.batch_create_edges(edges, collection)
    provider.lock_external_app_memberships.assert_not_awaited()


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
    await store.batch_create_edges([grant, _grant("users", "u1")], "permission")
    first, second = (c.args[0] for c in provider.batch_create_edges.await_args_list)
    assert first[0] == grant and first[1] == second[1]
    assert second[0] == ({**grant, "from_id": "p2"} if edge_shape == "generic" else {**grant, "_from": "person/p2"})
    assert store.upsert_person_by_email.await_args.args[0].email == "p1@example.com"
    assert calls == ["lock", "write", "write"]


@pytest.mark.asyncio
async def test_without_a_transaction_a_person_removed_every_time_fails_the_write() -> None:
    from app.exceptions.graph_db_exceptions import GraphWriteError
    store, provider, _ = _store()
    store.explicit = False
    provider.get_document = AsyncMock(side_effect=[{"_key": "p1", "email": "p@example.com", "orgId": "o"}, None, None, None])  # read, then gone each time
    store.upsert_person_by_email = AsyncMock(side_effect=["p2", "p3", "p4"])
    with pytest.raises(GraphWriteError):
        await store.batch_create_edges([_grant("person", "p1")], "permission")


@pytest.mark.asyncio
async def test_in_a_transaction_the_person_is_not_read_again() -> None:
    store, provider, calls = _store()
    provider.get_document = AsyncMock()
    await store.batch_create_edges([_grant("person", "p1")], "permission")
    provider.get_document.assert_not_awaited()
    assert calls == ["lock", "write"]


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
                         opens_explicit_transaction=MagicMock(return_value=True))
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
