# pyright: ignore-file

"""Confluence connector fixtures."""

import logging
import os
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from app.sources.client.confluence.confluence import (  # type: ignore[import-not-found]
    ConfluenceClient,
    ConfluenceApiKeyConfig,
)
from app.sources.external.confluence.confluence import ConfluenceDataSource  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_until_graph_condition, async_wait_for_stable_record_count  # type: ignore[import-not-found]

logger = logging.getLogger("confluence-conftest")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def confluence_datasource():
    """Session-scoped Confluence datasource using backend client."""
    base_url = os.getenv("CONFLUENCE_TEST_BASE_URL")
    email = os.getenv("CONFLUENCE_TEST_EMAIL")
    api_token = os.getenv("CONFLUENCE_TEST_API_TOKEN")
    
    if not base_url or not email or not api_token:
        pytest.skip("Confluence credentials not set (CONFLUENCE_TEST_BASE_URL, CONFLUENCE_TEST_EMAIL, CONFLUENCE_TEST_API_TOKEN)")
    
    config = ConfluenceApiKeyConfig(base_url=base_url, email=email, api_key=api_token)
    client = ConfluenceClient.build_with_config(config)
    return ConfluenceDataSource(client)


def _normalize_space_key(space_key: str) -> str:
    """Normalize space key to uppercase alphanumeric, max 10 chars."""
    cleaned = "".join(ch for ch in space_key.upper() if ch.isalnum())[:10]
    if not cleaned:
        raise ValueError("space_key must contain at least one alphanumeric character")
    return cleaned


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def confluence_connector(
    confluence_datasource: ConfluenceDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root: Path,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Confluence connector with full lifecycle."""
    base_url = os.getenv("CONFLUENCE_TEST_BASE_URL")
    email = os.getenv("CONFLUENCE_TEST_EMAIL")
    api_token = os.getenv("CONFLUENCE_TEST_API_TOKEN")
    
    custom_space_key = os.getenv("CONFLUENCE_TEST_SPACE_KEY")
    if custom_space_key:
        space_key = _normalize_space_key(custom_space_key)
    else:
        space_key = _normalize_space_key(f"INTTEST{uuid.uuid4().hex[:6]}")
    
    connector_name = f"confluence-test-{uuid.uuid4().hex[:8]}"
    state: Dict[str, Any] = {
        "space_key": space_key,
        "connector_name": connector_name,
    }
    
    # ========== SETUP ==========
    logger.info("SETUP: Creating Confluence space '%s'", space_key)
    
    # Create or reuse space
    try:
        resp = await confluence_datasource.get_spaces(keys=[space_key])
        results = resp.json().get("results", [])
        if results:
            space = results[0]
            state["space_id"] = str(space.get("id"))
        else:
            raise ValueError("Space not found")
    except (ValueError, Exception):
        resp = await confluence_datasource.create_space(
            space_key=space_key,
            name=f"Integration Test Space {space_key}",
            description="Automated integration test space"
        )
        if resp.status != 200:
            raise RuntimeError(f"Failed to create Confluence space: HTTP {resp.status}")
        space_data = resp.json()
        state["space_id"] = str(space_data.get("id"))
        logger.info("SETUP: Created space '%s' (id=%s)", space_key, state["space_id"])
    
    page_count = 0
    sample_files = list(sample_data_root.rglob("*.txt"))[:5] if sample_data_root.exists() else []
    
    if sample_files:
        for file_path in sample_files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")[:5000]
                title = file_path.stem
                
                from html import escape
                safe = escape(content)
                lines = safe.splitlines() or [safe]
                paragraphs = [f"<p>{line if line else '&#160;'}</p>" for line in lines]
                storage_body = "".join(paragraphs) if paragraphs else "<p></p>"
                
                body_payload = {
                    "spaceId": state["space_id"],
                    "status": "current",
                    "title": title,
                    "body": {
                        "representation": "storage",
                        "value": storage_body
                    }
                }
                
                resp = await confluence_datasource.create_page(
                    root_level=True,
                    body=body_payload
                )
                
                page_count += 1
            except Exception as e:
                logger.error("SETUP: Failed to create page from %s: %s", file_path.name, e, exc_info=True)
    
    if page_count < 3:
        for i in range(3 - page_count):
            title = f"InitTestPage{i+1+page_count}-{uuid.uuid4().hex[:6]}"
            content = f"<p>This is initial test page {i+1+page_count} for integration testing.</p>"
            
            body_payload = {
                "spaceId": state["space_id"],
                "status": "current",
                "title": title,
                "body": {
                    "representation": "storage",
                    "value": content
                }
            }
            
            resp = await confluence_datasource.create_page(
                root_level=True,
                body=body_payload
            )
            
            if resp.status == 200:
                page_count += 1
            else:
                logger.error("SETUP: Failed to create page '%s': HTTP %s", title, resp.status)
    
    assert page_count >= 3, f"Expected at least 3 initial pages, got {page_count}"
    state["uploaded_count"] = page_count
    
    # Create connector
    config = {
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": base_url,
            "email": email,
            "apiToken": api_token,
        }
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
    
    async def _check_initial_sync() -> bool:
        return await graph_provider.count_records(connector_id) >= page_count
    
    await wait_until_graph_condition(
        connector_id,
        check=_check_initial_sync,
        timeout=180,
        poll_interval=10,
        description="initial sync",
    )
    
    full_count = await async_wait_for_stable_record_count(
        graph_provider,
        connector_id,
        stability_checks=3,
        interval=5,
        max_rounds=20,
    )
    state["full_sync_count"] = full_count
    
    yield state
    
    # ========== TEARDOWN ==========
    logger.info("TEARDOWN: Cleaning up connector %s and space '%s'", connector_id, space_key)
    
    # Disable connector
    try:
        pipeshub_client.toggle_sync(connector_id, enable=False)
        status = pipeshub_client.get_connector_status(connector_id)
        assert not status.get("isActive"), "Connector should be inactive after disable"
    except Exception as e:
        logger.warning("TEARDOWN: Failed to disable connector %s: %s", connector_id, e)
    
    # Delete connector
    try:
        pipeshub_client.delete_connector(connector_id)
        pipeshub_client.wait(25)
        cleanup_timeout = int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300"))
        await graph_provider.assert_all_records_cleaned(connector_id, timeout=cleanup_timeout)
    except Exception as e:
        logger.warning("TEARDOWN: Failed to delete/clean connector %s: %s", connector_id, e)
    
    try:
        resp = await confluence_datasource.get_pages_in_space(state["space_id"], limit=250)
        pages = resp.json().get("results", [])
        
        # First pass: Delete pages (moves to trash)
        for page in pages:
            try:
                page_id = page.get("id")
                if page_id:
                    await confluence_datasource.delete_page(int(page_id), purge=False)
            except Exception as e:
                logger.warning("TEARDOWN: Failed to delete page %s: %s", page.get("id"), e)
        
        # Second pass: Purge deleted pages
        for page in pages:
            try:
                page_id = page.get("id")
                if page_id:
                    await confluence_datasource.delete_page(int(page_id), purge=True)
            except Exception as e:
                logger.warning("TEARDOWN: Failed to purge page %s: %s", page.get("id"), e)
    except Exception as e:
        logger.warning("TEARDOWN: Failed to clear pages in space '%s': %s", space_key, e)
    
    try:
        await confluence_datasource.delete_space(space_key)
        logger.info("TEARDOWN: Deleted space '%s'", space_key)
    except Exception as e:
        logger.warning("TEARDOWN: Failed to delete space '%s': %s", space_key, e)
