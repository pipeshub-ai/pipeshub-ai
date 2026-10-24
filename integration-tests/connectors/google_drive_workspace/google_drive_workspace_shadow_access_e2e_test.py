"""Drive (Workspace): the creator reaches what the admin account they connected reaches, through that link only."""

import logging
import os
from typing import Any

import pytest

from helper.second_user import SecondUser, second_user  # noqa: F401 - fixture
from helper.shadow_access_e2e import verify_shadow_access

logger = logging.getLogger("drive-workspace-shadow-access-e2e")

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "1200"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]


class TestDriveWorkspaceShadowAccess:
    async def test_creator_reaches_records_only_through_linked_account(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        pipeshub_client,
        graph_provider,
        config_service,
        vector_store,
        second_user: SecondUser,  # noqa: F811
    ) -> None:
        report = await verify_shadow_access(
            connector_id=drive_workspace_entity_connector["connector_id"],
            pipeshub_client=pipeshub_client,
            graph_provider=graph_provider,
            config_service=config_service,
            vector_store=vector_store,
            second_user=second_user,
            sync_timeout=_SYNC_TIMEOUT_SEC,
        )
        logger.info("Shadow access: %s", report.summary())
