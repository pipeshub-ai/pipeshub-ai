"""Turning on a connector that signed in without a linked OAuth app says why it failed.

Google Drive (personal) reads its client ID and secret only from a linked OAuth app.
A connector saved with client credentials written on the connector itself can still
complete sign-in, because the sign-in flow falls back to those credentials, but its
``init()`` then has no app to read. Turning it on used to answer with the generic
"Failed to initialize connector" and leave the caller guessing.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError
from app.connectors.api import router as router_module
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)
from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector
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


@pytest.mark.asyncio
async def test_a_failed_access_check_after_init_answers_400_with_its_reason() -> None:
    # S3 and MinIO raise ConnectorInitError from test_connection_and_access, outside init().
    reason = "S3: Could not list objects in bucket 'b': AccessDenied."
    connector = MagicMock()
    connector.init = AsyncMock(return_value=True)
    connector.test_connection_and_access = AsyncMock(side_effect=ConnectorInitError(reason))
    connector.cleanup = AsyncMock()
    container = MagicMock()
    container.connectors_map = {}
    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(
        return_value={"scope": "team", "createdBy": "user-1", "orgId": ORG_ID, "name": "Buckets"}
    )

    with patch.object(router_module, "resolve_config_service", return_value=AsyncMock()), \
         patch.object(router_module, "build_graph_data_store", return_value=MagicMock()), \
         patch.object(router_module, "get_data_entities_processor_cls", return_value=MagicMock()), \
         patch.object(router_module.ConnectorFactory, "create_connector", AsyncMock(return_value=connector)):
        with pytest.raises(HTTPException) as caught:
            await router_module._build_and_store_connector(
                container, CONNECTOR_ID, "S3", MagicMock(), graph_provider, "user-1", ORG_ID,
                is_admin=True, logger=logging.getLogger("test_router_init_oauth_app_missing"),
            )

    assert caught.value.status_code == 400
    assert caught.value.detail == reason
    connector.cleanup.assert_awaited_once()
    assert CONNECTOR_ID not in container.connectors_map


async def _turn_on(connector: MagicMock) -> HTTPException:
    container = MagicMock()
    container.connectors_map = {}
    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(
        return_value={"scope": "team", "createdBy": "user-1", "orgId": ORG_ID, "name": "Sites"}
    )
    with patch.object(router_module, "resolve_config_service", return_value=AsyncMock()), \
         patch.object(router_module, "build_graph_data_store", return_value=MagicMock()), \
         patch.object(router_module, "get_data_entities_processor_cls", return_value=MagicMock()), \
         patch.object(router_module.ConnectorFactory, "create_connector", AsyncMock(return_value=connector)):
        with pytest.raises(HTTPException) as caught:
            await router_module._build_and_store_connector(
                container, CONNECTOR_ID, "SHAREPOINT ONLINE", MagicMock(), graph_provider, "user-1", ORG_ID,
                is_admin=True, logger=logging.getLogger("test_router_init_oauth_app_missing"),
            )
    assert CONNECTOR_ID not in container.connectors_map
    return caught.value


@pytest.mark.asyncio
async def test_an_unexpected_init_failure_answers_500_and_releases_the_connector() -> None:
    connector = MagicMock()
    connector.init = AsyncMock(side_effect=ValueError("token request failed"))
    connector.cleanup = AsyncMock()

    error = await _turn_on(connector)

    assert error.status_code == 500
    connector.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_init_failure_with_a_reason_answers_400_and_releases_the_connector() -> None:
    reason = "Upload the certificate to the app registration."
    connector = MagicMock()
    connector.init = AsyncMock(side_effect=ConnectorInitError(reason))
    connector.cleanup = AsyncMock()

    error = await _turn_on(connector)

    assert error.status_code == 400
    assert error.detail == reason
    connector.cleanup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", [
    {"adminEmail": "admin@example.com", "serviceAccountJson": json.dumps({"type": "service_account",
                                                                           "client_id": "1234"})},
    {"adminEmail": "admin@example.com", "type": "service_account", "client_id": "1234"},
], ids=["uploaded key", "legacy flattened key"])
async def test_gmail_workspace_without_delegation_answers_400_naming_delegation(auth: dict) -> None:
    with patch("app.connectors.sources.google.gmail.team.connector.SyncPoint"):
        connector = GoogleGmailTeamConnector(
            logger=logging.getLogger("test_router_init_oauth_app_missing"),
            data_entities_processor=MagicMock(org_id=ORG_ID),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id=CONNECTOR_ID,
            scope="team",
            created_by="user-1",
        )
    connector.init = AsyncMock(return_value=True)
    connector.cleanup = AsyncMock()
    connector.config = {"credentials": {"auth": auth}}
    connector.admin_client, connector.gmail_client = MagicMock(), MagicMock()
    connector.gmail_data_source = AsyncMock()
    connector.admin_data_source = AsyncMock()
    connector.admin_data_source.users_get = AsyncMock(side_effect=RefreshError(
        "unauthorized_client: Client is unauthorized to retrieve access tokens using this method.",
        {"error": "unauthorized_client",
         "error_description": "Client is unauthorized to retrieve access tokens using this method."},
    ))

    error = await _turn_on(connector)

    assert error.status_code == 400
    assert "Domain-wide delegation" in error.detail
    assert "(1234)" in error.detail
    connector.cleanup.assert_awaited_once()
