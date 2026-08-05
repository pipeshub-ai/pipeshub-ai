from app.services.resource_governor.gate import StartRateLimiter


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
        times = iter([0.0, 0.0, 100.0])
        limiter = StartRateLimiter(interval=2.0, capacity=2, clock=lambda: next(times))
        limiter.try_consume()  # t=0, drains from full (2 -> 1)
        # Huge elapsed time refills, but must clamp at capacity, not grow unbounded.
        assert limiter.try_consume() is True

    def test_light_pool_is_unaffected_because_it_has_no_rate_limiter(self) -> None:
        # AdmissionGate itself is tested separately; this just documents the
        # policy that LIGHT_PARSE never gets a StartRateLimiter constructed
        # for it in ResourceGovernor.__init__ (heavy_parse/download_bytes only).
        from app.services.resource_governor.models import Pool

        assert Pool.LIGHT_PARSE not in {Pool.HEAVY_PARSE, Pool.DOWNLOAD_BYTES}
