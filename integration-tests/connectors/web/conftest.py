# pyright: ignore-file

"""Web connector fixtures.

The connector crawls a small static site served by an nginx container in the
integration stack, so it is covered without depending on an external website.

Like Local FS and RSS, its settings live under a ``sync`` key rather than
``auth``: a start URL and crawl depth are sync settings, not credentials.
"""

import os
import uuid
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import create_connector_and_await_sync, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.web.web_source_helper import (
    DEPTH_0_TITLES,
    DEPTH_1_TITLES,
    WebSourceHelper,
)

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_HOST_DIR = "../deployment/docker-compose/web-test-data"
DEFAULT_START_URL = "http://web-source/index.html"

# The connector is configured with depth 1, so the landing page and the two
# pages it links to are expected; guides/deep.html is a hop further and is not.
EXPECTED_TITLES = DEPTH_0_TITLES + DEPTH_1_TITLES



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
def web_source() -> WebSourceHelper:
    helper = WebSourceHelper(os.getenv("WEB_TEST_HOST_DIR", DEFAULT_HOST_DIR))
    # Skip rather than fail when the directory is not usable, so a partial local
    # stack stays usable. In CI the mount is always present.
    try:
        helper.ensure_root()
    except OSError as exc:
        _unavailable(f"Web site directory not writable at {helper.root}: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def web_connector(
    web_source: WebSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    web_source.write_site()

    state: Dict[str, Any] = {
        "resource_name": str(web_source.root),
        "expected_titles": EXPECTED_TITLES,
        "beyond_depth_titles": ["Deep page"],
        "uploaded_count": len(EXPECTED_TITLES),
    }

    # Start URL and crawl settings live under "sync", not "auth".
    config = {
        "sync": {
            "url": os.getenv("WEB_CONNECTOR_START_URL", DEFAULT_START_URL),
            "type": "recursive",
            "depth": 1,
            "max_pages": 50,
            "follow_external": False,
            "use_headless_browser": False,
        }
    }

    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Web",
        connector_name=f"web-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(EXPECTED_TITLES),
    )

    yield state

    await destructor(
        web_source, pipeshub_client, graph_provider, state, connector_type="Web"
    )
