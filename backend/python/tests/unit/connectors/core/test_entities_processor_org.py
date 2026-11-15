"""Which org the entities processor writes records under."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from app.config.constants.arangodb import Connectors
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
from app.models.entities import AppUser

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


def _make_user(connector_id: str = "conn-1", email: str = "alice@example.com", org_id: str = "") -> AppUser:
    return AppUser(
        app_name=Connectors.GOOGLE_DRIVE,
        connector_id=connector_id,
        id="u-1",
        source_user_id="s-1",
        email=email,
        full_name="Alice",
        org_id=org_id,
    )


async def test_on_new_app_users_with_explicit_organization() -> None:
    provider = _provider(ORGS)
    tx = provider.transaction.return_value.__aenter__.return_value
    tx.get_document = AsyncMock(return_value={"id": "conn-1", "orgId": "first-org"})
    tx.batch_upsert_app_users = AsyncMock()

    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    await processor.initialize("first-org")

    user = _make_user(connector_id="conn-1", email="alice@example.com")
    await processor.on_new_app_users([user])

    assert user.org_id == "first-org"
    tx.batch_upsert_app_users.assert_awaited_once_with([user])


async def test_on_new_app_users_with_ambient_organization() -> None:
    from app.core import org_context

    provider = _provider(ORGS)
    tx = provider.transaction.return_value.__aenter__.return_value
    tx.get_document = AsyncMock(return_value={"id": "conn-1", "orgId": "second-org"})
    tx.batch_upsert_app_users = AsyncMock()

    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    # Fallback initialization in multi-org: fallback must not be proof of ownership
    await processor.initialize()
    assert processor._org_id_is_fallback is True

    user = _make_user(connector_id="conn-1", email="alice@example.com")
    with org_context.for_org("second-org", job="test"):
        await processor.on_new_app_users([user])

    assert user.org_id == "second-org"
    tx.batch_upsert_app_users.assert_awaited_once_with([user])


async def test_on_new_app_users_missing_organization_in_multi_org_deployment_raises() -> None:
    provider = _provider(ORGS)  # 2 orgs
    tx = provider.transaction.return_value.__aenter__.return_value
    tx.get_document = AsyncMock(return_value={"id": "conn-1"})  # no orgId on connector app
    tx.batch_upsert_app_users = AsyncMock()

    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    await processor.initialize()  # sets fallback org

    user = _make_user(connector_id="conn-1")
    with pytest.raises(ValueError, match="Cannot resolve organization"):
        await processor.on_new_app_users([user])

    tx.batch_upsert_app_users.assert_not_called()


async def test_on_new_app_users_conflicting_ambient_and_processor_org_raises() -> None:
    from app.core import org_context

    provider = _provider(ORGS)
    tx = provider.transaction.return_value.__aenter__.return_value
    tx.get_document = AsyncMock(return_value={"id": "conn-1", "orgId": "first-org"})
    tx.batch_upsert_app_users = AsyncMock()

    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    await processor.initialize("first-org")

    user = _make_user(connector_id="conn-1")
    with org_context.for_org("second-org", job="test"):
        with pytest.raises(ValueError, match="Conflicting organization context"):
            await processor.on_new_app_users([user])

    tx.batch_upsert_app_users.assert_not_called()


async def test_on_new_app_users_conflicting_connector_and_context_org_raises() -> None:
    provider = _provider(ORGS)
    tx = provider.transaction.return_value.__aenter__.return_value
    tx.get_document = AsyncMock(return_value={"id": "conn-1", "orgId": "second-org"})
    tx.batch_upsert_app_users = AsyncMock()

    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    await processor.initialize("first-org")

    user = _make_user(connector_id="conn-1")
    with pytest.raises(ValueError, match="Organization conflict"):
        await processor.on_new_app_users([user])

    tx.batch_upsert_app_users.assert_not_called()


async def test_on_new_app_users_conflicting_user_org_raises_and_prevents_cross_org_writes() -> None:
    provider = _provider(ORGS)
    tx = provider.transaction.return_value.__aenter__.return_value
    tx.get_document = AsyncMock(return_value={"id": "conn-1", "orgId": "first-org"})
    tx.batch_upsert_app_users = AsyncMock()

    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    await processor.initialize("first-org")

    # User explicitly declares second-org which conflicts with connector's first-org
    user = _make_user(connector_id="conn-1", email="alice@example.com", org_id="second-org")
    with pytest.raises(ValueError, match="conflicts with resolved organization"):
        await processor.on_new_app_users([user])

    # Assert no cross-organization user or edge is written
    tx.batch_upsert_app_users.assert_not_called()


async def test_on_new_app_users_single_org_fallback_is_safe() -> None:
    provider = _provider([{"id": "only-org"}])
    tx = provider.transaction.return_value.__aenter__.return_value
    tx.get_document = AsyncMock(return_value={"id": "conn-1"})
    tx.batch_upsert_app_users = AsyncMock()

    processor = DataSourceEntitiesProcessor(MagicMock(), provider, MagicMock())
    await processor.initialize()

    user = _make_user(connector_id="conn-1")
    await processor.on_new_app_users([user])

    assert user.org_id == "only-org"
    tx.batch_upsert_app_users.assert_awaited_once_with([user])

