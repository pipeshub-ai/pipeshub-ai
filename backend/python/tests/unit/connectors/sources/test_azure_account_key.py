"""A connection string whose AccountKey Azure's SDK cannot sign with is refused with words that say
what to fix: when it is saved, when a connector saved with it is enabled, and when a request fails
to sign. Before, the admin saw base64's "Incorrect padding".

Signing fails before a request is sent; the transports below fail the test if one ever is.
"""

import base64
import copy
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.storage.blob import BlobServiceClient
from azure.storage.fileshare import ShareServiceClient
from fastapi import HTTPException

from app.config.redaction import REDACTED_PLACEHOLDER
from app.connectors.api.router import update_connector_instance_auth_config
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.auth_builder import AuthType
from app.connectors.sources.azure_blob.connector import AzureBlobConnector
from app.connectors.sources.azure_files.connector import AzureFilesConnector
from app.sources.client.azure.connection_string import INVALID_ACCOUNT_KEY
from app.sources.external.azure.azure_blob import azure_error_text
from app.sources.external.azure.azure_files import describe_error

GOOD_KEY = base64.b64encode(bytes(range(64))).decode()


def _connection_string(key: str) -> str:
    return f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={key};EndpointSuffix=core.windows.net"


GOOD = _connection_string(GOOD_KEY)
BAD = _connection_string("not-a-real-key")

# (connector class, the auth field holding its connection string)
CONNECTORS = pytest.mark.parametrize(
    ("connector_cls", "field"),
    [(AzureBlobConnector, "azureBlobConnectionString"), (AzureFilesConnector, "connectionString")],
    ids=["blob", "files"],
)


class TestAuthConfigProblems:

    @CONNECTORS
    def test_a_bad_key_is_refused(self, connector_cls: type, field: str) -> None:
        auth = {"authType": AuthType.CONNECTION_STRING, field: BAD}

        assert connector_cls.auth_config_problems(auth) == [INVALID_ACCOUNT_KEY]

    @CONNECTORS
    def test_a_good_key_passes(self, connector_cls: type, field: str) -> None:
        assert connector_cls.auth_config_problems({"authType": AuthType.CONNECTION_STRING, field: GOOD}) == []

    @CONNECTORS
    def test_entra_id_ignores_a_leftover_connection_string(self, connector_cls: type, field: str) -> None:
        assert connector_cls.auth_config_problems({"authType": AuthType.ENTRA_ID, field: BAD}) == []


def _connector(connector_cls: type, field: str, connection_string: str) -> AzureBlobConnector | AzureFilesConnector:
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={"auth": {field: connection_string}})
    app = "AzureBlobApp" if connector_cls is AzureBlobConnector else "AzureFilesApp"
    with patch(f"{connector_cls.__module__}.{app}"):
        connector = connector_cls(
            logger=logging.getLogger("test.azure_account_key"),
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=config_service,
            connector_id="az-1",
            scope="personal",
            created_by="user-1",
        )
    connector.notify = AsyncMock()
    return connector


class TestInitWithASavedBadKey:

    @CONNECTORS
    async def test_init_refuses_with_the_reason(self, connector_cls: type, field: str) -> None:
        connector = _connector(connector_cls, field, BAD)
        client_cls = "AzureBlobClient" if connector_cls is AzureBlobConnector else "AzureFilesClient"

        with patch(f"{connector_cls.__module__}.{client_cls}.build_from_services", new_callable=AsyncMock) as build:
            with pytest.raises(ConnectorInitError) as raised:
                await connector.init()

        assert INVALID_ACCOUNT_KEY in str(raised.value)
        build.assert_not_awaited()
        assert INVALID_ACCOUNT_KEY in connector.notify.await_args.kwargs["message"]


def _offline_transport() -> MagicMock:
    transport = MagicMock()
    transport.send.side_effect = AssertionError("a request reached the transport")
    return transport


