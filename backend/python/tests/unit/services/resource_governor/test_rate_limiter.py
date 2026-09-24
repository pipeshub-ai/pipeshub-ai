import logging

from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.gate import StartRateLimiter
from app.services.resource_governor.models import Pool, ResourceSnapshot


class _StubProbe:
    """Fixed, healthy snapshot — only used to construct a governor whose
    rate-limiter wiring we want to inspect, not to drive sampling."""

    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            cpu_quota=4.0,
            cpu_utilisation=0.1,
            cpu_throttled_ratio=0.0,
            cpu_pressure=0.0,
            mem_limit_bytes=4 * 1024 ** 3,
            mem_working_set_bytes=1 * 1024 ** 3,
            source="stub",
        )


class TestStartRateLimiter:
    def test_admits_up_to_capacity_immediately(self) -> None:
        clock = iter([0.0, 0.0, 0.0, 0.0])
        limiter = StartRateLimiter(interval=2.0, capacity=2, clock=lambda: next(clock))
        assert limiter.try_consume() is True
        assert limiter.try_consume() is True
        assert limiter.try_consume() is False

    def test_refills_one_token_per_interval(self) -> None:
        # One extra leading value: the constructor itself reads the clock
        # once to seed `_last_refill`.
        times = iter([0.0, 0.0, 0.0, 2.0, 2.0])
        limiter = StartRateLimiter(interval=2.0, capacity=1, clock=lambda: next(times))
        assert limiter.try_consume() is True   # consumes the only token at t=0
        assert limiter.try_consume() is False  # still t=0, no refill yet
        # advance by exactly one interval -> exactly one token back
        assert limiter.try_consume() is True
        assert limiter.try_consume() is False

    def test_never_exceeds_capacity(self) -> None:
        times = iter([0.0, 0.0, 100.0, 100.0, 100.0])
        limiter = StartRateLimiter(interval=2.0, capacity=2, clock=lambda: next(times))
        limiter.try_consume()  # t=0, drains from full (2 -> 1)
        # Huge elapsed time refills, but must clamp at capacity, not grow unbounded.
        assert limiter.try_consume() is True
        # Only `capacity` tokens were banked despite 100s of elapsed time,
        # so exactly one more admission is available, not 50.
        assert limiter.try_consume() is True
        assert limiter.try_consume() is False

    def test_light_pool_gets_no_start_rate_limiter(self) -> None:
        # LIGHT_PARSE is deliberately never rate-limited (plan section 4,
        # "Light parses are never rate-limited"); assert that directly
        # against the governor's wiring rather than the Pool enum.
        governor = ResourceGovernor(logger=logging.getLogger("test.rate_limiter"), probe=_StubProbe())
        assert governor._rate_limiters.get(Pool.LIGHT_PARSE) is None
        assert governor._rate_limiters.get(Pool.HEAVY_PARSE) is not None
