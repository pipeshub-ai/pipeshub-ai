"""The strategy resolver's failure modes.

``ConfigurationService.get_config`` swallows store errors and returns its
``default``, so "the read came back empty" does not mean "nothing is
persisted". Every test here is a way that ambiguity could have overwritten a
live deployment's collection layout with the env default — which would leave
every read and delete resolving to a collection holding no data.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.strategy_resolver import (
    STRATEGY_CONFIG_KEY,
    StrategyConfigurationError,
    reset_strategy_cache,
    resolve_persisted_strategy_name,
    resolve_strategy,
)


@pytest.fixture(autouse=True)
def _clean_memo():
    reset_strategy_cache()
    yield
    reset_strategy_cache()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)


def _config_service(*, stored=None, read_raises=False, create_raises=False):
    """A ConfigurationService double over one key.

    ``read_raises`` reproduces the real service's behaviour rather than an
    exception: it swallows and returns the default, which is precisely why the
    resolver cannot trust an empty read.
    """
    state = {"value": stored}

    async def get_config(key, default=None):
        if read_raises:
            return default  # what the real service does on a store error
        return state["value"] if state["value"] is not None else default

    async def create_config_if_absent(key, value):
        if create_raises:
            raise ConnectionError("kv unreachable")
        if state["value"] is not None:
            return False
        state["value"] = value
        return True

    svc = MagicMock()
    svc.get_config = AsyncMock(side_effect=get_config)
    svc.set_config = AsyncMock()
    svc.create_config_if_absent = AsyncMock(side_effect=create_config_if_absent)
    svc._state = state
    return svc


class TestTransientReadFailure:
    @pytest.mark.asyncio
    async def test_unreadable_store_never_overwrites_a_persisted_strategy(self):
        """The bug this design exists to prevent: a KV blip at startup making a
        `per_org` deployment look brand new and persisting `single` over it."""
        svc = _config_service(stored="per_org_ee", read_raises=True)

        with pytest.raises(StrategyConfigurationError, match="could not be read back"):
            await resolve_persisted_strategy_name(svc, MagicMock())

        assert svc._state["value"] == "per_org_ee"

    @pytest.mark.asyncio
    async def test_never_calls_the_overwriting_setter(self):
        """set_config would clobber; only create-if-absent may be used here."""
        svc = _config_service(stored="per_org_ee", read_raises=True)

        with pytest.raises(StrategyConfigurationError):
            await resolve_persisted_strategy_name(svc, MagicMock())

        svc.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_read_recovering_after_a_failed_create_returns_the_stored_value(self):
        """First read blipped, create found the key owned, read-back succeeded."""
        state = {"value": "per_org_ee"}
        reads = {"n": 0}

        async def get_config(key, default=None):
            reads["n"] += 1
            if reads["n"] == 1:
                return default  # the blip
            return state["value"]

        async def create_config_if_absent(key, value):
            return False  # already owned

        svc = MagicMock()
        svc.get_config = AsyncMock(side_effect=get_config)
        svc.create_config_if_absent = AsyncMock(side_effect=create_config_if_absent)

        assert await resolve_persisted_strategy_name(svc, MagicMock()) == "per_org_ee"

    @pytest.mark.asyncio
    async def test_unwritable_store_fails_loudly(self):
        svc = _config_service(create_raises=True)

        with pytest.raises(StrategyConfigurationError, match="Could not persist"):
            await resolve_persisted_strategy_name(svc, MagicMock())


class TestFirstStartupAndRaces:
    @pytest.mark.asyncio
    async def test_absent_key_persists_the_default(self):
        svc = _config_service()

        assert await resolve_persisted_strategy_name(svc, MagicMock()) == "single"
        assert svc._state["value"] == "single"

    @pytest.mark.asyncio
    async def test_env_var_is_persisted_on_first_startup(self, monkeypatch):
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "single")
        svc = _config_service()

        assert await resolve_persisted_strategy_name(svc, MagicMock()) == "single"
        svc.create_config_if_absent.assert_awaited_once_with(
            STRATEGY_CONFIG_KEY, "single"
        )

    @pytest.mark.asyncio
    async def test_loser_of_a_startup_race_adopts_the_winners_value(self):
        """Two services starting together: one creates, the other reads back."""
        # The key becomes owned between one resolver's read and its create.
        state: dict = {"value": None}

        async def get_config(key, default=None):
            return state["value"] if state["value"] is not None else default

        async def create_config_if_absent(key, value):
            if state["value"] is not None:
                return False
            state["value"] = value
            return True

        a = MagicMock()
        a.get_config = AsyncMock(side_effect=get_config)
        a.create_config_if_absent = AsyncMock(side_effect=create_config_if_absent)
        b = MagicMock()
        b.get_config = AsyncMock(side_effect=get_config)
        b.create_config_if_absent = AsyncMock(side_effect=create_config_if_absent)

        names = await asyncio.gather(
            resolve_persisted_strategy_name(a, MagicMock()),
            resolve_persisted_strategy_name(b, MagicMock()),
        )

        assert names == ["single", "single"]
        assert state["value"] == "single"

    @pytest.mark.asyncio
    async def test_unregistered_env_name_fails_before_persisting(self, monkeypatch):
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "not_a_real_strategy")
        svc = _config_service()

        with pytest.raises(StrategyConfigurationError, match="Unknown collection"):
            await resolve_persisted_strategy_name(svc, MagicMock())

        svc.create_config_if_absent.assert_not_awaited()
        assert svc._state["value"] is None


class TestEnvContradiction:
    @pytest.mark.asyncio
    async def test_env_contradicting_the_persisted_value_fails_fast(self, monkeypatch):
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "something_else")
        svc = _config_service(stored="single")

        with pytest.raises(StrategyConfigurationError, match="contradicts"):
            await resolve_persisted_strategy_name(svc, MagicMock())

    @pytest.mark.asyncio
    async def test_contradiction_is_also_caught_on_the_read_back_path(self, monkeypatch):
        """The value discovered *after* a failed create must be checked too, or
        the race-loser path becomes a way around the fail-fast."""
        state: dict = {"value": None}

        async def get_config(key, default=None):
            return state["value"] if state["value"] is not None else default

        async def create_config_if_absent(key, value):
            state["value"] = "single"  # someone else got there first
            return False

        svc = MagicMock()
        svc.get_config = AsyncMock(side_effect=get_config)
        svc.create_config_if_absent = AsyncMock(side_effect=create_config_if_absent)

        # Registered name: env is validated before create, so an unknown
        # strategy would fail earlier and miss this path.
        monkeypatch.setenv("VECTOR_COLLECTION_STRATEGY", "per_connector_type")

        with pytest.raises(StrategyConfigurationError, match="contradicts"):
            await resolve_persisted_strategy_name(svc, MagicMock())


class TestCrossEventLoopResolution:
    def test_resolves_on_two_different_event_loops(self):
        """Indexing runs work on the record consumer's loop and on the main
        loop. A module-level asyncio.Lock binds to whichever awaited it first
        and raises on the other, so resolution must not use one."""
        svc = _config_service()

        first = asyncio.run(resolve_strategy(svc, MagicMock()))
        reset_strategy_cache()
        second = asyncio.run(resolve_strategy(svc, MagicMock()))

        assert first.strategy_name() == second.strategy_name() == "single"

    def test_memo_survives_across_loops_once_resolved(self):
        svc = _config_service()
        resolved = asyncio.run(resolve_strategy(svc, MagicMock()))
        again = asyncio.run(resolve_strategy(svc, MagicMock()))
        assert again is resolved
