"""Which org the entities processor writes records under."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from app.connectors.core.base.data_processor import (
    data_source_entities_processor as module,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.sources.s3.connector import (
    S3CompatibleDataSourceEntitiesProcessor,
    S3Connector,
)

ORGS = [{"id": "first-org"}, {"id": "second-org"}]


def _provider(orgs: list[dict]) -> MagicMock:
    tx = MagicMock()
    tx.get_all_orgs = AsyncMock(return_value=orgs)
    provider = MagicMock()
    provider.transaction.return_value.__aenter__ = AsyncMock(return_value=tx)
    provider.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return provider


@pytest.fixture(autouse=True)
def _no_broker() -> Iterator[None]:
    producer = MagicMock(initialize=AsyncMock())
    with patch.object(module.MessagingUtils, "create_producer_config_from_service", AsyncMock()), \
         patch.object(module.MessagingFactory, "create_producer", return_value=producer):
        yield


async def test_a_passed_org_wins() -> None:
    provider = _provider(ORGS)
    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())

    await processor.initialize("mine")

    assert processor.org_id == "mine"
    provider.transaction.assert_not_called()


async def test_without_an_org_the_first_one_is_used() -> None:
    processor = DataSourceEntitiesProcessor(MagicMock(), _provider(ORGS), MagicMock())

    await processor.initialize()

    assert processor.org_id == "first-org"


async def test_a_deployment_can_require_the_org() -> None:
    processor = DataSourceEntitiesProcessor(MagicMock(), _provider(ORGS), MagicMock())
    processor.allow_first_org_fallback = False

    with pytest.raises(ValueError):
        await processor.initialize()


async def test_an_org_agnostic_caller_needs_no_org() -> None:
    provider = _provider(ORGS)
    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    processor.allow_first_org_fallback = False

    await processor.initialize(org_agnostic=True)

    provider.transaction.assert_not_called()


async def test_the_s3_connector_hands_its_org_to_the_processor() -> None:
    initialize = AsyncMock()
    with patch.object(S3CompatibleDataSourceEntitiesProcessor, "initialize", initialize), \
         patch.object(S3Connector, "__init__", return_value=None):
        await S3Connector.create_connector(
            MagicMock(), _provider(ORGS), MagicMock(), "conn-1", "team", "user-1", None, org_id="second-org",
        )

    initialize.assert_awaited_once_with(org_id="second-org")
