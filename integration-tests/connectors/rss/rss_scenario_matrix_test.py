# pyright: ignore-file

"""RSS in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are entries of a feed of this run's own on the ``web-fixtures`` service,
each pointing at an article page there. The connector reads the feed and, with
full content on, indexes each entry's article page, so an item's text is its
article and its name is the entry's title.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import create_connector_and_await_sync, destructor

from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from helper.graph_provider import GraphProviderProtocol
from helper.web_fixtures import WebFixtures, html_page
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

HTML = "text/html; charset=utf-8"
RSS = "application/rss+xml; charset=utf-8"
PUB_DATE = "Mon, 01 Sep 2025 09:00:00 GMT"


class RSSAdapter(ScenarioAdapter):
    source = "RSS"

    def __init__(self, *, fixtures: WebFixtures, root: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fixtures = fixtures
        self.root = root
        self.feed_path = f"{root}feed.xml"
        # guid -> (title, article path), newest first, as a feed lists them.
        self.entries: dict[str, tuple[str, str]] = {}

    def publish_feed(self) -> None:
        items = "".join(
            f'<item><guid isPermaLink="false">{guid}</guid><title>{title}</title>'
            f"<link>{{{{BASE_URL}}}}/{path}</link>"
            f"<description>Summary of {title}.</description><pubDate>{PUB_DATE}</pubDate></item>"
            for guid, (title, path) in self.entries.items()
        )
        self.fixtures.put(
            self.feed_path,
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            f"<title>Scenario matrix feed</title><link>{{{{BASE_URL}}}}/{self.root}</link>"
            f"<description>Scenario matrix entries.</description>{items}</channel></rss>",
            RSS,
        )

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        guid = f"urn:mx:{token}"
        title = f"{role.value}-{token}"
        path = f"{self.root}{role.value}-{token}.html"
        self.fixtures.put(path, html_page(title, text), HTML)
        self.entries = {guid: (title, path), **self.entries}
        self.publish_feed()
        return SourceItem(role=role, key=path, record_name=title, text=text, token=token,
                          external_id=guid)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.fixtures.put(item.key, html_page(item.record_name, text), HTML)
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name,
                          text=text, token=token, external_id=item.external_id)

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        title = f"renamed-{item.token}"
        assert item.external_id
        self.entries[item.external_id] = (title, item.key)
        self.publish_feed()
        return SourceItem(role=item.role, key=item.key, record_name=title, text=item.text,
                          token=item.token, external_id=item.external_id)

    async def trigger_incremental_sync(self) -> None:
        # The resync API, as the RSS suite uses: the path a scheduled sync takes.
        self.client.resync_connector(self.connector_id, full_sync=False)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    rss_fixtures: WebFixtures,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[RSSAdapter, None]:
    rss_fixtures.reset()
    root = f"feeds/mx-{uuid.uuid4().hex[:8]}/"
    adapter = RSSAdapter(
        fixtures=rss_fixtures, root=root, client=pipeshub_client, graph=graph_provider,
        connector_id="",
    )
    await adapter.create_item(Role.KEEP, "Scenario matrix seed article.", "seed")
    feed_url = rss_fixtures.url_for_connector(adapter.feed_path)
    state: dict[str, Any] = {"resource_name": feed_url}
    try:
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="RSS",
            connector_name=f"rss-matrix-{uuid.uuid4().hex[:8]}",
            connector_config={
                "sync": {
                    "feed_urls": feed_url,
                    # Above what a run adds, so no item rolls off the limit.
                    "max_articles_per_feed": 50,
                    "fetch_full_content": True,
                }
            },
            expected_records=1,
            scope="team",
        )
        adapter.connector_id = state["connector_id"]
        yield adapter
    finally:
        if "connector_id" in state:
            await destructor(
                rss_fixtures, pipeshub_client, graph_provider, state, connector_type="RSS"
            )
        rss_fixtures.reset()


@pytest.mark.integration
@pytest.mark.rss
class TestRSSScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "RSS"
    UNSUPPORTED = {
        Action.DELETE.value: (
            "a feed has no deletions: entries roll off it, and the connector keeps an item "
            "that leaves the feed on purpose (the RSS suite's TC-FEED-001)"
        ),
        Action.CHANGE_PERMISSION.value: (
            "every item of a team feed is shared with the whole org (one ORG permission); "
            "a feed has no per-item access"
        ),
        Action.SET_FILTER.value: "the connector declares no sync filters",
        Action.SET_INDEXING.value: (
            "the connector declares no indexing filters (no enable_manual_sync), so indexing "
            "cannot be switched to manual"
        ),
    }
