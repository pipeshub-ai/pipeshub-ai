# pyright: ignore-file

"""GitHub (personal) connector fixtures.

The personal connector (``app.connectors.sources.github``) reuses the GitHub Teams
tenant and its classic PAT: ``GithubConnector`` subclasses ``GitHubTeamsConnector``
and registers only ``AuthType.OAUTH``, and the OAuth gate checks nothing more than a
stored ``credentials.access_token`` — so the same injected PAT authenticates it (see
``connectors/github_teams/conftest.py``). Only the org, primary and public repos are
needed; this suite never writes to GitHub.

- session-scoped ``github_rest``: raw REST client for source-of-truth reads
- module-scoped ``github_personal_connector``: a personal-scope connector over the
  private primary repo alone (an instance syncs exactly one repository), synced once
  and torn down after the module.
"""

import logging
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.github_personal.github_personal_utils import (  # type: ignore[import-not-found]
    connector_name,
    require_env,
    synced_personal_connector,
)
from connectors.github_teams.constants import ENV_PRIMARY_REPO, ENV_PUBLIC_REPO  # type: ignore[import-not-found]
from connectors.github_teams.github_test_utils import (  # type: ignore[import-not-found]
    build_rest_client,
    get_repo,
    get_tree,
    list_issues,
    list_pulls,
)

logger = logging.getLogger("github-personal-conftest")


@pytest.fixture(scope="session")
def github_env() -> dict[str, str]:
    return require_env()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def github_rest(github_env: dict[str, str]) -> AsyncGenerator[Any, None]:
    client = build_rest_client(github_env["token"])
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def github_personal_connector(
    github_env: dict[str, str],
    github_rest: Any,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """A personal connector over the private primary repo, synced once. Read-only."""
    org = github_env["org"]
    primary = await get_repo(github_rest, org, github_env["primary"])
    public = await get_repo(github_rest, org, github_env["public"])
    if primary.get("visibility") == "public":
        raise RuntimeError(
            f"SETUP: {primary['full_name']} is public; point {ENV_PRIMARY_REPO} at a "
            "private repo so the no-ORG-grant assertions test the private path."
        )
    if public.get("visibility") != "public":
        raise RuntimeError(
            f"SETUP: {public['full_name']} is not public; point {ENV_PUBLIC_REPO} at a "
            "public repo so the public-repo permission test means something."
        )

    state: dict[str, Any] = {
        "token": github_env["token"],
        "primary_repo": primary,
        "public_repo": public,
        "primary_issues": await list_issues(github_rest, org, primary["name"]),
        "primary_pulls": await list_pulls(github_rest, org, primary["name"]),
        "primary_tree": await get_tree(github_rest, org, primary["name"], primary["default_branch"]),
    }
    if not state["primary_issues"]:
        raise RuntimeError(f"SETUP: {primary['full_name']} has no issues to sync.")

    async with synced_personal_connector(
        pipeshub_client, graph_provider,
        token=github_env["token"], name=connector_name("primary"),
        repo_full_name=primary["full_name"],
    ) as connector_id:
        state["connector_id"] = connector_id
        logger.info("SETUP: personal connector %s scoped to %s", connector_id, primary["full_name"])
        yield state
