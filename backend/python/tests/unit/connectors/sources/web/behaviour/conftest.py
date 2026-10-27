"""Fixtures for the Web connector behaviour tests (fakes live in web_behaviour_fakes)."""

import logging
import sys
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from web_behaviour_fakes import (
    CONNECTOR_ID,
    START_URL,
    FakeCheckpointStore,
    FakeConfigService,
    FakeRecordsDb,
    FakeRequestsClient,
    FakeScraper,
    FakeWeb,
    MakeConnector,
    RecordingNotifications,
    VirtualClock,
    isolate_network,
    serve_fake_browser,
    serve_fake_web,
)

from app.connectors.sources.web import connector as connector_module
from app.connectors.sources.web import fetch_strategy
from app.connectors.sources.web.connector import WebConnector


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_network(monkeypatch)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    virtual = VirtualClock()
    monkeypatch.setattr(connector_module, "asyncio", virtual)
    monkeypatch.setattr(fetch_strategy, "asyncio", virtual)
    return virtual


@pytest.fixture
async def site(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeWeb]:
    async with serve_fake_web(FakeWeb(), monkeypatch) as fake:
        yield fake


@pytest.fixture
async def browser(site: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeWeb]:
    """crawl4ai's browser renders from the fake websites; the shared fetcher starts fresh."""
    async with serve_fake_browser(site, monkeypatch) as fake:
        yield fake


@pytest.fixture
def use_strategy(site: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Serve page fetches with one of the fetcher's strategies (aiohttp is the default)."""
    def _use(name: str) -> None:
        if name == "curl_cffi":
            import curl_cffi.requests

            monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])
            monkeypatch.setattr(curl_cffi.requests, "Session", lambda **_: FakeRequestsClient(site, "curl_cffi"))
        elif name == "cloudscraper":
            fake = SimpleNamespace(create_scraper=lambda **_: FakeScraper(site, "cloudscraper"))
            monkeypatch.setitem(sys.modules, "cloudscraper", fake)
    return _use


@pytest.fixture
def db() -> FakeRecordsDb:
    return FakeRecordsDb()


@pytest.fixture
def checkpoints() -> FakeCheckpointStore:
    return FakeCheckpointStore()


@pytest.fixture
def notifications() -> RecordingNotifications:
    return RecordingNotifications()


@pytest.fixture
async def make_connector(
    browser: FakeWeb,
    clock: VirtualClock,
    db: FakeRecordsDb,
    checkpoints: FakeCheckpointStore,
    notifications: RecordingNotifications,
) -> AsyncIterator[MakeConnector]:
    built: list[WebConnector] = []

    async def _make(
        url: str = START_URL,
        *,
        crawl_type: str = "recursive",
        depth: int = 3,
        max_pages: int = 100,
        filters: dict[str, Any] | None = None,
        scope: str = "team",
        expect_init: bool = True,
        **sync: object,
    ) -> WebConnector:
        config = FakeConfigService(
            CONNECTOR_ID,
            # Browser-like unless a test says otherwise: most scenarios predate the setting.
            {"url": url, "type": crawl_type, "depth": depth, "max_pages": max_pages, "browser_like_identity": True, **sync},
            filters,
        )
        connector = WebConnector(
            logging.getLogger("web-behaviour"),
            db,  # type: ignore[arg-type]
            SimpleNamespace(transaction=checkpoints.transaction),  # type: ignore[arg-type]
            config,  # type: ignore[arg-type]
            CONNECTOR_ID,
            scope,
            "user-1",
        )
        connector._notification_service = notifications
        built.append(connector)
        assert await connector.init() is expect_init
        return connector

    yield _make
    for connector in built:
        await connector.cleanup()
