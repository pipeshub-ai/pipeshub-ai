"""S3 connector user-visible notification paths."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("aiobotocore")

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.s3.connector import S3Connector


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.s3.notif")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-s3-notif"
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_empty():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value=None)
    return svc


@pytest.mark.asyncio
async def test_s3_init_missing_config_calls_notify_error(
    mock_logger,
    mock_data_entities_processor,
    mock_data_store_provider,
    mock_config_empty,
):
    notif = MagicMock()
    notif.publish_notification = AsyncMock()

    with patch("app.connectors.sources.s3.connector.S3App"):
        connector = S3Connector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_empty,
            connector_id="s3-notif-1",
            scope="team",
            created_by="507f1f77bcf86cd799439011",
        )
    connector._notification_service = notif

    ok = await connector.init()
    assert ok is False
    await asyncio.sleep(0)

    notif.publish_notification.assert_awaited()
    assert "not found" in notif.publish_notification.await_args.kwargs["message"].lower()


@pytest.mark.asyncio
async def test_s3_iam_role_sts_access_denied_raises_connector_auth_error(
    mock_logger,
    mock_data_entities_processor,
    mock_data_store_provider,
) -> None:
    """A role the base key cannot assume (bad ARN or wrong external ID) surfaces as CONNECTOR_AUTH_ERROR."""
    from botocore.exceptions import ClientError

    from app.services.notification.types import NotificationType
    from app.sources.client.s3 import s3 as s3_module

    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={
        "auth": {
            "authType": "IAM_ROLE",
            "accessKey": "AKIDBASE",
            "secretKey": "BASESECRET",
            "roleArn": "arn:aws:iam::123456789012:role/missing",
            "externalId": "wrong",
        }
    })
    denied = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "not authorized to perform: sts:AssumeRole"}},
        "AssumeRole",
    )
    sts = MagicMock()
    sts.__aenter__ = AsyncMock(return_value=sts)
    sts.__aexit__ = AsyncMock(return_value=False)
    sts.assume_role = AsyncMock(side_effect=denied)
    real_create = s3_module.create_aio_session

    def session_factory(access_key_id, secret_access_key, region_name) -> object:
        if not access_key_id:
            return real_create(access_key_id, secret_access_key, region_name)
        base = MagicMock()
        base.create_client.return_value = sts
        return base

    with patch("app.connectors.sources.s3.connector.S3App"):
        connector = S3Connector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=config_service,
            connector_id="s3-role-1",
            scope="team",
            created_by="507f1f77bcf86cd799439011",
        )
    connector.notify = AsyncMock()

    with patch("app.sources.client.s3.s3.create_aio_session", side_effect=session_factory), patch(
        "app.connectors.sources.s3.connector.load_connector_filters",
        AsyncMock(return_value=(MagicMock(), MagicMock())),
    ):
        assert await connector.init() is True
        with pytest.raises(ConnectorInitError, match="AccessDenied"):
            await connector.test_connection_and_access()

    sts.assume_role.assert_awaited_once()
    assert sts.assume_role.await_args.kwargs["ExternalId"] == "wrong"
    kwargs = connector.notify.await_args.kwargs
    assert kwargs["type"] == NotificationType.CONNECTOR_AUTH_ERROR
    assert "AccessDenied" in kwargs["message"]


def test_s3_registry_keeps_access_key_default_and_offers_iam_role() -> None:
    # The create route auto-selects supportedAuthTypes[0] when no authType is sent.
    auth = S3Connector._connector_metadata["config"]["auth"]
    assert auth["supportedAuthTypes"] == ["ACCESS_KEY", "IAM_ROLE"]
    fields = {f["name"]: f for f in auth["schemas"]["IAM_ROLE"]["fields"]}
    assert list(fields) == ["accessKey", "secretKey", "roleArn", "externalId"]
    assert fields["externalId"]["required"] is False
