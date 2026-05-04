# pyright: ignore-file

"""SharePoint Online connector fixtures for integration tests."""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.sources.external.microsoft.sharepoint.sharepoint import (  # noqa: E402
    SharePointDataSource,
    sharepoint_build_graph_client_from_certificate_text,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    async_wait_for_stable_record_count,
    wait_until_graph_condition,
)
from pipeshub_client import PipeshubClient  # noqa: E402

logger = logging.getLogger("sharepoint-conftest")


def _default_site_display_names() -> List[str]:
    raw = os.getenv(
        "SHAREPOINT_TEST_SITE_NAMES",
        "Test Site A,Test Site B,Test Site C",
    )
    return [p.strip() for p in raw.split(",") if p.strip()]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def sharepoint_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped SharePoint connector with cert auth, site_ids filter, and full lifecycle."""
    tenant_id = os.getenv("SHAREPOINT_TEST_TENANT_ID")
    client_id = os.getenv("SHAREPOINT_TEST_CLIENT_ID")
    sharepoint_domain = os.getenv("SHAREPOINT_TEST_SHAREPOINT_DOMAIN")
    cert_text = os.getenv("SHAREPOINT_TEST_CERTIFICATE")
    key_text = os.getenv("SHAREPOINT_TEST_PRIVATE_KEY")

    missing = [
        name
        for name, val in (
            ("SHAREPOINT_TEST_TENANT_ID", tenant_id),
            ("SHAREPOINT_TEST_CLIENT_ID", client_id),
            ("SHAREPOINT_TEST_SHAREPOINT_DOMAIN", sharepoint_domain),
            ("SHAREPOINT_TEST_CERTIFICATE", cert_text),
            ("SHAREPOINT_TEST_PRIVATE_KEY", key_text),
        )
        if not val
    ]
    if missing:
        pytest.skip(
            "SharePoint integration credentials not set: " + ", ".join(missing),
        )

    site_names = _default_site_display_names()
    holder: Any = None
    connector_id: str | None = None

    try:
        holder = sharepoint_build_graph_client_from_certificate_text(
            tenant_id=tenant_id,
            client_id=client_id,
            certificate=cert_text,
            private_key=key_text,
        )
        datasource = SharePointDataSource(holder.client)
        site_graph_ids = await datasource.integration_resolve_site_graph_ids_by_display_names(
            site_names,
            exclude_onedrive_sites=True,
        )

        connector_name = f"sharepoint-test-{uuid.uuid4().hex[:8]}"
        config = {
            "auth": {
                "tenantId": tenant_id,
                "clientId": client_id,
                "sharepointDomain": sharepoint_domain,
                "hasAdminConsent": True,
                "certificate": cert_text,
                "privateKey": key_text,
            },
            "filters": {
                "sync": {
                    "values": {
                        "site_ids": {
                            "operator": "in",
                            "type": "list",
                            "value": site_graph_ids,
                        }
                    }
                }
            },
        }

        instance = pipeshub_client.create_connector(
            connector_type="SharePoint Online",
            instance_name=connector_name,
            scope="team",
            config=config,
            auth_type="OAUTH_ADMIN_CONSENT",
        )
        connector_id = instance.connector_id

        pipeshub_client.toggle_sync(connector_id, enable=True)

        sync_timeout = int(os.getenv("INTEGRATION_SHAREPOINT_SYNC_TIMEOUT", "600"))

        async def _graph_ready() -> bool:
            assert connector_id is not None
            users = await graph_provider.count_app_users(connector_id)
            groups = await graph_provider.count_record_groups(connector_id)
            return users >= 2 and groups >= len(site_names)

        await wait_until_graph_condition(
            connector_id,
            check=_graph_ready,
            timeout=sync_timeout,
            poll_interval=15,
            description="SharePoint sync (users>=2 and site record groups)",
        )

        stable_records = await async_wait_for_stable_record_count(
            graph_provider,
            connector_id,
            stability_checks=3,
            interval=10,
            max_rounds=30,
        )

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(5)
        pipeshub_client.toggle_sync(connector_id, enable=True)
        verified = await async_wait_for_stable_record_count(
            graph_provider,
            connector_id,
            stability_checks=3,
            interval=10,
            max_rounds=30,
        )
        if verified != stable_records:
            logger.info(
                "SETUP: verification sync record count %s -> %s",
                stable_records,
                verified,
            )

        state: Dict[str, Any] = {
            "connector_id": connector_id,
            "connector_name": connector_name,
            "site_display_names": site_names,
            "site_graph_ids": site_graph_ids,
            "full_sync_record_count": verified,
            "graph_holder": holder,
        }
        yield state
    finally:
        if connector_id:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
                pipeshub_client.get_connector_status(connector_id)
            except Exception as exc:
                logger.warning("TEARDOWN: disable connector failed: %s", exc)
            try:
                pipeshub_client.delete_connector(connector_id)
                pipeshub_client.wait(25)
                cleanup_timeout = int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300"))
                await graph_provider.assert_all_records_cleaned(
                    connector_id, timeout=cleanup_timeout
                )
            except Exception as exc:
                logger.warning("TEARDOWN: delete/cleanup failed: %s", exc)
        if holder is not None:
            await holder.aclose()
