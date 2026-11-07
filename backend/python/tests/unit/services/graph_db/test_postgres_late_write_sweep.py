"""PostgreSQL's late-write pass: the blocks and stage states written for a deleted connector, in batches that commit on their own."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.org_context import for_org
from app.services.graph_db.postgres import delete as pg_delete
from app.services.graph_db.postgres.provider import PostgresGraphProvider


class Tx:
    """``named`` is what each batch's SELECT finds, per collection, in order."""

    def __init__(self, named: dict[str, list[int]], left: int, links: list[int] = (), links_left: int = 0) -> None:
        self.named = {coll: list(sizes) for coll, sizes in named.items()}
        self.left = left
        self.links, self.links_left = list(links), links_left
        self.selects: list[tuple] = []
        self.link_deletes: list[tuple] = []

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        assert "late_write_named" in sql
        self.selects.append(args)
        org, coll, connector_id, limit = args
        assert (org, connector_id, limit) == ("org1", "c1", pg_delete._CHUNK)
        size = self.named[coll].pop(0) if self.named[coll] else 0
        return [{"id": f"{coll}-{i}"} for i in range(size)]

    async def fetchval(self, sql: str, *args: object) -> int:
        if "late_write_links_left" in sql:
            assert args == ("org1", ["nodeRelations", "recordLinks"], ["rg1"])
            return self.links_left
        if "late_write_links" in sql:
            assert args == ("org1", ["nodeRelations", "recordLinks"], ["rg1"], pg_delete._CHUNK) and "doc->>'recordGroupId'" in sql
            self.link_deletes.append(args)
            return self.links.pop(0) if self.links else 0
        assert "late_write_left" in sql and args == ("org1", ["blocks", "stageStates"], "c1")
        return self.left


def provider(tx: Tx) -> tuple[PostgresGraphProvider, list[bool]]:
    p = PostgresGraphProvider(logging.getLogger("t"), MagicMock())
    writes: list[bool] = []

    async def q(fn, transaction, *, write=False) -> object:
        assert transaction is None  # every batch commits on its own
        writes.append(write)
        return await fn(tx)

    p._q = q  # type: ignore[method-assign]
    return p, writes


@pytest.fixture(autouse=True)
def removed_as_found(monkeypatch) -> AsyncMock:
    remove = AsyncMock(side_effect=lambda tx, org, coll, keys: len(keys))
    monkeypatch.setattr(pg_delete, "_remove_nodes", remove)
    return remove


@pytest.mark.asyncio
async def test_a_pass_removes_the_blocks_and_stage_states_batch_by_batch_then_counts(removed_as_found) -> None:
    tx = Tx({"blocks": [pg_delete._CHUNK, 2], "stageStates": [1]}, left=0)
    p, writes = provider(tx)
    with for_org("org1", job="t"):
        assert await p.sweep_deleted_connector_writes("c1", ["rg1"]) == (pg_delete._CHUNK + 3, 0)
    assert [a[1] for a in tx.selects] == ["blocks", "blocks", "stageStates"]
    assert writes == [True, True, True, True, False]  # the three node batches, one link batch, then the count
    assert [c.args[2] for c in removed_as_found.await_args_list] == ["blocks", "blocks", "stageStates"]


@pytest.mark.asyncio
async def test_links_of_the_groups_are_removed_in_batches_and_counted_in_what_is_left() -> None:
    """The link writer checks the ends without locking them, so a link can commit after its block and its edges went."""
    tx = Tx({"blocks": [], "stageStates": []}, left=1, links=[pg_delete._CHUNK, 3], links_left=2)
    p, _ = provider(tx)
    with for_org("org1", job="t"):
        assert await p.sweep_deleted_connector_writes("c1", ["rg1", "", "rg1"]) == (pg_delete._CHUNK + 3, 3)
    assert len(tx.link_deletes) == 2


@pytest.mark.asyncio
async def test_without_groups_no_link_is_touched() -> None:
    tx = Tx({"blocks": [], "stageStates": []}, left=0, links=[7], links_left=7)
    p, _ = provider(tx)
    with for_org("org1", job="t"):
        assert await p.sweep_deleted_connector_writes("c1") == (0, 0)
    assert tx.link_deletes == []


@pytest.mark.asyncio
async def test_what_is_left_after_the_pass_is_reported() -> None:
    p, _ = provider(Tx({"blocks": [], "stageStates": []}, left=4))
    with for_org("org1", job="t"):
        assert await p.sweep_deleted_connector_writes("c1") == (0, 4)


@pytest.mark.asyncio
async def test_a_failing_batch_fails_the_pass() -> None:
    """The late-write sweep retries a pass that raised; it must not read as an empty connector."""
    p, _ = provider(Tx({"blocks": [1], "stageStates": []}, left=0))
    with for_org("org1", job="t"), patch.object(pg_delete, "_remove_nodes", AsyncMock(side_effect=RuntimeError("deadlock"))), \
            pytest.raises(RuntimeError, match="deadlock"):
        await p.sweep_deleted_connector_writes("c1")
