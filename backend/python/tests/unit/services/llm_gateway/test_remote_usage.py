"""A remote model request is spent once per request id, and what it spent is kept for a caller that lost the reply."""

from app.services.llm_gateway import remote_usage
from app.services.llm_gateway.fleet_redis import FleetRedis


async def test_a_second_delivery_of_a_request_spends_nothing_and_finds_the_first_ones_usage(fleet: FleetRedis) -> None:
    assert await remote_usage.claim("classify:vr:rev", "req-1") is True
    assert await remote_usage.claim("classify:vr:rev", "req-1") is None, "still running: refused, nothing to report yet"
    await remote_usage.record("classify:vr:rev", "req-1", {"failed": 2, "charged": 1})
    assert await remote_usage.claim("classify:vr:rev", "req-1") == {"failed": 2, "charged": 1}
    assert await remote_usage.read("classify:vr:rev", "req-1") == {"failed": 2, "charged": 1}
    assert await remote_usage.claim("classify:vr:rev", "req-2") is True, "another request of the job is its own"


async def test_without_a_request_id_every_delivery_may_spend(fleet: FleetRedis) -> None:
    assert await remote_usage.claim("classify:vr:rev", "") is True
    assert await remote_usage.claim("classify:vr:rev", "") is True
    assert await remote_usage.claim("classify:vr:rev", "x" * 65) is True, "an over-long id is not stored"
    assert await remote_usage.read("classify:vr:rev", "missing") is None
