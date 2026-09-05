# pyright: ignore-file

"""RSS connector fixtures.

The feed is served by an nginx container in the integration stack, so this
connector is covered without depending on a feed on the public internet.

Like Local FS, its settings live under a ``sync`` key rather than ``auth``: a
feed URL is a sync setting, not a credential.
"""

import os
import uuid
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import create_connector_and_await_sync, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.rss.rss_source_helper import BASE_ARTICLES, RssSourceHelper

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_HOST_DIR = "../deployment/docker-compose/rss-test-data"
DEFAULT_FEED_URL = "http://rss-source/feed.xml"



def _unavailable(reason: str) -> None:
    """A source that is not reachable: skip locally, fail in CI.

    Skipping on any exception turns a broken stack into a green run. CI brings
    this source up itself, so if it is not reachable there, that is a result and
    not a reason to report success.
    """
    if os.getenv("CI"):
        pytest.fail(reason)
    pytest.skip(reason)

@pytest.fixture(scope="session")
def rss_source() -> RssSourceHelper:
    helper = RssSourceHelper(os.getenv("RSS_TEST_HOST_DIR", DEFAULT_HOST_DIR))
    # Skip rather than fail when the directory is not usable, so a partial local
    # stack stays usable. In CI the mount is always present.
    try:
        helper.ensure_root()
    except OSError as exc:
        _unavailable(f"RSS feed directory not writable at {helper.root}: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def rss_connector(
    rss_source: RssSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    written = rss_source.write_feed(BASE_ARTICLES)

    state: Dict[str, Any] = {
        "resource_name": str(rss_source.feed_path),
        "seeded_titles": [title for title, _ in BASE_ARTICLES],
        "uploaded_count": written,
    }

    # A feed URL is a sync setting, not a credential, so it goes under "sync".
    # The URL is the one the *connector container* resolves.
    config = {
        "sync": {
            "feed_urls": os.getenv("RSS_CONNECTOR_FEED_URL", DEFAULT_FEED_URL),
            "max_articles_per_feed": 50,
            "fetch_full_content": False,
        }
    }

    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="RSS",
        connector_name=f"rss-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=written,
    )

    yield state

    await destructor(
        rss_source, pipeshub_client, graph_provider, state, connector_type="RSS"
    )
