"""Fixtures for every connector source's tests."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def fresh_web_crawl_state() -> Iterator[None]:
    """The web connector's robots.txt cache and per-site pacing are process-wide: one test's site
    must not answer for, or slow down, the next one's."""
    from app.connectors.sources.web.politeness import shared_host_gate
    from app.connectors.sources.web.robots import shared_robots_cache

    shared_robots_cache.clear()
    shared_host_gate.reset()
    yield
    shared_robots_cache.clear()
    shared_host_gate.reset()
