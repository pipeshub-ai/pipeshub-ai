# pyright: ignore-file

"""Confluence connector fixtures."""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio
from app.sources.client.confluence.confluence import (  # type: ignore[import-not-found]
    ConfluenceClient,
    ConfluenceApiKeyConfig,
)
from app.sources.external.confluence.confluence import ConfluenceDataSource

from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.graph_provider import (
    GraphProviderProtocol,  # type: ignore[import-not-found]
)
from connectors.confluence.confluence_v1_test_utils import (  # type: ignore[import-not-found]
    assert_api_content_matches_graph,
    fetch_space_content_snapshot,
    find_page_with_ancestors,
)
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    wait_for_sync_completion,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("confluence-conftest")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def confluence_datasource():
    """Session-scoped Confluence datasource using backend client."""
    base_url = os.getenv("CONFLUENCE_TEST_BASE_URL")
    email = os.getenv("CONFLUENCE_TEST_EMAIL")
    api_token = os.getenv("CONFLUENCE_TEST_API_TOKEN")

    if not base_url or not email or not api_token:
        pytest.skip(
            "Confluence credentials not set "
            "(CONFLUENCE_TEST_BASE_URL, CONFLUENCE_TEST_EMAIL, CONFLUENCE_TEST_API_TOKEN)"
        )

    config = ConfluenceApiKeyConfig(base_url=base_url, email=email, api_key=api_token)
    client = ConfluenceClient.build_with_config(config)
    return ConfluenceDataSource(client)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol):
    """Generic assertions helper - works for any connector."""
    return ConnectorAssertions(graph_provider)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def confluence_connector(
    confluence_datasource: ConfluenceDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_assertions: ConnectorAssertions,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Confluence connector using a pre-provisioned static space."""
    base_url = os.getenv("CONFLUENCE_TEST_BASE_URL")
    email = os.getenv("CONFLUENCE_TEST_EMAIL")
    api_token = os.getenv("CONFLUENCE_TEST_API_TOKEN")
    space_key_raw = os.getenv("CONFLUENCE_TEST_SPACE_KEY")

    assert email, "CONFLUENCE_TEST_EMAIL is not set"
    assert api_token, "CONFLUENCE_TEST_API_TOKEN is not set"
    assert base_url, "CONFLUENCE_TEST_BASE_URL is not set"
    assert space_key_raw, "CONFLUENCE_TEST_SPACE_KEY is not set (required for static IT space)"

    space_key = space_key_raw.strip()
    if not space_key:
        pytest.fail("CONFLUENCE_TEST_SPACE_KEY cannot be empty after trimming")

    connector_name = f"confluence-test-{uuid.uuid4().hex[:8]}"
    state: Dict[str, Any] = {
        "space_key": space_key,
        "connector_name": connector_name,
    }

    # ========== SETUP (read-only) ==========
    logger.info("SETUP: Using Confluence space '%s'", space_key)

    try:
        resp = await confluence_datasource.get_spaces(keys=[space_key])
        results = resp.json().get("results", [])
        if not results:
            pytest.fail(
                f"Confluence space '{space_key}' not found. "
                "Set CONFLUENCE_TEST_SPACE_KEY to an existing space key."
            )
        space = results[0]
        state["space_id"] = str(space.get("id"))
        state["space_name"] = space.get("name", space_key)
        logger.info(
            "SETUP: Found space '%s' (id=%s, name=%s)",
            space_key, state["space_id"], state["space_name"],
        )
    except Exception as e:
        pytest.fail(f"Failed to resolve Confluence space '{space_key}': {e}")

    try:
        snapshot = await fetch_space_content_snapshot(confluence_datasource, space_key)
        state["content_snapshot"] = snapshot

        if not snapshot.pages:
            pytest.fail(
                f"Confluence space '{space_key}' has no pages. "
                "Add at least one page to the IT space before running tests."
            )

        state["test_page_id"] = snapshot.pages[0].id
        state["test_page_title"] = snapshot.pages[0].title
        logger.info(
            "SETUP: Selected test page: %s (id=%s)",
            state["test_page_title"], state["test_page_id"],
        )

        hierarchy_page_id, hierarchy_parent_id = await find_page_with_ancestors(
            snapshot, confluence_datasource,
        )
        state["hierarchy_page_id"] = hierarchy_page_id
        state["hierarchy_parent_id"] = hierarchy_parent_id
        if hierarchy_page_id:
            logger.info(
                "SETUP: Found hierarchy: page %s has parent %s",
                hierarchy_page_id, hierarchy_parent_id,
            )
    except Exception as e:
        pytest.fail(f"Failed to fetch content snapshot for space '{space_key}': {e}")

    config = {
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": base_url,
            "email": email,
            "apiToken": api_token,
        },
        "filters": {
            "sync": {
                "values": {
                    "space_keys": {
                        "operator": "in",
                        "type": "list",
                        "value": [space_key],
                    }
                }
            }
        },
    }
    instance = pipeshub_client.create_connector(
        connector_type="Confluence",
        instance_name=connector_name,
        scope="team",
        config=config,
        auth_type="API_TOKEN",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state["connector_id"] = connector_id

    pipeshub_client.toggle_sync(connector_id, enable=True)

    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        timeout=180,
    )

    await assert_api_content_matches_graph(
        snapshot,
        confluence_datasource,
        graph_provider,
        connector_assertions,
        connector_id,
        phase="SETUP after initial sync",
    )

    space_rg = await graph_provider.get_record_group_by_external_id(
        connector_id, state["space_id"]
    )
    assert space_rg is not None, (
        f"Space RecordGroup for external id {state['space_id']} should exist after sync"
    )
    state["space_record_group_id"] = space_rg.id

    state["full_sync_count"] = await graph_provider.count_records(connector_id)
    logger.info("SETUP: Full sync completed with %d records", state["full_sync_count"])

    yield state

    # ========== TEARDOWN (connector only; preserve Confluence content) ==========
    logger.info(
        "TEARDOWN: Cleaning up connector %s (space '%s' preserved)",
        connector_id, space_key,
    )

    try:
        pipeshub_client.toggle_sync(connector_id, enable=False)
        status = pipeshub_client.get_connector_status(connector_id)
        assert not status.get("isActive"), "Connector should be inactive after disable"
    except Exception as e:
        logger.warning("TEARDOWN: Failed to disable connector %s: %s", connector_id, e)

    try:
        pipeshub_client.delete_connector(connector_id)
        pipeshub_client.wait(25)
        cleanup_timeout = int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300"))
        await graph_provider.assert_all_records_cleaned(connector_id, timeout=cleanup_timeout)
    except Exception as e:
        logger.warning("TEARDOWN: Failed to delete/clean connector %s: %s", connector_id, e)

    logger.info("TEARDOWN: Connector cleanup complete; Confluence space '%s' preserved", space_key)
