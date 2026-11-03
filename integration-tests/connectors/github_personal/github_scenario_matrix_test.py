# pyright: ignore-file

"""GitHub (personal) in the shared scenario matrix (``connectors/scenario_matrix.py``).

The personal connector reuses the GitHub Teams tenant and PAT (see this
directory's ``conftest.py``). The rest of this suite only reads GitHub; the
matrix writes, so it points a personal connector of its own at the Teams
mutation repo and commits its items under ``it/<run_id>/mx/`` there, the same
way the Teams matrix does (``github_teams/github_scenario_adapter.py``). A
personal connector grants its creator access to what it syncs, so search runs
as the admin that created it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from connectors.github_personal.github_personal_utils import (
    connector_name,
    create_personal_connector,
)
from connectors.github_teams.constants import ENV_MUTATION_REPO, GH_SYNC_WAIT_SEC
from connectors.github_teams.github_scenario_adapter import UNSUPPORTED, GitHubCodeAdapter
from connectors.github_teams.github_test_utils import get_repo, teardown_connector
from connectors.scenario_matrix import ConnectorScenarioMatrix
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.source_credentials import source_unavailable
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("github-personal-matrix")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    github_env: dict[str, str],
    github_rest: Any,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[GitHubCodeAdapter, None]:
    mutation_name = os.getenv(ENV_MUTATION_REPO, "").strip()
    if not mutation_name:
        source_unavailable(
            "No GitHub repository is named for the matrix to write its files to.",
            secrets=[ENV_MUTATION_REPO],
        )
    org = github_env["org"]
    mutation = await get_repo(github_rest, org, mutation_name)
    connector_id = create_personal_connector(
        pipeshub_client,
        token=github_env["token"],
        name=connector_name("matrix"),
        repo_full_name=mutation["full_name"],
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
@pytest.mark.github
class TestGitHubScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "GitHub"
    UNSUPPORTED = UNSUPPORTED
