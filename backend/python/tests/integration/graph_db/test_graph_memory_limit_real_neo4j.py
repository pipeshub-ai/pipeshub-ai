"""Neo4j at the small-VM memory preset: a bulk write the memory pool refuses fails as GraphMemoryLimitError.

Same server and environment as `test_replace_edges_to_memory_real_neo4j.py` (see its docstring). The
statements here go through the production path: `GraphTransactionStore.replace_edges_to` -> `Neo4jProvider`
-> `Neo4jClient`, inside `GraphDataStore.execute_in_transaction`, whose attempts are counted.

Run one group of tests per fresh container (back-to-back heavy runs got the container OOM-killed).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import TransientError

from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    retry_on_deadlock,
)
from app.exceptions.graph_db_exceptions import GraphMemoryLimitError
from tests.integration.graph_db.replace_edges_support import (
    GROUPS,
    PERMISSION,
    clear_prefix,
    permissions,
)
from tests.integration.graph_db.test_replace_edges_to_memory_real_neo4j import Pair
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.timeout(3000),
    pytest.mark.skipif(os.environ.get("NEO4J_IT_PRESET") != "small", reason="needs a neo4j at the small preset"),
]

logger = logging.getLogger("graph-memory-limit-neo4j-it")
PAIR_THAT_FITS = int(os.environ.get("NEO4J_IT_PAIR_N", "60000"))


@pytest.fixture
async def pair(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Pair]:
    try:
        provider = await connect_neo4j(logger, monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    p = Pair(provider, f"gm{uuid.uuid4().hex[:8]}")
    try:
        yield p
    finally:
        try:
            await clear_prefix(provider, p.prefix)
        except Exception:
            logger.warning("neo4j cleanup failed", exc_info=True)
        await provider.disconnect()


async def replace_once(provider: Neo4jProvider, group: str, edges: list[dict]) -> int:
    """The production replace in a transaction; returns how many times the block ran."""
    store = GraphDataStore(MagicMock(), provider)
    attempts = 0

    async def write(tx_store) -> None:
        nonlocal attempts
        attempts += 1
        await tx_store.replace_edges_to(group, GROUPS, edges, PERMISSION)

    try:
        await store.execute_in_transaction(write, explicit=True)
    except BaseException as exc:
        exc.attempts = attempts  # type: ignore[attr-defined]
        raise
    return attempts


async def refused(pair: Pair, group: str, start: int, stop: int, before: tuple[int, int, int]) -> GraphMemoryLimitError:
    """A replace of the group by users start..stop-1 fails as GraphMemoryLimitError after one attempt, group unchanged."""
    await pair.ensure_users(stop)
    with pytest.raises(GraphMemoryLimitError) as raised:
        await replace_once(pair.provider, group, permissions(pair.ids(start, stop), group))
    err = raised.value
    assert err.attempts == 1  # type: ignore[attr-defined]
    assert err.__cause__ is None and err.__context__ is None
    assert not isinstance(err, TransientError)
    assert await pair.source_range(group) == before  # previous edges intact: count and members
    return err


class TestRefusedBulkWrites:
    """IT-6 rows d, e, g."""

    async def test_d_full_swap_of_160k_fails_once_and_leaves_the_group(self, pair: Pair) -> None:
        group = await pair.group("d", 0, 160_000)
        err = await refused(pair, group, 160_000, 320_000, (160_000, 0, 159_999))
        assert "NEO4J_HEAP_MAX" in str(err)
        assert err.size == 160_000

    async def test_e_fresh_200k_group_passes_and_fresh_300k_fails(self, pair: Pair) -> None:
        await pair.ensure_users(300_000)
        fresh200 = await pair.group("e200")
        assert await replace_once(pair.provider, fresh200, permissions(pair.ids(0, 200_000), fresh200)) == 1
        assert (await pair.source_range(fresh200))[0] == 200_000

        fresh300 = await pair.group("e300")
        await refused(pair, fresh300, 0, 300_000, (0, None, None))

    async def test_g_400k_full_swap_fails_naming_the_setting(self, pair: Pair) -> None:
        group = await pair.group("g", 0, 400_000)
        err = await refused(pair, group, 400_000, 800_000, (400_000, 0, 399_999))
        assert "NEO4J_HEAP_MAX" in str(err)


class TestSharedPool:
    async def test_f_pair_that_fits_commits_both_through_the_production_path(self, pair: Pair) -> None:
        """Measured, commit-time (slice file, D6.4): two full swaps of 60k over disjoint members."""
        n = PAIR_THAT_FITS
        groups = [await pair.group("p1", 0, n), await pair.group("p2", 2 * n, 3 * n)]
        await pair.ensure_users(4 * n)
        done = await asyncio.gather(
            replace_once(pair.provider, groups[0], permissions(pair.ids(n, 2 * n), groups[0])),
            replace_once(pair.provider, groups[1], permissions(pair.ids(3 * n, 4 * n), groups[1])),
        )
        assert done == [1, 1]
        assert await pair.source_range(groups[0]) == (n, n, 2 * n - 1)
        assert await pair.source_range(groups[1]) == (n, 3 * n, 4 * n - 1)

    async def test_f_overloaded_pair_fails_only_as_memory_limit_and_never_retries(self, pair: Pair) -> None:
        n = 100_000
        groups = [await pair.group("o1", 0, n), await pair.group("o2", 2 * n, 3 * n)]
        await pair.ensure_users(4 * n)
        outcomes = await asyncio.gather(
            replace_once(pair.provider, groups[0], permissions(pair.ids(n, 2 * n), groups[0])),
            replace_once(pair.provider, groups[1], permissions(pair.ids(3 * n, 4 * n), groups[1])),
            return_exceptions=True,
        )
        logger.info("overloaded pair: %s", [type(o).__name__ for o in outcomes])
        old, new = [(n, 0, n - 1), (n, 2 * n, 3 * n - 1)], [(n, n, 2 * n - 1), (n, 3 * n, 4 * n - 1)]
        for group, outcome, before, after in zip(groups, outcomes, old, new, strict=True):
            if isinstance(outcome, BaseException):
                assert isinstance(outcome, GraphMemoryLimitError), repr(outcome)
                assert outcome.attempts == 1  # type: ignore[attr-defined]
                assert await pair.source_range(group) == before
            else:
                assert await pair.source_range(group) == after

    async def test_h_one_edge_victim_retries_and_succeeds_while_the_pool_is_overloaded(self, pair: Pair) -> None:
        """R5-3: a 1-edge batch_create_edges is not marked bulk, so it keeps the bounded retry and gets through."""
        n = 100_000
        groups = [await pair.group("v1", 0, n), await pair.group("v2", 2 * n, 3 * n)]
        victim_group = await pair.group("victim", 0, 1)
        await pair.ensure_users(4 * n)
        big = [
            asyncio.create_task(replace_once(pair.provider, groups[0], permissions(pair.ids(n, 2 * n), groups[0]))),
            asyncio.create_task(replace_once(pair.provider, groups[1], permissions(pair.ids(3 * n, 4 * n), groups[1]))),
        ]
        store = GraphDataStore(MagicMock(), pair.provider)
        failures: list[BaseException] = []

        class Victim:
            data_store_provider = store

            @retry_on_deadlock()
            async def write(self_) -> None:
                try:
                    async with store.transaction(explicit=True) as tx_store:
                        await tx_store.batch_create_edges(permissions([pair.uid(7)], victim_group), PERMISSION)
                except BaseException as exc:
                    failures.append(exc)
                    raise

        while not all(t.done() for t in big):
            await Victim().write()
            await asyncio.sleep(0.2)
        await Victim().write()
        await asyncio.gather(*big, return_exceptions=True)

        assert all(isinstance(f, TransientError) and not isinstance(f, GraphMemoryLimitError) for f in failures), failures
        logger.info("victim: %d transient failures, all retried", len(failures))
