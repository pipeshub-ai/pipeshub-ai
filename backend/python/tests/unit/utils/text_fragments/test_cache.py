from app.utils.text_fragments.cache import TtlCache, ttl_memoize


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestTtlCache:
    def test_miss_then_hit(self) -> None:
        cache = TtlCache(maxsize=4, ttl_seconds=10, clock=FakeClock())
        assert cache.get("k") == (False, None)
        cache.set("k", "v")
        assert cache.get("k") == (True, "v")

    def test_caches_falsy_values(self) -> None:
        cache = TtlCache(maxsize=4, ttl_seconds=10, clock=FakeClock())
        cache.set("k", "")
        assert cache.get("k") == (True, "")

    def test_entries_expire(self) -> None:
        clock = FakeClock()
        cache = TtlCache(maxsize=4, ttl_seconds=10, clock=clock)
        cache.set("k", "v")
        clock.now += 9.9
        assert cache.get("k") == (True, "v")
        clock.now += 0.2
        assert cache.get("k") == (False, None)
        assert len(cache) == 0

    def test_is_bounded_by_clearing_when_full(self) -> None:
        cache = TtlCache(maxsize=3, ttl_seconds=10, clock=FakeClock())
        for key in "abc":
            cache.set(key, key)
        assert len(cache) == 3
        cache.set("d", "d")
        assert len(cache) == 1
        assert cache.get("d") == (True, "d")

    def test_clear(self) -> None:
        cache = TtlCache(maxsize=3, ttl_seconds=10, clock=FakeClock())
        cache.set("a", 1)
        cache.clear()
        assert len(cache) == 0


class TestTtlMemoize:
    def test_calls_function_once_per_key(self) -> None:
        cache = TtlCache(maxsize=8, ttl_seconds=10, clock=FakeClock())
        calls: list[int] = []

        @ttl_memoize(cache, lambda x: x)
        def square(x: int) -> int:
            calls.append(x)
            return x * x

        assert [square(3), square(3), square(4)] == [9, 9, 16]
        assert calls == [3, 4]

    def test_none_key_bypasses_cache(self) -> None:
        cache = TtlCache(maxsize=8, ttl_seconds=10, clock=FakeClock())
        calls: list[object] = []

        @ttl_memoize(cache, lambda x: None)
        def identity(x: object) -> object:
            calls.append(x)
            return x

        identity(1)
        identity(1)
        assert len(calls) == 2
        assert len(cache) == 0

    def test_preserves_function_metadata(self) -> None:
        cache = TtlCache(maxsize=1, ttl_seconds=1)

        @ttl_memoize(cache, lambda: "k")
        def documented() -> int:
            """Docstring."""
            return 1

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "Docstring."
