# pyright: ignore-file

"""GitHub Teams in the shared scenario matrix (``connectors/scenario_matrix.py``).

A connector of its own over the mutation repo (``GH_TEAMS_TEST_MUTATION_REPO``),
whose items are Markdown files this run commits under ``it/<run_id>/mx/``; see
``github_scenario_adapter.py``. Search runs as the admin that created the
connector, which the connector links to the token's GitHub account.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from connectors.github_teams.constants import GH_IT_RUN_ID, GH_SYNC_WAIT_SEC
from connectors.github_teams.github_scenario_adapter import UNSUPPORTED, GitHubCodeAdapter
from connectors.github_teams.github_test_utils import (
    create_github_connector,
    get_repo,
    list_filter,
    sync_filters,
    teardown_connector,
)
from connectors.scenario_matrix import ConnectorScenarioMatrix
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("github-teams-matrix")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    github_env: dict[str, str],
    github_rest: Any,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[GitHubCodeAdapter, None]:
    org = github_env["org"]
    mutation = await get_repo(github_rest, org, github_env["mutation"])
    connector_id = create_github_connector(
        pipeshub_client,
        token=github_env["token"],
        name=f"github-teams-matrix-{GH_IT_RUN_ID}-{uuid.uuid4().hex[:6]}",
        filters=sync_filters(repo_ids=list_filter("in", [mutation["full_name"]])),
    )
    adapter = GitHubCodeAdapter(
        rest=github_rest,
        repo_owner=org,
        repo=mutation["name"],
        branch=mutation["default_branch"],
        client=pipeshub_client,
        graph=graph_provider,
        connector_id=connector_id,
    )
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, min_records=1, timeout=GH_SYNC_WAIT_SEC,
        )
        yield adapter
    finally:
        await adapter.cleanup()
        try:
            await teardown_connector(pipeshub_client, graph_provider, connector_id)
        except Exception as e:  # noqa: BLE001 - teardown must not mask the test result
            logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)


@pytest.mark.integration
@pytest.mark.github_teams
class TestGitHubTeamsScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "GitHub Teams"
    UNSUPPORTED = UNSUPPORTED
