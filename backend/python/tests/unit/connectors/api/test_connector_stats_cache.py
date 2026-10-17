"""Kept connector counts for callers that pass maxAgeSeconds on GET /api/v1/stats."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.connectors.api.connector_stats_cache import (
    MAX_AGE_SECONDS_LIMIT,
    ConnectorStatsCache,
)
from app.connectors.api.router import get_connector_stats_endpoint

STATS = {
    "orgId": "o1",
    "connectorId": "c1",
    "origin": "CONNECTOR",
    "stats": {"total": 10, "indexingStatus": {"COMPLETED": 7, "QUEUED": 3}},
    "byRecordType": [],
}


class FakeClock:
    """Monotonic and wall clocks that only move when told to."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.wall = 1_790_000_000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.wall += seconds


def _cache(clock: FakeClock, max_entries: int = 1024) -> ConnectorStatsCache:
    return ConnectorStatsCache(max_entries=max_entries, clock=clock.monotonic, wall_clock=clock.time)


class TestConnectorStatsCache:
    def test_nothing_is_kept_until_counts_are_put(self) -> None:
        assert _cache(FakeClock()).get("o1", "c1", 60) is None

    def test_put_returns_the_counts_stamped_as_fresh(self) -> None:
        clock = FakeClock()
        data = _cache(clock).put("o1", "c1", STATS)

        assert data["stats"] == STATS["stats"]
        assert data["countedAt"] == 1_790_000_000_000
        assert data["ageSeconds"] == 0
        assert "countedAt" not in STATS  # the provider's dict is not changed

    def test_kept_counts_say_how_old_they_are(self) -> None:
        clock = FakeClock()
        cache = _cache(clock)
        cache.put("o1", "c1", STATS)
        clock.advance(42.7)

        data = cache.get("o1", "c1", 60)

        assert data is not None
        assert data["countedAt"] == 1_790_000_000_000
        assert data["ageSeconds"] == 42
        assert data["stats"] == STATS["stats"]

    def test_counts_older_than_the_caller_accepts_are_not_returned(self) -> None:
        clock = FakeClock()
        cache = _cache(clock)
        cache.put("o1", "c1", STATS)
        clock.advance(61)

        assert cache.get("o1", "c1", 60) is None
        assert cache.get("o1", "c1", 120) is not None

    def test_entries_are_per_org_and_per_connector(self) -> None:
        cache = _cache(FakeClock())
        cache.put("o1", "c1", STATS)

        assert cache.get("o2", "c1", 60) is None
        assert cache.get("o1", "c2", 60) is None

    def test_the_oldest_entry_goes_when_the_cache_is_full(self) -> None:
        cache = _cache(FakeClock(), max_entries=2)
        for connector in ("c1", "c2", "c3"):
            cache.put("o1", connector, STATS)

        assert len(cache) == 2
        assert cache.get("o1", "c1", 60) is None
        assert cache.get("o1", "c3", 60) is not None

    def test_the_limit_on_accepted_age_is_ten_minutes(self) -> None:
        assert MAX_AGE_SECONDS_LIMIT == 600


def _request() -> MagicMock:
    request = MagicMock()
    request.app.container.logger.return_value = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1"}
    return request


