# pyright: ignore-file

"""Notion connector fixtures.

- session ``notion_source_helper``: Notion API client for creating/deleting test resources
  (skips if ``NOTION_TEST_*`` creds are missing).
- session ``connector_assertions``: generic ``ConnectorAssertions`` over the graph provider.
- module ``notion_connector``: sweeps stale run roots, seeds the whole fixture tree, creates the
  product connector with API-token auth, runs the baseline sync, yields the manifest, then tears
  down the connector, the graph, and the seeded Notion content.

Unlike the Jira / Outlook suites, nothing here is pre-provisioned beyond the permanent IT root
page and the workspace-root database: the tests create every page, database, block, comment and
attachment they assert on, and trash them at the end.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from connectors.notion.constants import (  # type: ignore[import-not-found]
    ENV_ROOT_DATABASE_ID,
    ENV_ROOT_PAGE_ID,
    ENV_TOKEN,
    GRAPH_CLEANUP_TIMEOUT,
    NOTION_AUTH_TYPE,
    NOTION_CONNECTOR_TYPE,
    NOTION_SCOPE,
    SYNC_TIMEOUT,
)
from connectors.notion.notion_seed import (  # type: ignore[import-not-found]
    NotionSeed,
    discover_root_pages,
    discover_workspace_root_database,
    seed_notion_workspace,
    sweep_stale_runs,
    teardown_seed,
    wait_for_seed_indexed,
    wait_for_swept_runs_gone,
)
from connectors.notion.notion_source_helper import (  # type: ignore[import-not-found]
    NotionSourceHelper,
    normalize_notion_id,
)

logger = logging.getLogger("notion-conftest")


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else None


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def notion_source_helper() -> AsyncGenerator[NotionSourceHelper, None]:
    """Notion API helper for creating/deleting test resources."""
    token = _env(ENV_TOKEN)
    if not token:
        pytest.skip(f"Notion credentials not set ({ENV_TOKEN}).")
    helper = NotionSourceHelper(token)
    try:
        yield helper
    finally:
        await helper.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol) -> ConnectorAssertions:
    """Generic assertions helper — works for any connector."""
    return ConnectorAssertions(graph_provider)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def notion_connector(
    notion_source_helper: NotionSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Module-scoped Notion connector over a freshly seeded fixture tree."""
    token = _env(ENV_TOKEN)
    # Both ids are optional: in a dedicated workspace they can be discovered from what the
    # integration can see. The env vars stay as overrides for a workspace with several shared
    # roots. Values may be pasted straight from a Notion URL (undashed, possibly a title slug).
    root_page_id = normalize_notion_id(_env(ENV_ROOT_PAGE_ID) or "")
    if not root_page_id:
        candidates = await discover_root_pages(notion_source_helper)
        if len(candidates) == 1:
            root_page_id = candidates[0]
            logger.info("SETUP: discovered IT root page %s", root_page_id)
        elif not candidates:
            pytest.skip(
                "No page is shared with the Notion integration. Share one top-level page with "
                f"it (or set {ENV_ROOT_PAGE_ID}) — the suite builds its fixture tree under it."
            )
        else:
            pytest.skip(
                f"{len(candidates)} shared root pages found ({candidates}); set "
                f"{ENV_ROOT_PAGE_ID} to pick one."
            )

    root_database_id = normalize_notion_id(_env(ENV_ROOT_DATABASE_ID) or "") or None
    if not root_database_id:
        discovered = await discover_workspace_root_database(notion_source_helper)
        if discovered:
            root_database_id = discovered[0]
            logger.info("SETUP: discovered workspace-root database %s", root_database_id)
        else:
            logger.info(
                "SETUP: no workspace-root database shared — TC-DS-003 will skip. Share a "
                f"database that sits at the workspace root (or set {ENV_ROOT_DATABASE_ID}) "
                "to cover data sources with a workspace parent."
            )

    run_id = uuid.uuid4().hex[:8]
    connector_name = f"notion-test-{run_id}"
    state: dict[str, Any] = {
        "run_id": run_id,
        "connector_name": connector_name,
        "connector_id": None,
        "source_helper": notion_source_helper,
        "seed": None,
    }
    seed: NotionSeed | None = None

    # ========== SETUP ==========
    # Seeding happens inside the try so a failure part-way through still trashes whatever was
    # created — the run root is the first thing made, and trashing it cascades.
    try:
        swept = await sweep_stale_runs(notion_source_helper, root_page_id)
        if swept:
            logger.warning("SETUP: swept %d stale run root(s) before seeding", swept)
            await wait_for_swept_runs_gone(notion_source_helper, root_page_id)

        logger.info("SETUP: seeding Notion fixture tree (run=%s)", run_id)
        seed = await seed_notion_workspace(
            notion_source_helper,
            root_page_id=root_page_id,
            root_database_id=root_database_id,
            run_id=run_id,
        )
        state["seed"] = seed
        state["workspace_id"] = seed.workspace_id
        state["workspace_name"] = seed.workspace_name

        # The connector only sees what Notion Search returns, and that index lags the
        # seeding writes — sync before it settles and the newest pages are simply absent.
        await wait_for_seed_indexed(notion_source_helper, seed)

        config: dict[str, Any] = {
            "auth": {
                "authType": NOTION_AUTH_TYPE,
                "apiToken": token,
            },
        }

        instance = pipeshub_client.create_connector(
            connector_type=NOTION_CONNECTOR_TYPE,
            instance_name=connector_name,
            scope=NOTION_SCOPE,
            config=config,
            auth_type=NOTION_AUTH_TYPE,
        )
        assert instance.connector_id, "Connector must have a valid ID"
        state["connector_id"] = instance.connector_id

        pipeshub_client.toggle_sync(instance.connector_id, enable=True)
        state["full_sync_count"] = await wait_for_sync_completion(
            pipeshub_client, graph_provider, instance.connector_id,
            min_records=1, timeout=SYNC_TIMEOUT,
        )
        logger.info(
            "SETUP done: baseline sync produced %d records (expected %d) for connector %s",
            state["full_sync_count"], seed.expected_record_count, instance.connector_id,
        )

        yield state
    finally:
        # ========== TEARDOWN: connector + graph, then the seeded Notion content ==========
        connector_id = state.get("connector_id")
        if connector_id:
            logger.info("TEARDOWN: cleaning connector %s", connector_id)
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
                status = pipeshub_client.get_connector_status(connector_id)
                assert not status.get("isActive"), "Connector should be inactive after disable"
            except Exception as e:  # noqa: BLE001
                logger.warning("TEARDOWN: disable failed for %s: %s", connector_id, e)
            try:
                pipeshub_client.delete_connector(connector_id)
                pipeshub_client.wait(25)
                cleanup_timeout = int(
                    os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", str(GRAPH_CLEANUP_TIMEOUT))
                )
                await graph_provider.assert_all_records_cleaned(
                    connector_id, timeout=cleanup_timeout
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)

        if seed is not None:
            logger.info("TEARDOWN: trashing seeded Notion run root %s", seed.run_root_page_id)
            await teardown_seed(notion_source_helper, seed)
        else:
            # Seeding raised before returning a manifest, so the run root id is unknown —
            # sweep by title instead, otherwise the orphan inflates the next run's counts.
            try:
                # only_run_id: this run's root is seconds old, so the age guard that
                # protects a concurrent run would otherwise skip our own orphan.
                await sweep_stale_runs(
                    notion_source_helper, root_page_id, only_run_id=run_id
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("TEARDOWN: sweep of partial seed failed: %s", e)


@pytest.fixture(scope="module")
def notion_seed(notion_connector: dict[str, Any]) -> NotionSeed:
    """The seeded fixture manifest — every id the tests assert against."""
    return notion_connector["seed"]
