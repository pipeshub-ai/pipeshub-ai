"""Confluence: the creator reaches what the Atlassian account they connected reaches, through that link only."""

import logging
import os
from typing import Any

import pytest

from helper.second_user import SecondUser, second_user  # noqa: F401 - fixture
from helper.shadow_access_e2e import verify_lent_link_grants_access, verify_shadow_access

logger = logging.getLogger("confluence-shadow-access-e2e")

_SYNC_TIMEOUT_SEC = int(os.getenv("CONFLUENCE_SHADOW_E2E_SYNC_TIMEOUT", "900"))


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceShadowAccess:
    async def test_creator_reaches_records_only_through_linked_account(
        self,
        confluence_connector: dict[str, Any],
        pipeshub_client,
        graph_provider,
        config_service,
        vector_store,
        second_user: SecondUser,  # noqa: F811
    ) -> None:
        report = await verify_shadow_access(
            connector_id=confluence_connector["connector_id"],
            pipeshub_client=pipeshub_client,
            graph_provider=graph_provider,
            config_service=config_service,
            vector_store=vector_store,
            second_user=second_user,
            sync_timeout=_SYNC_TIMEOUT_SEC,
        )
        logger.info("Shadow access: %s", report.summary())

    async def test_lent_link_alone_grants_access(
        self,
        confluence_connector: dict[str, Any],
        graph_provider,
        config_service,
        second_user: SecondUser,  # noqa: F811
    ) -> None:
        summary = await verify_lent_link_grants_access(
            connector_id=confluence_connector["connector_id"],
            graph_provider=graph_provider,
            config_service=config_service,
            second_user=second_user,
        )
        logger.info("Lent link: %s", summary)
