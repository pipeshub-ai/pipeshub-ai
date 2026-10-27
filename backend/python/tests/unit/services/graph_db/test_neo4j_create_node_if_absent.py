"""``create_node_if_absent`` answers "not created" only when the requested key exists."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

CONSTRAINT = RuntimeError("Node(1) already exists with label `Teams` and property `email` = 'a@b.c' (ConstraintValidation)")


def _provider(*results: object) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=list(results))
    return p


async def test_a_lost_race_on_the_key_is_not_created() -> None:
    p = _provider(CONSTRAINT, [{"found": 1}])
    assert await p.create_node_if_absent("k1", "teams", {"name": "t"}) is False
    assert p.client.execute_query.await_count == 2


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
    assert p.client.execute_query.await_count == 1
