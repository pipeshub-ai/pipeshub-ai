"""S3 connector user-visible notification paths."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("aioboto3")

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
    assert "not found" in notif.publish_notification.await_args.kwargs["payload"]["message"].lower()
