"""One user group the graph server's memory pool refuses must not take the others with it.

``on_new_user_groups`` writes each group in a transaction of its own, so concurrent syncs of
groups that share members do not deadlock. A group whose member replace fails with
``GraphMemoryLimitError`` is skipped and named; the others commit.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import Neo4jError, TransientError

from app.config.constants.arangodb import Connectors as ConnectorsEnum
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.sources.localKB.push.retry import is_attempt_lost
from app.exceptions.graph_db_exceptions import GraphMemoryLimitError
from app.models.entities import AppUserGroup


class _Store:
    """Transactions that keep what was written only when they exit without an error."""

    def __init__(
        self,
        refused: set[str],
        broken: frozenset[str] = frozenset(),
        *,
        contended: frozenset[str] = frozenset(),
        full_at: int | None = None,
    ) -> None:
        self.refused = refused
        self.broken = broken
        self.attempts: dict[str, int] = {}
        # groups whose write the pool refuses with the driver's own error (not marked as bulk)
        self.contended = contended
        # a transaction that has already written this many groups is refused by the pool on the next
        self.full_at = full_at
        self.committed: list[str] = []
        self.transactions = 0
        self.replaces = 0

    def transaction(self, **_kwargs: object) -> object:
        self.transactions += 1
        store = self
        written: list[str] = []

        class Ctx:
            async def __aenter__(self_) -> MagicMock:
                tx = AsyncMock()
                tx.explicit = True
                tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="existing"))
                tx.written = written
                return tx

            async def __aexit__(self_, exc_type, exc, tb) -> bool:
                if exc_type is None:
                    store.committed.extend(written)
                return False

        return Ctx()


def _pool_error() -> TransientError:
    err = Neo4jError._hydrate_neo4j(
        code="Neo.TransientError.General.MemoryPoolOutOfMemoryError", message="dbms.memory.transaction.total.max",
    )
    assert isinstance(err, TransientError)
    return err


def _group(name: str, connector_id: str = "conn-1") -> AppUserGroup:
    return AppUserGroup(
        app_name=ConnectorsEnum.GOOGLE_MAIL, connector_id=connector_id, source_user_group_id=name, name=name,
    )


def _processor(store: _Store) -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    proc.data_store_provider.transaction = store.transaction
    proc._access_changed = AsyncMock()

    async def write_edges(tx_store, to_id, to_collection, permissions, *, replace) -> None:
        store.replaces += 1
        # to_id is the group's id: the upsert stub below records which group this is
        name = tx_store.current
        store.attempts[name] = store.attempts.get(name, 0) + 1
        if name in store.refused:
            raise GraphMemoryLimitError(f"{name} too large", operation="group replace", size=9)
        if name in store.contended or (store.full_at is not None and len(tx_store.written) >= store.full_at):
            raise _pool_error()
        if name in store.broken:
            raise RuntimeError(f"{name} broke")
        tx_store.written.append(name)

    proc._write_permission_edges = write_edges
    return proc


def _wire(proc: DataSourceEntitiesProcessor) -> None:
    """Each tx store learns the group being written from the upsert call."""
    original = proc._write_user_group

    async def write_user_group(tx_store, user_group, members, *, replace_members) -> bool:
        tx_store.current = user_group.name
        return await original(tx_store, user_group, members, replace_members=replace_members)

    proc._write_user_group = write_user_group


class TestIsolation:
    @pytest.mark.asyncio
    async def test_the_group_the_pool_refuses_is_skipped_and_the_others_commit(self) -> None:
        store = _Store({"B"})
        proc = _processor(store)
        _wire(proc)

        with pytest.raises(GraphMemoryLimitError) as raised:
            await proc.on_new_user_groups([(_group("A"), []), (_group("B"), []), (_group("C"), [])])

        assert store.committed == ["A", "C"]
        assert raised.value.groups == ["B"]
        assert "B" in str(raised.value) and "NEO4J_HEAP_MAX" in str(raised.value)
        # one transaction per group
        assert store.transactions == 3
        # access is re-evaluated for the connector of the groups that did commit
        proc._access_changed.assert_awaited_once_with({"conn-1"})
        # not a retry loop: each group is written once
        assert store.replaces == 3
        # nothing in the aggregated error looks transient to a retry loop
        assert raised.value.__cause__ is None and raised.value.__context__ is None
        assert not is_attempt_lost(raised.value)

    @pytest.mark.asyncio
    async def test_another_error_in_the_fallback_still_drops_access_for_the_groups_written(self) -> None:
        """A committed group's members changed, so the cached access lists must drop even when a
        later group fails with an error that is not the memory pool's."""
        store = _Store({"B"}, broken=frozenset({"C"}))
        proc = _processor(store)
        _wire(proc)

        with pytest.raises(RuntimeError, match="C broke"):
            await proc.on_new_user_groups([(_group("A"), []), (_group("B"), []), (_group("C"), [])])

        assert store.committed == ["A"]
        proc._access_changed.assert_awaited_once_with({"conn-1"})

    @pytest.mark.asyncio
    async def test_access_of_a_group_written_before_another_error_is_dropped(self) -> None:
        """A group that committed before another group's error stays written, so its connector's
        access must drop even though the call raised."""
        store = _Store(set(), broken=frozenset({"B"}))
        proc = _processor(store)
        _wire(proc)

        with pytest.raises(RuntimeError, match="B broke"):
            await proc.on_new_user_groups([(_group("A", "conn-a"), []), (_group("B", "conn-b"), [])])

        assert store.committed == ["A"]
        proc._access_changed.assert_awaited_once_with({"conn-a"})

    @pytest.mark.asyncio
    async def test_each_group_is_written_in_a_transaction_of_its_own(self) -> None:
        """Groups share members: a transaction holding several groups' member locks deadlocked
        with another sync's, which took them in a different order."""
        store = _Store(set())
        proc = _processor(store)
        _wire(proc)

        await proc.on_new_user_groups([(_group("A"), []), (_group("B"), []), (_group("C"), [])])

        assert store.transactions == 3
        assert store.committed == ["A", "B", "C"]
        proc._access_changed.assert_awaited_once_with({"conn-1"})

    @pytest.mark.asyncio
    async def test_one_group_alone_raises_and_writes_nothing_else(self) -> None:
        store = _Store({"B"})
        proc = _processor(store)
        _wire(proc)

        with pytest.raises(GraphMemoryLimitError):
            await proc.on_new_user_groups([(_group("B"), [])])

        assert store.transactions == 1
        assert store.committed == []
        # nothing was written, so no connector's access changes
        proc._access_changed.assert_awaited_once_with(set())
        proc.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_every_group_refused_commits_nothing_and_names_them_all(self) -> None:
        store = _Store({"A", "B"})
        proc = _processor(store)
        _wire(proc)

        with pytest.raises(GraphMemoryLimitError) as raised:
            await proc.on_new_user_groups([(_group("A"), []), (_group("B"), [])])

        assert raised.value.groups == ["A", "B"]
        assert store.committed == []


