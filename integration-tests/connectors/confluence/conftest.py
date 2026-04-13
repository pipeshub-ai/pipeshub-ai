# pyright: ignore-file

"""Confluence connector fixtures."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import (  # type: ignore[import-not-found]
    RESOURCE_NAME,
    constructor,
    destructor,
)
from connectors.confluence.confluence_storage_helper import ConfluenceStorageHelper
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol


@pytest.fixture(scope="session")
def confluence_storage():
    """Session-scoped Confluence storage helper."""
    base_url = os.getenv("CONFLUENCE_TEST_BASE_URL")
    email = os.getenv("CONFLUENCE_TEST_EMAIL")
    api_token = os.getenv("CONFLUENCE_TEST_API_TOKEN")

    if not base_url or not email or not api_token:
        pytest.skip("Confluence credentials not set (CONFLUENCE_TEST_BASE_URL, CONFLUENCE_TEST_EMAIL, CONFLUENCE_TEST_API_TOKEN)")
    
    return ConfluenceStorageHelper(
        base_url=base_url,
        email=email,
        api_token=api_token,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def confluence_connector(
    confluence_storage: ConfluenceStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Confluence connector with full lifecycle."""
    base_url = os.getenv("CONFLUENCE_TEST_BASE_URL")
    email = os.getenv("CONFLUENCE_TEST_EMAIL")
    api_token = os.getenv("CONFLUENCE_TEST_API_TOKEN")
    
    # Allow custom space key via env var (useful if you have an existing test space)
    custom_space_key = os.getenv("CONFLUENCE_TEST_SPACE_KEY")

    config = {
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": base_url,
            "email": email,
            "apiToken": api_token,
        }
    }

    state = await constructor(
        confluence_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="Confluence space",
        connector_type="Confluence",
        connector_config=config,
        create_fn="create_space",
        scope="team",
        auth_type="API_TOKEN",
    )

    if custom_space_key:
        space_key = confluence_storage._normalize_space_key(custom_space_key)
    else:
        space_key = confluence_storage._normalize_space_key(state["resource_name"])

    state["space_key"] = space_key

    yield state

    await destructor(
        confluence_storage,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Confluence",
    )
