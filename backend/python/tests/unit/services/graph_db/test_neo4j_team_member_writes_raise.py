"""Removing team members and changing their roles raise when the statement fails.

Both ran in `update_team`'s transaction. A swallowed failure there hid a write conflict from the transaction's owner, which
re-runs the block on one, and let the other writes commit without this one.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


class _Deadlock(Exception):
    pass


def _provider(error: Exception) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=error)
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("connection reset"), _Deadlock("DeadlockDetected")], ids=["outage", "conflict"])
@pytest.mark.parametrize(("method", "args"), [
    ("delete_team_member_edges", ("t1", ["u1"])),
    ("batch_update_team_member_roles", ("t1", [{"userId": "u1", "role": "WRITER"}], 1000)),
])
async def test_a_failed_team_member_write_raises_the_same_error(method: str, args: tuple, error: Exception) -> None:
    provider = _provider(error)

    with pytest.raises(type(error)) as raised:
        await getattr(provider, method)(*args, transaction="txn-1")

    assert raised.value is error
    assert provider.client.execute_query.await_args.kwargs["txn_id"] == "txn-1"