class TestPoolFilledBySmallGroups:
    """Many groups under the bulk size added up to a refusal when they shared one transaction. That
    refusal carries the driver's error, not ``GraphMemoryLimitError``."""

    @pytest.mark.asyncio
    async def test_small_groups_never_add_up_in_one_transaction(self) -> None:
        # The pool refuses a transaction that has already written two groups.
        store = _Store(set(), full_at=2)
        proc = _processor(store)
        _wire(proc)

        await proc.on_new_user_groups([(_group("A"), []), (_group("B"), []), (_group("C"), [])])

        assert store.committed == ["A", "B", "C"]
        assert store.transactions == 3
        proc._access_changed.assert_awaited_once_with({"conn-1"})

    @pytest.mark.asyncio
    async def test_an_unmarked_refusal_of_one_group_propagates_and_is_retried_a_bounded_number_of_times(
        self,
    ) -> None:
        store = _Store(set(), contended=frozenset({"B"}))
        proc = _processor(store)
        _wire(proc)
        provider = MagicMock(spec=DataStoreProvider)
        provider.transaction = store.transaction
        provider.is_transient_error = lambda e, **_: isinstance(e, TransientError)
        proc.data_store_provider = provider

        with patch("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", AsyncMock()):
            with pytest.raises(TransientError):
                await proc.on_new_user_groups([(_group("A"), []), (_group("B"), []), (_group("C"), [])])

        # six attempts; each writes A alone (committed), then B alone (refused)
        assert store.transactions == 6 * 2
        assert store.committed == ["A"] * 6
        # access dropped for the written group on every attempt, error or not
        assert proc._access_changed.await_count == 6

    @pytest.mark.asyncio
    async def test_a_marked_refusal_is_still_skipped_and_named(self) -> None:
        store = _Store({"B"}, full_at=None)
        proc = _processor(store)
        _wire(proc)

        with pytest.raises(GraphMemoryLimitError) as raised:
            await proc.on_new_user_groups([(_group("A"), []), (_group("B"), []), (_group("C"), [])])

        assert raised.value.groups == ["B"]
        assert store.committed == ["A", "C"]
