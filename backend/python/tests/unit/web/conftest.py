"""Web tests never touch the network. ``fake_web`` serves in-memory sites through the real
fetch chain, using the web connector's behaviour-test fakes."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "connectors" / "sources" / "web" / "behaviour"))

from web_behaviour_fakes import (
    FakeWeb,
    isolate_network,
    serve_fake_browser,
    serve_fake_web,
)

from app.connectors.sources.web import address_guard
from app.web.fetch.browser import BrowserRenderer


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_network(monkeypatch)


@pytest.fixture
async def fake_web(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeWeb]:
    address_guard._allowed_hosts.cache_clear()
    async with serve_fake_web(FakeWeb(), monkeypatch) as site:
        yield site
    address_guard._allowed_hosts.cache_clear()


@pytest.fixture
async def fake_browser(fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeWeb]:
    """The headless browser renders from ``fake_web``; chat's reference to it starts empty."""
    monkeypatch.setattr(BrowserRenderer, "_fetcher", None)
    async with serve_fake_browser(fake_web, monkeypatch) as site:
        yield site
