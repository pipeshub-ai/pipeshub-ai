# pyright: ignore-file

"""GitLab connector fixtures.

The credentials this needs were provisioned in #3157, which also registered the
``gitlab`` pytest marker and documented how the account must be set up. No suite
followed, so nothing has been reading them.

Read-only. The group and its projects are shared fixtures this suite does not
own, so it creates nothing and deletes nothing there; only the connector and its
graph data are cleaned up.
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

# Set by #3157. env.local.template documents the required scopes (read_api,
# read_user, read_repository) and that the token owner must be Owner or
# Maintainer on the group, because reading project members needs it.
ENV_TOKEN = "GITLAB_TEST_TOKEN"
ENV_INSTANCE_URL = "GITLAB_TEST_INSTANCE_URL"
ENV_GROUP = "GITLAB_TEST_GROUP"
ENV_SUBGROUP = "GITLAB_TEST_SUBGROUP"
ENV_PRIMARY_PROJECT = "GITLAB_TEST_PRIMARY_PROJECT"

REQUIRED = (ENV_TOKEN, ENV_GROUP, ENV_PRIMARY_PROJECT)

# env.local.template: blank or https://gitlab.com for cloud, the host for
# self-managed EE.
DEFAULT_INSTANCE_URL = "https://gitlab.com"


@pytest.fixture(scope="session")
def gitlab_settings() -> dict[str, str]:
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        pytest.skip(f"GitLab credentials not set: {', '.join(missing)}")
    settings = {name: os.environ[name] for name in REQUIRED}
    settings[ENV_INSTANCE_URL] = (
        os.getenv(ENV_INSTANCE_URL, "").strip() or DEFAULT_INSTANCE_URL
    )
    settings[ENV_SUBGROUP] = os.getenv(ENV_SUBGROUP, "")
    return settings


def expected_projects(settings: dict[str, str]) -> list[str]:
    """Projects the sync is expected to reach, from configuration."""
    return [p for p in (settings[ENV_PRIMARY_PROJECT],) if p]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def gitlab_connector(
    gitlab_settings: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    group = gitlab_settings[ENV_GROUP]
    token = gitlab_settings[ENV_TOKEN]

    # authType has to be set explicitly here: the GitLab client defaults to
    # OAUTH (app/sources/client/gitlab/gitlab.py:284), unlike the GitHub client
    # which defaults to API_TOKEN. `credentials` is required even on this path —
    # the client raises on an empty one at line 280, before reaching the branch
    # that never reads it.
    config: dict[str, Any] = {
        "auth": {
            "authType": "API_TOKEN",
            "token": token,
            "instanceUrl": gitlab_settings[ENV_INSTANCE_URL],
        },
        "credentials": {"access_token": token},
        "filters": {
            "sync": {
                "values": {
                    "group_ids": {"operator": "in", "type": "list", "value": [group]}
                }
            }
        },
    }

    connector_name = f"gitlab-lifecycle-test-{uuid.uuid4().hex[:8]}"
    instance = pipeshub_client.create_connector(
        connector_type="GitLab",
        instance_name=connector_name,
        scope="team",
        config=config,
        auth_type="API_TOKEN",
    )
    assert instance.connector_id, "Connector must have a valid ID"

    state: dict[str, Any] = {
        "connector_id": instance.connector_id,
        "connector_name": connector_name,
        "resource_name": group,
        "group": group,
        "subgroup": gitlab_settings[ENV_SUBGROUP],
        "instance_url": gitlab_settings[ENV_INSTANCE_URL],
        "expected_projects": expected_projects(gitlab_settings),
    }

    # pytest runs no teardown for a fixture that fails before yielding, so a
    # timeout here would otherwise leave the connector enabled with its data.
    try:
        pipeshub_client.toggle_sync(instance.connector_id, enable=True)

        async def _any_records() -> bool:
            return await graph_provider.count_records(instance.connector_id) > 0

        # How much the group holds is not knowable from here, and GitLab is
        # slower than a container on the same network, so this waits for any
        # record rather than a count it cannot predict.
        await wait_until_graph_condition(
            instance.connector_id,
            check=_any_records,
            timeout=900,
            poll_interval=15,
            description="GitLab full sync",
        )
        state["full_sync_count"] = await graph_provider.count_records(
            instance.connector_id
        )
    except BaseException:
        await destructor(
            _NoOpStorage(), pipeshub_client, graph_provider, state,
            connector_type="GitLab",
        )
        raise

    yield state

    await destructor(
        _NoOpStorage(), pipeshub_client, graph_provider, state, connector_type="GitLab"
    )


class _NoOpStorage:
    """Satisfies the destructor's storage protocol without touching GitLab.

    Suites that own their source clear it on teardown. This one syncs from a
    group it does not own, so clearing is deliberately nothing.
    """

    def clear_objects(self, resource_name: str) -> None:
        del resource_name
