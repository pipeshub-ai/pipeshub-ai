"""Unit tests for app.services.vector_db.strategy_resolver."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy_resolver import (
    STRATEGY_CONFIG_KEY,
    StrategyConfigurationError,
    reset_strategy_cache,
    resolve_persisted_strategy_name,
    resolve_strategy,
)


@pytest.fixture(autouse=True)
def _clear_strategy_memo():
    """resolve_strategy memoises per process; tests must not inherit it."""
    reset_strategy_cache()
    yield
    reset_strategy_cache()


def _make_config_service(persisted=None, *, create_succeeds=True):
    """A ConfigurationService double over one key.

    ``create_succeeds=False`` models the case the resolver exists to survive:
    the read said nothing, but the key is in fact owned — a store hiccup, or
    another service that won the race.
    """
    state = {"value": persisted}

    async def get_config(key, default=None):
        return state["value"] if state["value"] is not None else default

    async def create_config_if_absent(key, value):
        if state["value"] is not None or not create_succeeds:
            return False
        state["value"] = value
        return True

    svc = MagicMock()
    svc.get_config = AsyncMock(side_effect=get_config)
    svc.set_config = AsyncMock()
    svc.create_config_if_absent = AsyncMock(side_effect=create_config_if_absent)
    svc._state = state
    return svc


class TestResolvePersistedStrategyName:
    @pytest.mark.asyncio
    async def test_persisted_key_present_is_used_as_is(self, monkeypatch):
        monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)
        config_service = _make_config_service(persisted="single")

        name = await resolve_persisted_strategy_name(config_service, MagicMock())

        assert name == "single"
        config_service.create_config_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persisted_present_and_env_var_agrees(self, monkeypatch):
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "single")
        config_service = _make_config_service(persisted="single")

        name = await resolve_persisted_strategy_name(config_service, MagicMock())

        assert name == "single"

    @pytest.mark.asyncio
    async def test_persisted_present_and_env_var_contradicts_fails_fast(self, monkeypatch):
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "per_org")
        config_service = _make_config_service(persisted="single")

        with pytest.raises(StrategyConfigurationError, match="contradicts"):
            await resolve_persisted_strategy_name(config_service, MagicMock())

    @pytest.mark.asyncio
    async def test_absent_persists_env_var_default_and_returns_it(self, monkeypatch):
        monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)
        config_service = _make_config_service(persisted=None)

        name = await resolve_persisted_strategy_name(config_service, MagicMock())

        assert name == "single"
        config_service.create_config_if_absent.assert_awaited_once_with(STRATEGY_CONFIG_KEY, "single")

    @pytest.mark.asyncio
    async def test_absent_with_env_var_set_persists_that_name(self, monkeypatch):
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "single")
        config_service = _make_config_service(persisted=None)

        name = await resolve_persisted_strategy_name(config_service, MagicMock())

        assert name == "single"
        config_service.create_config_if_absent.assert_awaited_once_with(STRATEGY_CONFIG_KEY, "single")

    @pytest.mark.asyncio
    async def test_absent_with_unknown_env_var_raises_listing_registered_names(self, monkeypatch):
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "totally_bogus")
        config_service = _make_config_service(persisted=None)

        with pytest.raises(StrategyConfigurationError, match="totally_bogus"):
            await resolve_persisted_strategy_name(config_service, MagicMock())
        config_service.create_config_if_absent.assert_not_awaited()


class TestResolveStrategy:
    @pytest.mark.asyncio
    async def test_returns_instantiated_strategy(self, monkeypatch):
        monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)
        config_service = _make_config_service(persisted="single")

        strategy = await resolve_strategy(config_service, MagicMock())

        assert isinstance(strategy, SingleCollectionStrategy)

    @pytest.mark.asyncio
    async def test_is_memoised_across_calls(self, monkeypatch):
        """The strategy cannot change without the rebuild procedure, so ad-hoc
        callers must not re-read the KV store on every use."""
        monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)
        config_service = _make_config_service(persisted="single")

        first = await resolve_strategy(config_service, MagicMock())
        second = await resolve_strategy(config_service, MagicMock())

        assert first is second
        config_service.get_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_first_calls_resolve_once(self, monkeypatch):
        import asyncio

        monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)
        config_service = _make_config_service(persisted=None)

        results = await asyncio.gather(
            *[resolve_strategy(config_service, MagicMock()) for _ in range(5)]
        )

        assert len({id(r) for r in results}) == 1
        # One resolution, so exactly one persist — not five racing writes.
        config_service.create_config_if_absent.assert_awaited_once_with(STRATEGY_CONFIG_KEY, "single")

    @pytest.mark.asyncio
    async def test_reset_forces_a_fresh_resolution(self, monkeypatch):
        monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)
        config_service = _make_config_service(persisted="single")

        await resolve_strategy(config_service, MagicMock())
        reset_strategy_cache()
        await resolve_strategy(config_service, MagicMock())

        assert config_service.get_config.await_count == 2
