"""Turning on a connector that signed in without a linked OAuth app says why it failed.

Google Drive (personal) reads its client ID and secret only from a linked OAuth app.
A connector saved with client credentials written on the connector itself can still
complete sign-in, because the sign-in flow falls back to those credentials, but its
``init()`` then has no app to read. Turning it on used to answer with the generic
"Failed to initialize connector" and leave the caller guessing.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.connectors.api import router as router_module
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)
from fastapi import HTTPException

CONNECTOR_ID = "drive-conn-1"
ORG_ID = "org-1"

# What the full-config save leaves behind: client credentials on the connector,
# tokens from a successful sign-in, and no oauthConfigId.
SIGNED_IN_WITHOUT_OAUTH_APP = {
    "auth": {
        "authType": "OAUTH",
        "clientId": "client-id.apps.googleusercontent.com",
        "clientSecret": "client-secret",
        "connectorScope": "personal",
    },
    "credentials": {"access_token": "access", "refresh_token": "refresh"},
}


def _drive_connector(config_service: AsyncMock) -> GoogleDriveIndividualConnector:
    processor = MagicMock()
    processor.org_id = ORG_ID
    with patch("app.connectors.sources.google.drive.individual.connector.SyncPoint"):
        return GoogleDriveIndividualConnector(
            logger=logging.getLogger("test_router_init_oauth_app_missing"),
            data_entities_processor=processor,
            data_store_provider=MagicMock(),
            config_service=config_service,
            connector_id=CONNECTOR_ID,
            scope="personal",
            created_by="user-1",
        )


@pytest.mark.asyncio
async def test_turning_on_drive_without_an_oauth_app_names_the_missing_app() -> None:
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value=SIGNED_IN_WITHOUT_OAUTH_APP)
    connector = _drive_connector(config_service)

    container = MagicMock()
    container.connectors_map = {}
    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(
        return_value={"scope": "personal", "createdBy": "user-1", "orgId": ORG_ID, "name": "My Drive"}
    )

    with patch.object(router_module, "resolve_config_service", return_value=config_service), \
         patch.object(router_module, "build_graph_data_store", return_value=MagicMock()), \
         patch.object(router_module, "get_data_entities_processor_cls", return_value=MagicMock()), \
         patch.object(router_module.ConnectorFactory, "create_connector", AsyncMock(return_value=connector)):
        with pytest.raises(HTTPException) as caught:
            await router_module._build_and_store_connector(
                container,
                CONNECTOR_ID,
                "DRIVE",
                MagicMock(),
                graph_provider,
                "user-1",
                ORG_ID,
                is_admin=True,
                logger=logging.getLogger("test_router_init_oauth_app_missing"),
            )

    assert caught.value.status_code == 400
    assert "not linked to an OAuth app" in caught.value.detail
    assert "choose or create an OAuth app" in caught.value.detail
    assert CONNECTOR_ID not in container.connectors_map
