# pyright: ignore-file

"""
SharePoint Online Connector – Integration Tests
===============================================

Tests receive a fully set-up connector via the ``sharepoint_connector`` fixture
(defined in conftest.py), which authenticates against the configured Microsoft
365 tenant, runs a full sync, and removes the connector afterwards.

Scope of these tests
--------------------
The source is a real tenant these tests do not own. They are read-only and
content-agnostic: nothing is written, and nothing asserts that a particular
document exists. A test that hard-coded the tenant's contents would fail
whenever somebody edited a file there, which is noise rather than a connector
regression.

What that leaves is still the failure mode that matters most for this connector.
SharePoint breaks when Microsoft changes something — a certificate expires, an
admin-consent grant is revoked, a Graph endpoint is retired, a permission scope
is renamed. None of that shows up in a unit test, and none of it involves any
change to this repository. These tests fail when it happens.

Test cases:
  TC-AUTH-001  — Certificate authentication against the tenant succeeds and syncs
  TC-GRAPH-001 — The synced graph is coherent: record groups, edges, no orphans
  TC-PERM-001  — Permissions are synced, not just content
  TC-SITES-001 — The sites named in configuration are among what was synced
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from helper.graph_provider import GraphProviderProtocol  # noqa: E402

logger = logging.getLogger("sharepoint-lifecycle-test")


@pytest.mark.integration
@pytest.mark.sharepoint
@pytest.mark.asyncio(loop_scope="session")
class TestSharePointConnector:
    """Read-only coverage for the SharePoint Online connector."""

    @pytest.mark.order(1)
    async def test_tc_auth_001_certificate_auth_and_sync(
        self,
        sharepoint_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-AUTH-001: The connector authenticates and syncs something.

        Reaching this point already proves a good deal: the certificate and
        private key were accepted, admin consent is still granted, and the Graph
        endpoints the connector calls still answer. Those are what break without
        any change on our side.
        """
        connector_id = sharepoint_connector["connector_id"]
        full_count = sharepoint_connector["full_sync_count"]

        assert full_count > 0, (
            "TC-AUTH-001: the sync produced no records. Either the credentials "
            "were rejected, admin consent was revoked, or the tenant is empty."
        )
        await graph_provider.assert_min_records(connector_id, 1)
        logger.info(
            "TC-AUTH-001 passed: %d records synced from the tenant (connector %s)",
            full_count,
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_graph_001_synced_graph_is_coherent(
        self,
        sharepoint_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GRAPH-001: Records are attached to groups, with nothing orphaned.

        A record with no group or app edge is invisible to search even though it
        was indexed, which looks to a user like the document was never synced.
        """
        connector_id = sharepoint_connector["connector_id"]

        await graph_provider.assert_record_groups_and_edges(
            connector_id, min_groups=1, min_record_edges=1
        )
        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info("TC-GRAPH-001 passed: graph is coherent for %s", connector_id)

    @pytest.mark.order(3)
    async def test_tc_perm_001_permissions_are_synced(
        self,
        sharepoint_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-PERM-001: Permission edges exist.

        SharePoint content is access-controlled, and this connector maps that
        into the graph. If permission sync silently stopped, content would still
        appear to index correctly while access decisions were made on nothing —
        the kind of failure worth catching before a customer does.
        """
        connector_id = sharepoint_connector["connector_id"]

        edges = await graph_provider.count_permission_edges(connector_id)
        assert edges > 0, (
            "TC-PERM-001: no permission edges were created. Content synced but "
            "its access control did not, so permissions are being evaluated "
            "against nothing."
        )
        logger.info("TC-PERM-001 passed: %d permission edges", edges)

    @pytest.mark.order(4)
    async def test_tc_sites_001_configured_sites_were_synced(
        self,
        sharepoint_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SITES-001: The sites named in configuration appear in the sync.

        Skipped when SHAREPOINT_TEST_SITE_NAMES is not set, because without it
        there is nothing specific to check and the earlier cases already cover
        that the sync worked.
        """
        expected = sharepoint_connector["expected_site_names"]
        if not expected:
            pytest.skip("SHAREPOINT_TEST_SITE_NAMES is not set")

        connector_id = sharepoint_connector["connector_id"]
        names = await graph_provider.fetch_record_names(connector_id)
        haystack = " ".join(names).lower()

        missing = [site for site in expected if site.lower() not in haystack]
        assert not missing, (
            f"TC-SITES-001: configured sites {missing} produced no records. "
            f"Synced {len(names)} records from other sites, so authentication "
            "worked but these sites were not reached — check the app's "
            "site-level permissions."
        )
        logger.info("TC-SITES-001 passed: sites %s are present", expected)
