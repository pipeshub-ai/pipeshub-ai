"""The ``lab`` fixture: see ``lab.py``."""

from collections.abc import AsyncIterator

import pytest
from web_behaviour_fakes import FakeWeb

from app.web import container
from app.web.chain import Cooldowns
from app.web.search.ddgs_search import DdgsSearch
from tests.unit.web.qa.lab import Engines, WebLab


@pytest.fixture
async def lab(fake_browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WebLab]:
    engines = Engines()
    monkeypatch.setattr("app.web.container.shared_http_client", engines.client)
    monkeypatch.setattr("app.web.endpoint.guarded_async_client", engines.client)
    monkeypatch.setattr(DdgsSearch, "_text", staticmethod(engines.ddgs_text))
    monkeypatch.setattr("app.web.search.duckduckgo_html.fetch_public", engines.duckduckgo_page)
    # Each test starts with no source cooling down.
    monkeypatch.setattr(container, "COOLDOWNS", Cooldowns())
    yield WebLab(fake_browser, engines)
