"""Shared fixtures for the strict OpenAPI audit of /api/v1/artifacts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Iterator

import pytest
import pytest_asyncio

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from artifacts_audit_support import (  # noqa: E402
    UNRELATED_SCOPE,
    ArtifactsClient,
    SeededArtifact,
    bearer,
    oauth_token_with_scopes,
    remove_artifact,
    seed_artifact,
)

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol


@pytest.fixture(scope="session")
def artifacts_client(pipeshub_client: PipeshubClient) -> ArtifactsClient:
    return ArtifactsClient(pipeshub_client)


@pytest.fixture(scope="session")
def unscoped_headers(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Headers of an OAuth token for the admin's org that carries neither kb:read nor connector:read."""
    with oauth_token_with_scopes(
        pipeshub_client.base_url, [UNRELATED_SCOPE], pipeshub_client.timeout_seconds
    ) as token:
        yield bearer(token)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def seeded_artifact(
    graph_provider: "GraphProviderProtocol", pipeshub_client: PipeshubClient
) -> AsyncGenerator[SeededArtifact, None]:
    """One visible DOCUMENT artifact with two versions, owned by the shared admin.

    Nobody else holds a permission edge on it, so ``second_user`` gets a 404.
    """
    admin_id = pipeshub_client.acting_user_id
    if not admin_id:
        pytest.fail("admin access token carries no user identity")
    admin = await graph_provider.get_user_by_user_id(admin_id)
    owner_key = (admin or {}).get("_key") or (admin or {}).get("id")
    if not owner_key:
        pytest.fail(f"admin user {admin_id} is not in the graph, cannot own an artifact")

    seeded = await seed_artifact(
        graph_provider, org_id=pipeshub_client.org_id, owner_key=owner_key
    )
    try:
        yield seeded
    finally:
        await remove_artifact(graph_provider, seeded)
