# pyright: ignore-file

"""Web connector fixtures.

The connector crawls the ``web-fixtures`` service of the integration stack (see
``helper/web_fixtures.py``), so it needs no external site and its results do
not change when some public page does.
"""

import uuid
from collections.abc import AsyncGenerator
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

START_PATH = "site/docs/"
MAX_DEPTH = 2

# What a depth-2 crawl of START_PATH, restricted to that path, must index:
# record name -> path. Names come from each page's <title>; the text file has
# none, so its name comes from the file name.
EXPECTED_PAGES = {
    "Fixture Docs Home": "site/docs/",
    "Fixture Getting Started Guide": "site/docs/guide.html",
    "Fixture API Overview": "site/docs/api/",
    "Fixture API Reference": "site/docs/api/reference.html",
    "Release Notes": "site/docs/release-notes.txt",
}
# Linked from the site, but outside what the crawl may index.
EXCLUDED_PAGES = {
    "Fixture Outside Page": "above the start path",
    "Fixture API Deep Dive": "three links deep, past the depth limit",
}


@pytest.fixture(scope="session")
def web_fixtures() -> WebFixtures:
    fixtures = WebFixtures()
    try:
        fixtures.check_available()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(f"web-fixtures service not reachable at {fixtures.test_url}: {exc}")
    return fixtures


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def web_connector(
    web_fixtures: WebFixtures,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    web_fixtures.reset()
    start_url = web_fixtures.url_for_connector(START_PATH)
    state: dict[str, Any] = {"resource_name": start_url, "start_url": start_url}

    config = {
        "sync": {
            "url": start_url,
            "type": "recursive",
            "depth": MAX_DEPTH,
            "max_pages": 50,
            "restrict_to_start_path": True,
            "follow_external": False,
        }
    }
    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Web",
        connector_name=f"web-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(EXPECTED_PAGES),
        scope="team",
    )

    yield state

    await destructor(web_fixtures, pipeshub_client, graph_provider, state, connector_type="Web")
