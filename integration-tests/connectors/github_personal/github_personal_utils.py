# pyright: ignore-file

"""Helpers for the GitHub (personal) connector integration tests."""

import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest

from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.oauth_token_helper import inject_access_token  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.github_teams.constants import (  # type: ignore[import-not-found]
    ENV_ORG,
    ENV_PRIMARY_REPO,
    ENV_PUBLIC_REPO,
    ENV_TOKEN,
    GH_IT_RUN_ID,
    GH_SYNC_WAIT_SEC,
)
from connectors.github_teams.github_test_utils import (  # type: ignore[import-not-found]
    list_filter,
    sync_filters,
    teardown_connector,
)


def require_env() -> dict[str, str]:
    values = {
        "token": os.getenv(ENV_TOKEN, ""),
        "org": os.getenv(ENV_ORG, ""),
        "primary": os.getenv(ENV_PRIMARY_REPO, ""),
        "public": os.getenv(ENV_PUBLIC_REPO, ""),
    }
    missing = [k for k, v in values.items() if not v]
    if missing:
        pytest.skip(
            f"GitHub credentials/config not set (missing: {', '.join(sorted(missing))}). "
            f"Required: {ENV_TOKEN}, {ENV_ORG}, {ENV_PRIMARY_REPO}, {ENV_PUBLIC_REPO}."
        )
    return values


# Exact registry name: the lookup is a plain dict hit with no normalization.
CONNECTOR_TYPE = "Github"


def connector_name(kind: str) -> str:
    return f"github-personal-{kind}-{GH_IT_RUN_ID}-{uuid.uuid4().hex[:6]}"


def create_personal_connector(
    pipeshub_client: PipeshubClient, *, token: str, name: str, repo_full_name: str,
) -> str:
    """Register a personal GitHub connector over one repository and inject the PAT.

    ``config`` must be non-empty for the create route to persist the ``auth`` block.
    """
    instance = pipeshub_client.create_connector(
        connector_type=CONNECTOR_TYPE,
        instance_name=name,
        scope="personal",
        config={
            "auth": {},
            "filters": sync_filters(repo_ids=list_filter("in", [repo_full_name])),
        },
        auth_type="OAUTH",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    inject_access_token(instance.connector_id, token)
    return instance.connector_id


@asynccontextmanager
async def synced_personal_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    *,
    token: str,
    name: str,
    repo_full_name: str,
) -> AsyncIterator[str]:
    """Create, enable and wait for one sync; always tear down."""
    connector_id = create_personal_connector(
        pipeshub_client, token=token, name=name, repo_full_name=repo_full_name,
    )
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        instance = pipeshub_client.get_connector(connector_id)
        assert (instance.get("connector") or instance).get("isAuthenticated") is not False, (
            "Connector did not authenticate with the injected token — check the PAT's "
            "scopes and that SECRET_KEY matches the backend."
        )
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=1, timeout=GH_SYNC_WAIT_SEC,
        )
        yield connector_id
    finally:
        await teardown_connector(pipeshub_client, graph_provider, connector_id)
