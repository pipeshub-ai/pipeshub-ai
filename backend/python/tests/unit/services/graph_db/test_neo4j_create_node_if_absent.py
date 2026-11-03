"""``create_node_if_absent`` answers "not created" only when the requested key exists."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

CONSTRAINT = RuntimeError("Node(1) already exists with label `Teams` and property `email` = 'a@b.c' (ConstraintValidation)")


CONSTRAINT_PRESENT = [{"found": 1}]


def _provider(*results: object, constraint: object = CONSTRAINT_PRESENT) -> Neo4jProvider:
    """The first query each process makes for a label checks its unique id constraint."""
    p = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=[constraint, *results])
    return p


async def test_a_lost_race_on_the_key_is_not_created() -> None:
    p = _provider(CONSTRAINT, [{"found": 1}])
    assert await p.create_node_if_absent("k1", "teams", {"name": "t"}) is False
    assert p.client.execute_query.await_count == 3


async def test_another_unique_constraint_is_raised_not_reported_as_existing() -> None:
    p = _provider(CONSTRAINT, [])
    with pytest.raises(RuntimeError, match="ConstraintValidation"):
        await p.create_node_if_absent("k1", "teams", {"name": "t"})


async def test_a_check_that_fails_too_raises_the_original_error() -> None:
    p = _provider(CONSTRAINT, RuntimeError("transaction aborted"))
    with pytest.raises(RuntimeError, match="ConstraintValidation"):
        await p.create_node_if_absent("k1", "teams", {"name": "t"}, transaction="tx")


async def test_other_errors_are_not_checked() -> None:
    p = _provider(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await p.create_node_if_absent("k1", "teams", {"name": "t"})
    assert p.client.execute_query.await_count == 2


async def test_the_requested_key_wins_over_a_key_in_the_node() -> None:
    p = _provider([{"created": True}])
    assert await p.create_node_if_absent("k1", "teams", {"_key": "other", "name": "t"}) is True
    params = p.client.execute_query.await_args_list[1].kwargs["parameters"]
    assert params["key"] == "k1"
    assert params["props"]["id"] == "k1"


async def test_no_write_without_the_unique_id_constraint() -> None:
    """Without it two racing creates of one key could both succeed and both be told "created"."""
    p = _provider(constraint=[{"found": 0}])
    with pytest.raises(RuntimeError, match="unique constraint"):
        await p.create_node_if_absent("k1", "teams", {"name": "t"})
    assert p.client.execute_query.await_count == 1


async def test_the_constraint_is_checked_once_per_label() -> None:
    p = _provider([{"created": True}], [{"created": False}])
    await p.create_node_if_absent("k1", "teams", {"name": "t"})
    await p.create_node_if_absent("k2", "teams", {"name": "u"})
    assert p.client.execute_query.await_count == 3
