# pyright: ignore-file

"""Web (crawler) in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are pages under a start path of this run's own on the ``web-fixtures``
service, each linked from that path's index page so a depth-1 crawl finds it.
Pages are written, changed and removed through the service's control API.

A removed page stays linked: the crawler deletes a stored page only once it
answers 404 on two syncs in a row, and it only asks for pages it is linked to.
"""

from __future__ import annotations

import posixpath
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import create_connector_and_await_sync, destructor

from connectors.scenario_matrix import (
    FILTER_KEEPS_EXCLUDED_ITEM,
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
TEXT = "text/plain; charset=utf-8"
# The exclusion filter drops plain-text pages; every other item is HTML.
FILTERED_EXTENSION = "txt"


def title_from_file_name(path: str) -> str:
    """The name the crawler gives a page with no <title>, as WebConnector._extract_title_from_url does."""
    stem = posixpath.basename(path).rsplit(".", 1)[0]
    return stem.replace("-", " ").replace("_", " ").title()


class WebAdapter(ScenarioAdapter):
    source = "Web"

    def __init__(self, *, fixtures: WebFixtures, root: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fixtures = fixtures
        self.root = root
        self.links: list[str] = []

    def publish_index(self) -> None:
        links = [(posixpath.basename(path), posixpath.basename(path)) for path in self.links]
        self.fixtures.put(
            f"{self.root}index.html",
            html_page("Scenario matrix index", "Pages of the scenario matrix run.", links),
            HTML,
        )

    def _item(self, role: Role, path: str, name: str, text: str, token: str) -> SourceItem:
        return SourceItem(
            role=role, key=path, record_name=name, text=text, token=token,
            external_id=self.fixtures.url_for_connector(path),
        )

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        if role is Role.FILTERED:
            path = f"{self.root}{role.value}-{token}.{FILTERED_EXTENSION}"
            self.fixtures.put(path, text, TEXT)
            name = title_from_file_name(path)
        else:
            path = f"{self.root}{role.value}-{token}.html"
            name = f"{role.value}-{token}"
            self.fixtures.put(path, html_page(name, text), HTML)
        self.links.append(path)
        self.publish_index()
        return self._item(role, path, name, text, token)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.fixtures.put(item.key, html_page(item.record_name, text), HTML)
        return self._item(item.role, item.key, item.record_name, text, token)

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        name = f"renamed-{item.token}"
        self.fixtures.put(item.key, html_page(name, item.text), HTML)
        return self._item(item.role, item.key, name, item.text, item.token)

    async def delete_item(self, item: SourceItem) -> None:
        self.fixtures.delete(item.key)

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        assert excluded.key.endswith(f".{FILTERED_EXTENSION}")
        assert not any(k.key.endswith(f".{FILTERED_EXTENSION}") for k in kept)
        return {
            "sync": {
                "values": {
                    "file_extensions": {
                        "operator": "not_in",
                        "value": [FILTERED_EXTENSION],
                        "type": "multiselect",
                    }
                }
            }
        }

    async def trigger_incremental_sync(self) -> None:
        # The resync API, as the Web suite uses: the path a scheduled sync takes,
        # on the connector instance that did the first crawl.
        self.client.resync_connector(self.connector_id, full_sync=False)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    web_fixtures: WebFixtures,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[WebAdapter, None]:
    web_fixtures.reset()
    root = f"site/mx-{uuid.uuid4().hex[:8]}/"
    start_url = web_fixtures.url_for_connector(root)
    state: dict[str, Any] = {"resource_name": start_url}
    adapter = WebAdapter(
        fixtures=web_fixtures, root=root, client=pipeshub_client, graph=graph_provider,
        connector_id="",
    )
    adapter.publish_index()
    try:
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="Web",
            connector_name=f"web-matrix-{uuid.uuid4().hex[:8]}",
            connector_config={
                "sync": {
                    "url": start_url,
                    "type": "recursive",
                    "depth": 1,
                    "max_pages": 50,
                    "restrict_to_start_path": True,
                    "follow_external": False,
                }
            },
            # The index page itself.
            expected_records=1,
            scope="team",
        )
        adapter.connector_id = state["connector_id"]
        yield adapter
    finally:
        if "connector_id" in state:
            await destructor(
                web_fixtures, pipeshub_client, graph_provider, state, connector_type="Web"
            )
        web_fixtures.reset()


@pytest.mark.integration
@pytest.mark.web
class TestWebScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Web"
    KNOWN_BUGS = {"filter_change": FILTER_KEEPS_EXCLUDED_ITEM}
    UNSUPPORTED = {
        Action.CHANGE_PERMISSION.value: (
            "a crawled site has no per-page access: every page of a team crawl is shared "
            "with the whole org (one ORG permission)"
        ),
    }
