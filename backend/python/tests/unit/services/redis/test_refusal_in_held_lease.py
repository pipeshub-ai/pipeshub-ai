"""The page-limit refusal completes inside a held consumer record lease on the Redis lease manager."""
from __future__ import annotations

import pytest

pytest.importorskip("fakeredis.aioredis")

from tests.support.redis_provider_matrix import CLIENT_TRANSPORTS as TRANSPORTS
from tests.support.refusal_in_lease import refuse_inside_held_lease
from tests.unit.services.redis.test_lease_manager_contract import _make_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("make_client", TRANSPORTS)
async def test_a_refusal_completes_inside_the_held_record_lease(make_client) -> None:
    manager = await _make_manager(make_client)
    try:
        await refuse_inside_held_lease(manager)
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("make_client", TRANSPORTS)
async def test_a_refusal_that_takes_the_record_lease_is_caught(make_client) -> None:
    """The negative control: the same body fails when the un-index step acquires through ``RecordLeases``."""
    manager = await _make_manager(make_client)
    try:
        with pytest.raises(AssertionError, match="asked for a lease"):
            await refuse_inside_held_lease(manager, acquire_in_unindex=True)
    finally:
        await manager.cleanup()
