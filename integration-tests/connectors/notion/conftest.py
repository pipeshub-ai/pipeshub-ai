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

With ``NOTION_TEST_SEED_MANIFEST`` set (the Notion simulator, ``connectors/notion_sim.env``), the
fixture tree already exists: the manifest names every id, nothing is seeded, swept or trashed, and
``NOTION_TEST_RESET_URL`` restores the source before the run so writes of an earlier run are gone.
"""

import asyncio
import logging
import os
import uuid
import warnings
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
import requests

from helper.source_credentials import source_unavailable

from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.clients.users_client import UsersClient  # type: ignore[import-not-found]
from helper.pipeshub_users import find_pipeshub_user_by_email  # type: ignore[import-not-found]
from helper.second_user import (  # type: ignore[import-not-found]
    SecondUser,
    log_in_existing_user,
    log_out_existing_user,
    source_user_account,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from connectors.notion.constants import (  # type: ignore[import-not-found]
    ENV_ROOT_DATABASE_ID,
    ENV_ROOT_PAGE_ID,
    ENV_SEED_MANIFEST,
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
    load_seed_manifest,
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
from connectors.notion.notion_test_utils import (  # type: ignore[import-not-found]
    scoped_external_ids,
)
from app.models.entities import RecordType

logger = logging.getLogger("notion-conftest")

#: Baseline sync attempts before the fixture gives up. Each retry is a full resync, which also
#: buys the search index another minute to settle.
_BASELINE_SYNC_ATTEMPTS = 3


async def _missing_seeded_ids(
    graph_provider: GraphProviderProtocol, connector_id: str, seed: NotionSeed
) -> set[str]:
    """Seeded page / data-source ids the connector did not put in the graph."""
    pages = await scoped_external_ids(
        graph_provider, connector_id, RecordType.WEBPAGE.value
    )
    sources = await scoped_external_ids(
        graph_provider, connector_id, RecordType.DATASOURCE.value
    )
    return (seed.expected_page_ids - pages) | (
        seed.expected_data_source_ids - sources
    )


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else None


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def notion_source_helper() -> AsyncGenerator[NotionSourceHelper, None]:
    """Notion API helper for creating/deleting test resources."""
    token = _env(ENV_TOKEN)
    if not token:
        source_unavailable(
            "The Notion workspace this suite syncs from is not configured.",
            secrets=[ENV_TOKEN],
        )
    helper = NotionSourceHelper(token)
    try:
        yield helper
    finally:
        await helper.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol) -> ConnectorAssertions:
    """Generic assertions helper — works for any connector."""
    return ConnectorAssertions(graph_provider)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def notion_reader(
    notion_source_helper: NotionSourceHelper,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
) -> AsyncGenerator[SecondUser, None]:
    """A PipesHub account with a workspace member's email, to open records as.

    A Notion record is open only to the workspace's members, and the org admin the suite runs
    as is not one in the simulator's workspace. An account that already exists may be a real
    login, so its credentials are put back afterwards.
    """
    email = next(
        (
            str((user.get("person") or {}).get("email") or "").strip().lower()
            for user in await notion_source_helper.list_all_users()
            if user.get("type") == "person" and (user.get("person") or {}).get("email")
        ),
        "",
    )
    assert email, "the Notion workspace lists no member with an email to open records as"
    pipeshub_client._ensure_access_token()
    existing = await asyncio.to_thread(find_pipeshub_user_by_email, users_client, email)
    if existing is None:
        yield await asyncio.to_thread(source_user_account, pipeshub_client, email, "Notion IT reader")
        return
    user_id = str(existing.get("_id") or existing.get("id") or "")
    reader = await asyncio.to_thread(log_in_existing_user, pipeshub_client, user_id, email)
    try:
        yield reader
    finally:
        await asyncio.to_thread(log_out_existing_user, pipeshub_client, reader)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def notion_connector(
    notion_source_helper: NotionSourceHelper,
    # Created before the baseline sync, whose user sync links it to the app.
    notion_reader: SecondUser,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Module-scoped Notion connector over a freshly seeded fixture tree (or a pinned one, see
    ``ENV_SEED_MANIFEST``)."""
    token = _env(ENV_TOKEN)
    # A pinned tree: its manifest names every id, so there is nothing to discover or seed.
    manifest = _env(ENV_SEED_MANIFEST)
    if manifest and not os.path.isabs(manifest):
        manifest = os.path.join(os.path.dirname(__file__), manifest)
    # Both ids are optional: in a dedicated workspace they can be discovered from what the
    # integration can see. The env vars stay as overrides for a workspace with several shared
    # roots. Values may be pasted straight from a Notion URL (undashed, possibly a title slug).
    root_page_id = normalize_notion_id(_env(ENV_ROOT_PAGE_ID) or "")
    if not root_page_id and not manifest:
        candidates = await discover_root_pages(notion_source_helper)
        if len(candidates) == 1:
            root_page_id = candidates[0]
            logger.info("SETUP: discovered IT root page %s", root_page_id)
        elif not candidates:
            source_unavailable(
                "No page is shared with the Notion integration, so this suite has "
                "nothing to build its fixture tree under. Share one top-level page "
                "with the integration.",
                secrets=[ENV_ROOT_PAGE_ID],
            )
        else:
            source_unavailable(
                f"{len(candidates)} shared root pages found, so this suite cannot "
                "tell which one to use. Name the one it should build under.",
                secrets=[ENV_ROOT_PAGE_ID],
            )

    root_database_id = normalize_notion_id(_env(ENV_ROOT_DATABASE_ID) or "") or None
    if not root_database_id and not manifest:
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
        if manifest:
            logger.info("SETUP: using the pinned fixture tree in %s", manifest)
            seed = load_seed_manifest(manifest)
            reset_url = _env("NOTION_TEST_RESET_URL")
            if reset_url:
                # Writes of an earlier run (the incremental cases) would otherwise still be in the tree.
                response = await asyncio.to_thread(requests.post, reset_url, timeout=60)
                response.raise_for_status()
            else:
                warnings.warn(
                    "NOTION_TEST_SEED_MANIFEST is set without NOTION_TEST_RESET_URL: writes left by an "
                    "earlier run stay in the pinned tree and the id-set checks may fail",
                    stacklevel=1,
                )
                logger.warning("SETUP: pinned fixture tree used without a reset URL")
        else:
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

        # Notion Search is eventually consistent and the connector enumerates only through it,
        # so a sync that starts while the index is still settling quietly indexes fewer objects
        # than were seeded and still reports success. Check the seeded ids actually landed
        # rather than trusting the wait above, and resync if they did not: a full resync drops
        # the connector's sync points, so it re-enumerates instead of resuming past the
        # checkpoint and skipping the very objects it missed.
        #
        # Ids, not counts: the connector syncs the whole workspace, so a concurrent run's pages
        # land here too and a count can clear the bar while our own objects are still absent.
        for attempt in range(_BASELINE_SYNC_ATTEMPTS):
            state["full_sync_count"] = await wait_for_sync_completion(
                pipeshub_client, graph_provider, instance.connector_id,
                min_records=1, timeout=SYNC_TIMEOUT,
            )
            missing = await _missing_seeded_ids(
                graph_provider, instance.connector_id, seed
            )
            if not missing:
                break
            assert attempt < _BASELINE_SYNC_ATTEMPTS - 1, (
                f"baseline sync missed {len(missing)} seeded object(s) after "
                f"{_BASELINE_SYNC_ATTEMPTS} attempts (e.g. {sorted(missing)[:5]}): Notion "
                "Search never returned the full seed to the connector"
            )
            logger.warning(
                "SETUP: baseline sync missed %d seeded object(s) — full resync (attempt %d/%d)",
                len(missing), attempt + 2, _BASELINE_SYNC_ATTEMPTS,
            )
            pipeshub_client.resync_connector(instance.connector_id, full_sync=True)

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

        if manifest:
            pass  # a pinned tree stays: the next run reads it again
        elif seed is not None:
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
