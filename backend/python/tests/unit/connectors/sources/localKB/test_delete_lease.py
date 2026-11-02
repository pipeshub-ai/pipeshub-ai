"""The per-KB delete lease is only a hint (R6)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.localKB.handlers.delete_lease import (
    LEASE_TTL_SECONDS,
    hold_delete_lease,
    lease_is_held,
    lease_key,
)


class Config:
    def __init__(self) -> None:
        self.kv: dict[str, object] = {}
        self.ttls: list[int | None] = []

    async def create_config_if_absent(self, key, value, *, ttl_seconds=None) -> bool:
        self.ttls.append(ttl_seconds)
        return self.kv.setdefault(key, value) is value

    async def set_config(self, key, value, *, ttl_seconds=None) -> bool:
        self.ttls.append(ttl_seconds)
        self.kv[key] = value
        return True

    async def get_config(self, key, default=None, use_cache=True, raise_on_error=False) -> object:
        return self.kv.get(key, default)

    async def delete_config(self, key) -> bool:
        return self.kv.pop(key, None) is not None


@pytest.mark.asyncio
async def test_the_lease_is_held_while_the_delete_runs_and_gone_after() -> None:
    config = Config()
    async with hold_delete_lease(config, "kb1", "owner", MagicMock()):
        assert await lease_is_held(config, "kb1") is True
        assert config.ttls == [LEASE_TTL_SECONDS]
    assert await lease_is_held(config, "kb1") is False


@pytest.mark.asyncio
async def test_the_lease_is_removed_when_the_delete_fails() -> None:
    config = Config()
    with pytest.raises(RuntimeError):
        async with hold_delete_lease(config, "kb1", "owner", MagicMock()):
            raise RuntimeError("cascade failed")
    assert lease_key("kb1") not in config.kv


@pytest.mark.asyncio
async def test_the_lease_is_renewed_while_the_delete_runs() -> None:
    config = Config()
    async with hold_delete_lease(config, "kb1", "owner", MagicMock(), renew_seconds=0.01):
        await asyncio.sleep(0.08)
    assert len(config.ttls) >= 4


@pytest.mark.asyncio
async def test_an_existing_lease_does_not_stop_the_delete() -> None:
    """Correctness never depends on the lease: a stale holder's key is overwritten, not waited for."""
    config = Config()
    config.kv[lease_key("kb1")] = {"owner": "stale"}
    ran = False
    async with hold_delete_lease(config, "kb1", "owner", MagicMock()):
        ran = True
        assert config.kv[lease_key("kb1")]["owner"] == "owner"
    assert ran


@pytest.mark.asyncio
async def test_a_store_that_fails_never_fails_the_delete() -> None:
    config = AsyncMock()
    config.create_config_if_absent.side_effect = RuntimeError("kv down")
    config.delete_config.side_effect = RuntimeError("kv down")
    async with hold_delete_lease(config, "kb1", "owner", MagicMock()):
        pass


@pytest.mark.asyncio
async def test_an_unreadable_lease_counts_as_held() -> None:
    config = AsyncMock()
    config.get_config.side_effect = RuntimeError("kv down")
    assert await lease_is_held(config, "kb1") is True
