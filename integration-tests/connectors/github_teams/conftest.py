# pyright: ignore-file

"""GitHub Teams connector fixtures.

The credentials this needs were provisioned in #3157, which also registered the
``github_teams`` pytest marker and documented how the account must be set up.
No suite followed, so nothing has been reading them.

Read-only. The organisation and its repositories are shared fixtures that this
suite does not own, so it creates nothing and deletes nothing there; only the
connector and its graph data are cleaned up.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import destructor
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_until_graph_condition
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

# Set by #3157. env.local.template documents the required scopes and, more
# importantly, that the token owner needs PUSH access on every repository: the
# connector reads /collaborators to resolve permissions, and without push it
# silently falls back to the repository's visibility instead.
ENV_TOKEN = "GH_TEAMS_TEST_TOKEN"
ENV_ORG = "GH_TEAMS_TEST_ORG"
ENV_PRIMARY_REPO = "GH_TEAMS_TEST_PRIMARY_REPO"
ENV_PUBLIC_REPO = "GH_TEAMS_TEST_PUBLIC_REPO"

REQUIRED = (ENV_TOKEN, ENV_ORG, ENV_PRIMARY_REPO)


@pytest.fixture(scope="session")
def github_teams_settings() -> dict[str, str]:
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        pytest.skip(f"GitHub Teams credentials not set: {', '.join(missing)}")
    settings = {name: os.environ[name] for name in REQUIRED}
    settings[ENV_PUBLIC_REPO] = os.getenv(ENV_PUBLIC_REPO, "")
    return settings


def expected_repos(settings: dict[str, str]) -> list[str]:
    """Repositories the sync is expected to reach, from configuration."""
    names = [settings[ENV_PRIMARY_REPO]]
    if settings.get(ENV_PUBLIC_REPO):
        names.append(settings[ENV_PUBLIC_REPO])
    return [n for n in names if n]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def github_teams_connector(
    github_teams_settings: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    org = github_teams_settings[ENV_ORG]

    # authType selects the branch in app/sources/client/github/github.py:192.
    # `credentials` is required even here: the client raises on an empty one at
    # line 189, before it reaches the API_TOKEN branch that never reads it.
    config: dict[str, Any] = {
        "auth": {
            "authType": "API_TOKEN",
            "token": github_teams_settings[ENV_TOKEN],
        },
        "credentials": {"access_token": github_teams_settings[ENV_TOKEN]},
        "filters": {
            "sync": {
                "values": {
                    "org_ids": {"operator": "in", "type": "list", "value": [org]}
                }
            }
        },
    }

    connector_name = f"github-teams-lifecycle-test-{uuid.uuid4().hex[:8]}"
    instance = pipeshub_client.create_connector(
        connector_type="GithubTeams",
        instance_name=connector_name,
        scope="team",
        config=config,
        auth_type="API_TOKEN",
    )
    assert instance.connector_id, "Connector must have a valid ID"

    state: dict[str, Any] = {
        "connector_id": instance.connector_id,
        "connector_name": connector_name,
        "resource_name": org,
        "org": org,
        "expected_repos": expected_repos(github_teams_settings),
    }

    # Anything between here and the yield can raise, and pytest runs no teardown
    # for a fixture that fails before yielding — which would leave the connector
    # enabled with its graph data behind.
    try:
        pipeshub_client.toggle_sync(instance.connector_id, enable=True)

        async def _any_records() -> bool:
            return await graph_provider.count_records(instance.connector_id) > 0

        # How much is in the organisation is not knowable from here, and GitHub
        # is slower than a container on the same network, so this waits for the
        # sync to produce anything rather than for a count it cannot predict.
        await wait_until_graph_condition(
            instance.connector_id,
            check=_any_records,
            timeout=900,
            poll_interval=15,
            description="GitHub Teams full sync",
        )
        state["full_sync_count"] = await graph_provider.count_records(
            instance.connector_id
        )
    except BaseException:
        await destructor(
            _NoOpStorage(), pipeshub_client, graph_provider, state,
            connector_type="GithubTeams",
        )
        raise

    yield state

    await destructor(
        _NoOpStorage(), pipeshub_client, graph_provider, state,
        connector_type="GithubTeams",
    )


class _NoOpStorage:
    """Satisfies the destructor's storage protocol without touching GitHub.

    Suites that own their source clear it on teardown. This one syncs from an
    organisation it does not own, so clearing is deliberately nothing.
    """

    def clear_objects(self, resource_name: str) -> None:
        del resource_name
