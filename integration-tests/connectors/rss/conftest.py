# pyright: ignore-file

"""RSS connector fixtures.

The connector reads an RSS 2.0 feed and an Atom feed from the ``web-fixtures``
service of the integration stack (see ``helper/web_fixtures.py``), and fetches
each item's article page from it too.
"""

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import (
    create_connector_and_await_sync,
    destructor,
    source_unavailable,
)
from helper.graph_provider import GraphProviderProtocol
from helper.web_fixtures import WebFixtures
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

NEWS_FEED = "feeds/news.xml"
CHANGELOG_FEED = "feeds/changelog.atom"
MAX_ARTICLES_PER_FEED = 3


@dataclass(frozen=True)
class FeedItem:
    guid: str
    title: str
    article_path: str
    pub_date: str


# Mirrors web_fixtures/seed/feeds/news.xml, so tests can serve a changed feed.
NEWS_ITEMS = [
    FeedItem("fixture-news-1", "Fixture News: Quarterly Roadmap", "feeds/news/roadmap.html",
             "Mon, 01 Sep 2025 09:00:00 GMT"),
    FeedItem("fixture-news-2", "Fixture News: Office Move", "feeds/news/office-move.html",
             "Tue, 02 Sep 2025 09:00:00 GMT"),
    FeedItem("fixture-news-3", "Fixture News: Security Training", "feeds/news/security-training.html",
             "Wed, 03 Sep 2025 09:00:00 GMT"),
    FeedItem("fixture-news-4", "Fixture News: Archive Item Past The Limit", "feeds/news/past-limit.html",
             "Thu, 04 Sep 2025 09:00:00 GMT"),
]
CHANGELOG_GUIDS = ["urn:fixture:changelog:1", "urn:fixture:changelog:2"]

# The feed's first MAX_ARTICLES_PER_FEED items, plus every changelog entry.
EXPECTED_GUIDS = [item.guid for item in NEWS_ITEMS[:MAX_ARTICLES_PER_FEED]] + CHANGELOG_GUIDS
PAST_LIMIT_GUID = NEWS_ITEMS[MAX_ARTICLES_PER_FEED].guid


def news_feed(items: list[FeedItem]) -> str:
    """The news feed as RSS 2.0; ``{{BASE_URL}}`` is filled in by the fixture service."""
    entries = "".join(
        f"<item><guid isPermaLink=\"false\">{item.guid}</guid><title>{item.title}</title>"
        f"<link>{{{{BASE_URL}}}}/{item.article_path}</link>"
        f"<description>Summary for {item.title}.</description><pubDate>{item.pub_date}</pubDate></item>"
        for item in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>Fixture Company News</title><link>{{BASE_URL}}/feeds/</link>"
        f"<description>News items for the RSS connector integration tests.</description>{entries}"
        "</channel></rss>"
    )


@pytest.fixture(scope="session")
def rss_fixtures() -> WebFixtures:
    fixtures = WebFixtures()
    try:
        fixtures.check_available()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(f"web-fixtures service not reachable at {fixtures.test_url}: {exc}")
    return fixtures


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def rss_connector(
    rss_fixtures: WebFixtures,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    rss_fixtures.reset()
    feed_urls = [rss_fixtures.url_for_connector(NEWS_FEED), rss_fixtures.url_for_connector(CHANGELOG_FEED)]
    state: dict[str, Any] = {"resource_name": NEWS_FEED, "feed_urls": feed_urls}

    config = {
        "sync": {
            "feed_urls": "\n".join(feed_urls),
            "max_articles_per_feed": MAX_ARTICLES_PER_FEED,
            "fetch_full_content": True,
        }
    }
    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="RSS",
        connector_name=f"rss-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(EXPECTED_GUIDS),
        scope="team",
    )

    yield state

    await destructor(rss_fixtures, pipeshub_client, graph_provider, state, connector_type="RSS")
