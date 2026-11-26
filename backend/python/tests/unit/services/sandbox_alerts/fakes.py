"""Test doubles for the alert loop: a configuration store with TTLs and a producer."""

from __future__ import annotations


class FakeConfig:
    """``create_config_if_absent`` / ``get_config`` / ``set_config`` /
    ``delete_config`` over a dict, with expiry on a settable clock."""

    def __init__(self, clock=lambda: 0.0) -> None:
        self.clock = clock
        self.values: dict[str, object] = {}
        self.expires: dict[str, float | None] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = False

    def _live(self, key: str) -> bool:
        expires = self.expires.get(key)
        if key in self.values and expires is not None and self.clock() >= expires:
            del self.values[key]
        return key in self.values

    async def create_config_if_absent(self, key: str, value: object, *, ttl_seconds: int | None = None) -> bool:
        if self.fail:
            raise ConnectionError("store down")
        if self._live(key):
            return False
        self._put(key, value, ttl_seconds)
        return True

    async def set_config(self, key: str, value: object, *, ttl_seconds: int | None = None) -> bool:
        if self.fail:
            return False
        self._put(key, value, ttl_seconds)
        return True

    def _put(self, key: str, value: object, ttl_seconds: int | None) -> None:
        self.values[key] = value
        self.ttls[key] = ttl_seconds
        self.expires[key] = None if ttl_seconds is None else self.clock() + ttl_seconds

    async def get_config(self, key: str, use_cache: bool = False, raise_on_error: bool = False) -> object:
        if self.fail:
            raise ConnectionError("store down")
        return self.values.get(key) if self._live(key) else None

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class FakeProducer:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.sent: list[tuple[str, dict, str | None]] = []
        self.cleaned = False

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        self.sent.append((topic, message, key))
        return self.result

    async def cleanup(self) -> None:
        self.cleaned = True
