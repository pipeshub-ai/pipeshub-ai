"""A team edit is all or nothing on Neo4j and ArangoDB.

`update_team` renamed the team and removed members before its later checks and writes, each write committing on its own,
so a request answered with an error kept part of its change. The edit now runs in one transaction: a write that fails
after the rename and the removal leaves the team as it was. The team-member writes raise when they fail, since a
swallowed failure would let the transaction commit the rest. The PostgreSQL twin is postgres/test_team_update_transaction.py.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD;
without them each case skips.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.api.routes.entity import ROLE_WRITE_FAILED
from tests.integration.graph_db.team_update_support import (
    TeamWorld,
    plan_team,
    seed_team,
    send_edit,
    stored,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "team_update_tx_it"

logger = logging.getLogger("team-update-tx-it")


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[TeamWorld]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        team = plan_team(graph, f"org-team-tx-{run}", run)

        async def remove() -> None:
            for collection, keys in team.keys().items():
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(keys, collection)

        cleanup.push_async_callback(remove)      # before seeding: a seed that fails partway is cleaned up too
        await seed_team(team)
        yield team


async def test_a_write_that_fails_after_the_rename_and_removal_leaves_the_team_as_it_was(
    world: TeamWorld, monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = await stored(world)
    role_write = AsyncMock(side_effect=RuntimeError("forced outage"))
    monkeypatch.setattr(world.graph, "batch_update_team_member_roles", role_write)

    assert await send_edit(world, world.edit()) == (500, ROLE_WRITE_FAILED)
    role_write.assert_awaited_once()      # the failure was reached, after the rename and the removal
    assert await stored(world) == before


async def test_an_accepted_edit_lands_whole(world: TeamWorld) -> None:
    assert await send_edit(world, world.edit()) == (200, None)
    assert await stored(world) == ("after", {world.owner[0]: "OWNER", world.stays[0]: "WRITER", world.leaves[0]: None})


@pytest.mark.parametrize(("method", "args"), [
    ("delete_team_member_edges", lambda w: (w.team_key, [w.leaves[1]])),
    ("batch_update_team_member_roles", lambda w: (w.team_key, [{"userId": w.stays[1], "role": "WRITER"}], 1)),
])
async def test_a_failed_team_member_write_raises(world: TeamWorld, method: str, args) -> None:
    with pytest.raises(Exception):  # noqa: B017 - each store fails an unknown transaction with its own error
        await getattr(world.graph, method)(*args(world), transaction="no-such-transaction")
    assert (await stored(world))[1][world.leaves[0]] == "READER"
