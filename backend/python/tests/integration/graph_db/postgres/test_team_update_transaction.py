"""A team edit is all or nothing on PostgreSQL: the twin of ../test_team_update_transaction_real_backends.py.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none; without it each case skips.
"""
from __future__ import annotations

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

from .test_identity import w  # noqa: F401  (the fixture)

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


@pytest.fixture
async def world(w: World) -> TeamWorld:  # noqa: F811
    team = plan_team(w.p, w.org, uuid.uuid4().hex[:10])      # `w` deletes the whole org afterwards
    await seed_team(team)
    return team


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


async def test_a_failed_member_removal_raises(world: TeamWorld) -> None:
    with pytest.raises(Exception):  # noqa: B017 - the unknown transaction's TransactionClosed, no longer answered with []
        await world.graph.delete_team_member_edges(world.team_key, [world.leaves[1]], transaction="no-such-transaction")
    assert (await stored(world))[1][world.leaves[0]] == "READER"
