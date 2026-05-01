# pyright: ignore-file

"""
SharePoint Online Connector – Integration Tests
================================================

Uses module-scoped ``sharepoint_connector`` fixture: certificate auth from env file paths,
``site_ids`` sync filter for three named sites, full sync via Pipeshub.

Note: SharePoint connector syncs **tenant-wide** Microsoft 365 users before applying the
site filter; app-user assertions reflect directory users linked to the connector app.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from helper.graph_provider import GraphProviderProtocol  # noqa: E402

logger = logging.getLogger("sharepoint-integration-test")

# Matches ``RecordGroupType.SHAREPOINT_SITE`` in ``app.models.entities``.
SHAREPOINT_SITE_GROUP_TYPE = "SHAREPOINT_SITE"

# Comma-separated override: SHAREPOINT_TEST_EXPECTED_USER_EMAILS=a@x.com,b@y.com
_DEFAULT_EXPECTED_APP_USER_EMAILS: List[str] = [
    "testuser1@pipeshubinc.onmicrosoft.com",
    "testuser2@pipeshubinc.onmicrosoft.com",
]


def _expected_app_user_emails() -> List[str]:
    raw = os.getenv("SHAREPOINT_TEST_EXPECTED_USER_EMAILS")
    if raw:
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return [e.lower() for e in _DEFAULT_EXPECTED_APP_USER_EMAILS]


def _expected_sharepoint_site_record_group_count() -> int:
    """Default 3 (Test Site A/B/C); override with SHAREPOINT_TEST_EXPECTED_SITE_RG_COUNT."""
    raw = os.getenv("SHAREPOINT_TEST_EXPECTED_SITE_RG_COUNT")
    if raw and raw.strip().isdigit():
        return int(raw.strip())
    return 3


@pytest.mark.integration
@pytest.mark.sharepoint
@pytest.mark.asyncio(loop_scope="session")
class TestSharePointConnector:
    """Integration tests for SharePoint Online connector."""

    async def test_sharepoint_sync_app_users(
        self,
        sharepoint_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """App has at least two linked users, including configured test mailboxes."""
        connector_id = sharepoint_connector["connector_id"]
        expected_emails = _expected_app_user_emails()

        user_count = await graph_provider.count_app_users(connector_id)
        logger.info("App users for connector %s: %d", connector_id, user_count)
        assert user_count >= 2, (
            f"Expected at least 2 users linked to the app for connector {connector_id}, "
            f"got {user_count}"
        )

        for email in expected_emails:
            linked = await graph_provider.app_user_linked_by_email(connector_id, email)
            assert linked, (
                f"Expected User linked to app for connector {connector_id} with email {email!r}"
            )

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("Graph summary (users test): %s", summary)

    async def test_sharepoint_sync_site_record_groups(
        self,
        sharepoint_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """Exactly three site-level record groups (``SHAREPOINT_SITE``); names match filter sites."""
        connector_id = sharepoint_connector["connector_id"]
        site_names = sharepoint_connector["site_display_names"]
        expected_site_rgs = _expected_sharepoint_site_record_group_count()

        site_type_count = await graph_provider.count_record_groups_by_type(
            connector_id, SHAREPOINT_SITE_GROUP_TYPE
        )
        assert site_type_count == expected_site_rgs, (
            f"Expected exactly {expected_site_rgs} RecordGroup(s) with groupType "
            f"{SHAREPOINT_SITE_GROUP_TYPE!r} for connector {connector_id}, got {site_type_count}"
        )

        group_names = await graph_provider.fetch_record_group_names(connector_id)
        logger.info(
            "RecordGroup names for connector %s (%d groups): %s",
            connector_id,
            len(group_names),
            group_names,
        )
        for expected in site_names:
            assert expected in group_names, (
                f"Expected RecordGroup named {expected!r} for connector {connector_id}; "
                f"have: {group_names!r}"
            )

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("Graph summary (record groups test): %s", summary)