class TestStatsEndpointWithMaxAge:
    @pytest.fixture
    def clock(self) -> FakeClock:
        return FakeClock()

    @pytest.fixture(autouse=True)
    def cache(self, clock: FakeClock) -> Iterator[ConnectorStatsCache]:
        cache = _cache(clock)
        with patch("app.connectors.api.router.connector_stats_cache", cache):
            yield cache

    @pytest.fixture(autouse=True)
    def authorize(self) -> Iterator[AsyncMock]:
        with patch("app.connectors.api.router.authorize_connector_stats", new=AsyncMock()) as auth:
            yield auth

    @pytest.fixture
    def provider(self) -> MagicMock:
        provider = MagicMock()
        provider.get_connector_stats = AsyncMock(return_value={"success": True, "data": dict(STATS)})
        return provider

    async def _stats(self, provider: MagicMock, max_age_seconds: int | None = None, connector_id: str = "c1") -> dict:
        return await get_connector_stats_endpoint(
            _request(), connector_id=connector_id, graph_provider=provider, max_age_seconds=max_age_seconds
        )

    @pytest.mark.asyncio
    async def test_without_max_age_every_call_counts_again(self, provider: MagicMock) -> None:
        first = await self._stats(provider)
        second = await self._stats(provider)

        assert provider.get_connector_stats.await_count == 2
        assert first["success"] is True
        assert first["data"]["stats"] == STATS["stats"]
        assert first["data"]["ageSeconds"] == 0 and second["data"]["ageSeconds"] == 0

    @pytest.mark.asyncio
    async def test_max_age_zero_counts_again(self, provider: MagicMock) -> None:
        await self._stats(provider)
        await self._stats(provider, max_age_seconds=0)

        assert provider.get_connector_stats.await_count == 2

    @pytest.mark.asyncio
    async def test_with_max_age_recent_counts_are_reused_and_dated(self, provider: MagicMock, clock: FakeClock) -> None:
        first = await self._stats(provider, max_age_seconds=300)
        clock.advance(90)
        second = await self._stats(provider, max_age_seconds=300)

        assert provider.get_connector_stats.await_count == 1
        assert second["data"]["stats"] == STATS["stats"]
        assert second["data"]["countedAt"] == first["data"]["countedAt"]
        assert second["data"]["ageSeconds"] == 90

    @pytest.mark.asyncio
    async def test_counts_older_than_max_age_are_taken_again(self, provider: MagicMock, clock: FakeClock) -> None:
        await self._stats(provider, max_age_seconds=60)
        clock.advance(61)
        again = await self._stats(provider, max_age_seconds=60)

        assert provider.get_connector_stats.await_count == 2
        assert again["data"]["ageSeconds"] == 0

    @pytest.mark.asyncio
    async def test_fresh_counts_serve_a_later_caller_who_accepts_older(self, provider: MagicMock, clock: FakeClock) -> None:
        await self._stats(provider)
        clock.advance(5)
        reused = await self._stats(provider, max_age_seconds=60)

        assert provider.get_connector_stats.await_count == 1
        assert reused["data"]["ageSeconds"] == 5

    @pytest.mark.asyncio
    async def test_access_is_checked_even_when_counts_are_kept(self, provider: MagicMock, authorize: AsyncMock) -> None:
        await self._stats(provider, max_age_seconds=300)
        authorize.side_effect = HTTPException(status_code=404, detail="This connector was not found.")

        with pytest.raises(HTTPException) as exc:
            await self._stats(provider, max_age_seconds=300)

        assert exc.value.status_code == 404
        assert authorize.await_count == 2

    @pytest.mark.asyncio
    async def test_a_failed_count_is_not_kept(self, provider: MagicMock) -> None:
        provider.get_connector_stats = AsyncMock(return_value={"success": False, "data": None})
        with pytest.raises(HTTPException) as exc:
            await self._stats(provider, max_age_seconds=300)
        assert exc.value.status_code == 404

        provider.get_connector_stats = AsyncMock(return_value={"success": True, "data": dict(STATS)})
        ok = await self._stats(provider, max_age_seconds=300)

        assert ok["success"] is True
        provider.get_connector_stats.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_another_connector_is_counted_on_its_own(self, provider: MagicMock) -> None:
        await self._stats(provider, max_age_seconds=300, connector_id="c1")
        await self._stats(provider, max_age_seconds=300, connector_id="c2")

        assert [c.args for c in provider.get_connector_stats.await_args_list] == [("o1", "c1"), ("o1", "c2")]


class TestMaxAgeQueryParameter:
    """The query parameter as an HTTP caller sends it."""

    @pytest.fixture
    def client(self) -> Iterator[tuple[TestClient, MagicMock]]:
        from fastapi import FastAPI, Request

        from app.connectors.api.router import get_graph_provider

        provider = MagicMock()
        provider.get_connector_stats = AsyncMock(return_value={"success": True, "data": dict(STATS)})
        app = FastAPI()
        app.container = MagicMock()
        app.state.connector_registry = MagicMock()

        @app.middleware("http")
        async def user(request: Request, call_next):  # noqa: ANN202
            request.state.user = {"userId": "u1", "orgId": "o1"}
            return await call_next(request)

        app.add_api_route("/api/v1/stats", get_connector_stats_endpoint, methods=["GET"])
        app.dependency_overrides[get_graph_provider] = lambda: provider
        with patch("app.connectors.api.router.authorize_connector_stats", new=AsyncMock()), patch(
            "app.connectors.api.router.connector_stats_cache", ConnectorStatsCache()
        ):
            yield TestClient(app), provider

    def test_max_age_seconds_is_read_from_the_query_string(self, client: tuple[TestClient, MagicMock]) -> None:
        http, provider = client
        first = http.get("/api/v1/stats", params={"connector_id": "c1", "maxAgeSeconds": 300})
        second = http.get("/api/v1/stats", params={"connector_id": "c1", "maxAgeSeconds": 300})

        assert first.status_code == 200 and second.status_code == 200
        assert provider.get_connector_stats.await_count == 1
        assert set(second.json()["data"]) >= {"stats", "countedAt", "ageSeconds"}

    @pytest.mark.parametrize("value", ["-1", "601", "soon"])
    def test_an_out_of_range_max_age_is_refused(self, client: tuple[TestClient, MagicMock], value: str) -> None:
        http, provider = client
        response = http.get("/api/v1/stats", params={"connector_id": "c1", "maxAgeSeconds": value})

        assert response.status_code == 422
        provider.get_connector_stats.assert_not_called()
