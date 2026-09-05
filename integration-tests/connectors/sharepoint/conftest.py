# pyright: ignore-file

"""SharePoint Online connector fixtures.

The credentials for this suite are already configured in CI — seven
SHAREPOINT_TEST_* secrets are exported to the integration job — but nothing has
been using them. This connects them to a test.

Unlike the self-hosted connectors, the source here is a real Microsoft 365
tenant that these tests do not own and must not modify. Everything below is
read-only: the connector syncs, and the assertions check the shape of what came
back rather than specific documents, because the tenant's contents can change
without warning and a test that hard-codes them would fail for the wrong reason.
"""

import os
import uuid
from typing import Any, AsyncGenerator, Dict, List

import pytest
import pytest_asyncio

from connector_lifecycle import destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_until_graph_condition

# The secrets are named ...FILE_CONTENT, but the workflow exports them as
# ...FILE. These are the environment variable names, not the secret names.
REQUIRED_ENV = (
    "SHAREPOINT_TEST_CLIENT_ID",
    "SHAREPOINT_TEST_TENANT_ID",
    "SHAREPOINT_TEST_SHAREPOINT_DOMAIN",
    "SHAREPOINT_TEST_CERTIFICATE_FILE",
    "SHAREPOINT_TEST_PRIVATE_KEY_FILE",
)


def expected_site_names() -> List[str]:
    """Sites the tenant is expected to expose, from SHAREPOINT_TEST_SITE_NAMES."""
    raw = os.getenv("SHAREPOINT_TEST_SITE_NAMES", "")
    return [name.strip() for name in raw.replace("\n", ",").split(",") if name.strip()]


@pytest.fixture(scope="session")
def sharepoint_credentials() -> Dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.skip(f"SharePoint credentials not set: {', '.join(missing)}")
    return {name: os.environ[name] for name in REQUIRED_ENV}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def sharepoint_connector(
    sharepoint_credentials: Dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    config = {
        "auth": {
            "clientId": sharepoint_credentials["SHAREPOINT_TEST_CLIENT_ID"],
            "tenantId": sharepoint_credentials["SHAREPOINT_TEST_TENANT_ID"],
            "sharepointDomain": sharepoint_credentials[
                "SHAREPOINT_TEST_SHAREPOINT_DOMAIN"
            ],
            "hasAdminConsent": True,
            "certificate": sharepoint_credentials[
                "SHAREPOINT_TEST_CERTIFICATE_FILE"
            ],
            "privateKey": sharepoint_credentials[
                "SHAREPOINT_TEST_PRIVATE_KEY_FILE"
            ],
        }
    }

    connector_name = f"sharepoint-lifecycle-test-{uuid.uuid4().hex[:8]}"
    instance = pipeshub_client.create_connector(
        connector_type="SharePointOnline",
        instance_name=connector_name,
        scope="team",
        config=config,
        auth_type="OAUTH_ADMIN_CONSENT",
    )
    assert instance.connector_id, "Connector must have a valid ID"

    state: Dict[str, Any] = {
        "connector_id": instance.connector_id,
        "connector_name": connector_name,
        # Nothing is written to the tenant, so there is nothing to clear. The
        # destructor still needs a name for its log line.
        "resource_name": sharepoint_credentials["SHAREPOINT_TEST_SHAREPOINT_DOMAIN"],
        "expected_site_names": expected_site_names(),
    }

    pipeshub_client.toggle_sync(instance.connector_id, enable=True)

    # A real tenant is slower than a container in the same network, and how much
    # is there is not known ahead of time. Wait for the sync to produce anything
    # at all rather than for a count this suite cannot predict.
    async def _any_records() -> bool:
        return await graph_provider.count_records(instance.connector_id) > 0

    await wait_until_graph_condition(
        instance.connector_id,
        check=_any_records,
        timeout=900,
        poll_interval=15,
        description="SharePoint full sync",
    )
    state["full_sync_count"] = await graph_provider.count_records(
        instance.connector_id
    )

    yield state

    await destructor(
        _NoOpStorage(),
        pipeshub_client,
        graph_provider,
        state,
        connector_type="SharePointOnline",
    )


class _NoOpStorage:
    """Satisfies the destructor's storage protocol without touching the tenant.

    Every other suite owns its source and clears it on teardown. This one syncs
    from a Microsoft 365 tenant it does not own, so clearing is deliberately a
    no-op — the connector and its graph data are still removed.
    """

    def clear_objects(self, resource_name: str) -> None:
        del resource_name