class TestSigningErrorText:

    def test_blob_says_the_key_is_not_valid(self) -> None:
        client = BlobServiceClient.from_connection_string(BAD, transport=_offline_transport())

        with pytest.raises(Exception) as raised:
            next(iter(client.list_containers()))

        assert type(raised.value).__name__ == "AzureSigningError"
        assert azure_error_text(raised.value) == INVALID_ACCOUNT_KEY

    def test_files_says_the_key_is_not_valid(self) -> None:
        client = ShareServiceClient.from_connection_string(BAD, transport=_offline_transport())

        with pytest.raises(Exception) as raised:
            next(iter(client.list_shares()))

        assert type(raised.value).__name__ == "AzureSigningError"
        assert describe_error(raised.value) == INVALID_ACCOUNT_KEY


_ROUTER = "app.connectors.api.router"
_CONFIG_PATH = "/services/connectors/az-1/config"
_INSTANCE = {
    "_key": "az-1",
    "type": "Azure Blob",
    "authType": AuthType.CONNECTION_STRING,
    "isActive": False,
    "scope": "personal",
    "createdBy": "user-1",
    "name": "My Blob",
}
_METADATA = {
    "config": {
        "auth": {
            "schemas": {
                AuthType.CONNECTION_STRING: {
                    "fields": [{"name": "azureBlobConnectionString", "fieldType": "PASSWORD", "isSecret": True}]
                }
            }
        }
    }
}


def _auth_save(stored_connection_string: str, body_auth: dict[str, Any]) -> tuple[Any, AsyncMock]:
    stored = {
        "auth": {
            "authType": AuthType.CONNECTION_STRING,
            "connectorScope": "personal",
            "azureBlobConnectionString": stored_connection_string,
        }
    }
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(
        side_effect=lambda path, **kwargs: copy.deepcopy(stored) if path == _CONFIG_PATH else kwargs.get("default", {})
    )
    config_service.set_config = AsyncMock(return_value=True)
    registry = AsyncMock()
    registry.get_connector_instance = AsyncMock(return_value=dict(_INSTANCE))
    registry.get_connector_metadata = AsyncMock(return_value=copy.deepcopy(_METADATA))
    registry.update_connector_instance = AsyncMock(return_value={"_key": "az-1"})
    user = {"userId": "user-1", "orgId": "org-1", "role": "member"}
    request = MagicMock()
    request.state.user.get = lambda key, default=None: user.get(key, default)
    request.json = AsyncMock(return_value={"auth": body_auth})
    request.app.container = SimpleNamespace(
        logger=MagicMock(return_value=logging.getLogger("test")),
        config_service=MagicMock(return_value=config_service),
    )
    request.app.state = SimpleNamespace(connector_registry=registry)
    return request, config_service


@pytest.fixture()
def _route_access() -> Iterator[None]:
    with (
        patch(f"{_ROUTER}.resolve_config_service", side_effect=lambda container, org_id: container.config_service()),
        patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=dict(_INSTANCE)),
        patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock),
    ):
        yield


@pytest.mark.usefixtures("_route_access")
class TestSave:

    async def test_a_bad_key_is_refused_with_the_reason_and_nothing_is_saved(self) -> None:
        request, config_service = _auth_save(GOOD, {"azureBlobConnectionString": BAD})

        with pytest.raises(HTTPException) as raised:
            await update_connector_instance_auth_config("az-1", request, AsyncMock())

        assert raised.value.status_code == 400
        assert raised.value.detail == INVALID_ACCOUNT_KEY
        config_service.set_config.assert_not_awaited()

    async def test_an_edit_that_sends_the_mask_keeps_the_stored_key_and_saves(self) -> None:
        request, config_service = _auth_save(GOOD, {"azureBlobConnectionString": REDACTED_PLACEHOLDER})

        await update_connector_instance_auth_config("az-1", request, AsyncMock())

        saved = config_service.set_config.await_args.args[1]
        assert saved["auth"]["azureBlobConnectionString"] == GOOD
