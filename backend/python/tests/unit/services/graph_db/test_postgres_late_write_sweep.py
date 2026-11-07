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

    def __init__(self, named: dict[str, list[int]], left: int) -> None:
        self.named = {coll: list(sizes) for coll, sizes in named.items()}
        self.left = left
        self.selects: list[tuple] = []

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        assert "late_write_named" in sql
        self.selects.append(args)
        org, coll, connector_id, limit = args
        assert (org, connector_id, limit) == ("org1", "c1", pg_delete._CHUNK)
        size = self.named[coll].pop(0) if self.named[coll] else 0
        return [{"id": f"{coll}-{i}"} for i in range(size)]

    async def fetchval(self, sql: str, *args: object) -> int:
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
    assert writes == [True, True, True, False]
    assert [c.args[2] for c in removed_as_found.await_args_list] == ["blocks", "blocks", "stageStates"]


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
