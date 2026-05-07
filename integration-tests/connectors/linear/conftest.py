# pyright: ignore-file

"""Linear connector fixtures.

Mirrors the Jira-project lifecycle pattern:
- session-scoped ``linear_datasource`` (skips if credentials missing)
- module-scoped ``linear_connector`` that creates a fresh team, seeds issues,
  registers a Pipeshub Linear connector with API_TOKEN auth, runs an initial
  sync, then deletes the team during teardown (cascades all seed issues).

Only one env var is needed: ``LINEAR_TEST_API_TOKEN``. The team is created
and torn down by the fixture itself — no ``LINEAR_TEST_TEAM_ID`` to manage.
The team key is fixed (``INTTEST``) and the fixture reuses an existing team
of that key if a previous run failed teardown, so leftover state never
blocks the next run.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import pytest
import pytest_asyncio

from app.sources.client.linear.linear import (  # type: ignore[import-not-found]
    LinearClient,
    LinearTokenConfig,
)
from app.sources.external.linear.linear import (  # type: ignore[import-not-found]
    LinearDataSource,
)
from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    wait_for_sync_completion,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("linear-conftest")

# Stable test-team identity. The fixture reuses the existing team if it
# already exists (e.g. a previous run failed teardown) and tears it down at
# the end via ``teamDelete``, which cascades to issues.
_TEST_TEAM_KEY = "INTTEST"
_TEST_TEAM_NAME = "Pipeshub Integration Test"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def linear_datasource() -> LinearDataSource:
    """Session-scoped Linear datasource using API-token auth."""
    api_token = os.getenv("LINEAR_TEST_API_TOKEN")
    if not api_token:
        pytest.skip(
            "Linear credentials not set (LINEAR_TEST_API_TOKEN). "
            "Generate a personal API key at https://linear.app/settings/api "
            "and set LINEAR_TEST_API_TOKEN. The token must belong to a workspace "
            "admin so the fixture can create / delete teams and issues."
        )

    config = LinearTokenConfig(token=api_token)
    client = LinearClient.build_with_config(config)
    return LinearDataSource(client)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol):
    """Generic assertions helper - works for any connector."""
    return ConnectorAssertions(graph_provider)


async def _find_team_by_key(
    datasource: LinearDataSource, key: str
) -> Optional[Dict[str, Any]]:
    """Return the team dict whose ``key`` matches, or ``None``.

    Uses a server-side filter when available; falls back to scanning the
    first 50 teams, which is plenty for any test workspace.
    """
    resp = await datasource.teams(
        first=50,
        filter={"key": {"eq": key}},
    )
    if not resp.success:
        # Some Linear deployments reject unknown filter shapes — fall back
        # to a plain list.
        resp = await datasource.teams(first=50)
        if not resp.success:
            raise RuntimeError(f"Failed to list Linear teams: {resp.message}")
    nodes = ((resp.data or {}).get("teams") or {}).get("nodes") or []
    for node in nodes:
        if str(node.get("key", "")).upper() == key.upper():
            return node
    return None


async def _first_available_team(
    datasource: LinearDataSource,
) -> Optional[Dict[str, Any]]:
    """Return the first team accessible to this token, or ``None`` if there are none."""
    resp = await datasource.teams(first=10)
    if not resp.success:
        return None
    nodes = ((resp.data or {}).get("teams") or {}).get("nodes") or []
    return nodes[0] if nodes else None


async def _create_or_reuse_team(
    datasource: LinearDataSource, key: str, name: str
) -> tuple[Dict[str, Any], bool]:
    """Reuse the test team if it exists; create one when allowed; otherwise
    fall back to the first available workspace team.

    Returns ``(team, created_by_fixture)``. ``created_by_fixture`` is ``True``
    only when this fixture invocation produced a brand-new team — used by
    teardown to decide between ``teamDelete`` (cascade) and per-issue
    ``issueArchive`` (when we don't own the team).
    """
    # 1. Reuse a team with the canonical test key if one exists.
    existing = await _find_team_by_key(datasource, key)
    if existing and existing.get("id"):
        logger.info(
            "SETUP: Reusing existing Linear team key=%s id=%s",
            key, existing["id"],
        )
        return existing, False

    # 2. Try to create a fresh team.
    create_resp = await datasource.teamCreate(
        input={
            "name": name,
            "key": key,
            "description": "Automated integration-test team. Safe to delete.",
        }
    )
    if create_resp.success:
        team = ((create_resp.data or {}).get("teamCreate") or {}).get("team") or {}
        if team.get("id"):
            logger.info("SETUP: Created Linear team key=%s id=%s", key, team["id"])
            return team, True

    # 3. teamCreate failed — most often "Access denied" because Linear restricts
    # team creation to workspace admins/owners. Fall back to whatever team this
    # token CAN access, and clean up per-issue at teardown instead of deleting
    # the team.
    fallback = await _first_available_team(datasource)
    if not fallback or not fallback.get("id"):
        raise RuntimeError(
            f"Cannot create Linear team key={key!r} ({create_resp.message}) "
            "and no existing team is accessible to this token. Either grant "
            "the token workspace-admin permissions, or share at least one team "
            "with the user who owns the token."
        )
    logger.warning(
        "SETUP: teamCreate denied (%s); falling back to existing team "
        "key=%s id=%s. Teardown will archive seeded issues instead of "
        "deleting the team.",
        create_resp.message, fallback.get("key"), fallback["id"],
    )
    return fallback, False


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def linear_connector(
    linear_datasource: LinearDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Linear connector with full lifecycle.

    Yields a dict with: team_id, team_key, viewer_id, seed_issue_ids,
    connector_id, uploaded_count, full_sync_count.
    """
    api_token = os.getenv("LINEAR_TEST_API_TOKEN")
    assert api_token, "LINEAR_TEST_API_TOKEN is not set"

    connector_name = f"linear-test-{uuid.uuid4().hex[:8]}"
    state: Dict[str, Any] = {
        "connector_name": connector_name,
        "team_key": _TEST_TEAM_KEY,
        "seed_issue_ids": [],
    }

    # ========== SETUP ==========

    # 1. Verify the token. The data-source's ``viewer`` operation actually
    # queries ``organization`` under the hood (see graphql_op.py:301), so the
    # response payload is keyed on "organization" rather than "viewer".
    me_resp = await linear_datasource.viewer()
    if not me_resp.success:
        raise RuntimeError(
            f"Linear auth check failed: {me_resp.message}. Check LINEAR_TEST_API_TOKEN."
        )
    org = ((me_resp.data or {}).get("organization")) or {}
    state["org_id"] = org.get("id")
    logger.info(
        "SETUP: Linear auth OK — org id=%s name=%r",
        state["org_id"], org.get("name"),
    )

    # 2. Create or reuse the test team. ``team_created`` tells teardown whether
    # we own the team (delete it) or are sharing one (just archive our issues).
    team, team_created = await _create_or_reuse_team(
        linear_datasource, _TEST_TEAM_KEY, _TEST_TEAM_NAME,
    )
    team_id = str(team["id"])
    state["team_id"] = team_id
    state["team_key"] = team.get("key") or _TEST_TEAM_KEY
    state["team_created_by_fixture"] = team_created

    # 3. Seed 3 issues so the initial sync has something to index.
    seed_count_target = 3
    for i in range(seed_count_target):
        title = f"InitTestLinear{i + 1}-{uuid.uuid4().hex[:6]}"
        resp = await linear_datasource.issueCreate(
            input={
                "title": title,
                "teamId": team_id,
                "description": f"Seed issue {i + 1} for integration test.",
            }
        )
        if not resp.success:
            logger.error(
                "SETUP: Failed to create '%s': %s", title, resp.message,
            )
            continue
        created = (
            ((resp.data or {}).get("issueCreate") or {}).get("issue")
        ) or {}
        issue_id = created.get("id")
        if issue_id:
            state["seed_issue_ids"].append(str(issue_id))

    if len(state["seed_issue_ids"]) < seed_count_target:
        # Don't outright fail — record the partial count and let the test catch it.
        logger.warning(
            "SETUP: Only %d/%d seed issues created — sync coverage will be reduced",
            len(state["seed_issue_ids"]), seed_count_target,
        )
    state["uploaded_count"] = len(state["seed_issue_ids"])
    logger.info(
        "SETUP: Seeded %d Linear issues in team %s: %s",
        state["uploaded_count"], _TEST_TEAM_KEY, state["seed_issue_ids"],
    )

    # 4. Register the connector through the Pipeshub control plane.
    config: Dict[str, Any] = {
        "auth": {
            "authType": "API_TOKEN",
            "apiToken": api_token,
        }
    }
    instance = pipeshub_client.create_connector(
        connector_type="Linear",
        instance_name=connector_name,
        scope="team",
        config=config,
        auth_type="API_TOKEN",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state["connector_id"] = connector_id

    pipeshub_client.toggle_sync(connector_id, enable=True)

    # 5. Wait for the initial sync to absorb the seeded issues.
    full_count = await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        min_records=state["uploaded_count"],
        timeout=300,
    )

    # 6. Verification cycle — same race-avoidance pattern Confluence / Jira use.
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)
    verified_count = await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        min_records=state["uploaded_count"],
        timeout=300,
    )
    if verified_count != full_count:
        logger.info(
            "SETUP: Verification sync adjusted record count %d -> %d",
            full_count, verified_count,
        )
    state["full_sync_count"] = verified_count

    yield state

    # ========== TEARDOWN ==========
    logger.info(
        "TEARDOWN: Cleaning up connector %s and Linear team %s (id=%s)",
        connector_id, _TEST_TEAM_KEY, team_id,
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

    # Cleanup branches on whether we own the team:
    #   - team_created_by_fixture=True  → teamDelete cascades to all issues
    #   - team_created_by_fixture=False → archive only the issues WE created,
    #     leave the shared team and its other issues untouched.
    if state.get("team_created_by_fixture"):
        try:
            del_resp = await linear_datasource.teamDelete(id=team_id)
            if not del_resp.success:
                logger.warning(
                    "TEARDOWN: teamDelete returned error for team %s: %s",
                    team_id, del_resp.message,
                )
            else:
                logger.info(
                    "TEARDOWN: Deleted Linear team %s (id=%s)",
                    state.get("team_key"), team_id,
                )
        except Exception as e:
            logger.warning("TEARDOWN: Failed to delete Linear team %s: %s", team_id, e)
    else:
        archive_ids: List[str] = list(state.get("seed_issue_ids") or [])
        archive_ids.extend(state.get("incremental_issue_ids") or [])  # set by TC-INCR-001
        archived = 0
        for issue_id in archive_ids:
            try:
                resp = await linear_datasource.issueArchive(id=issue_id)
                if resp.success:
                    archived += 1
                else:
                    logger.warning(
                        "TEARDOWN: issueArchive failed for %s: %s",
                        issue_id, resp.message,
                    )
            except Exception as e:
                logger.warning(
                    "TEARDOWN: Failed to archive Linear issue %s: %s", issue_id, e,
                )
        logger.info(
            "TEARDOWN: Reused team %s (id=%s) — archived %d/%d test issues "
            "(team itself was not created by the fixture, so it's left intact).",
            state.get("team_key"), team_id, archived, len(archive_ids),
        )
