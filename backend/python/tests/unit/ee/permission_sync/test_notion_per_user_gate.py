"""Without a licence key Notion's per-user mode turns no user's listing into a grant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.user_access import UserConnection
from app.connectors.sources.notion.connector import NotionConnector
from app.ee.permission_sync.notion_per_user import NotionPerUserPermissionsMixin
from app.models.entities import RecordType
from app.models.permission import EntityType, PermissionType


def _connector(*, gated: bool) -> NotionPerUserPermissionsMixin:
    connector = type("Connector", (NotionPerUserPermissionsMixin,), {})()
    connector.connector_id = "notion-1"
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    return connector


def _page() -> SimpleNamespace:
    return SimpleNamespace(record_type=RecordType.WEBPAGE, is_placeholder=False, inherit_permissions=True,
                           adds_permissions=False)


CONNECTION = UserConnection(user_id="u1", email="ann@example.com", token="tok", source_user_id="n-u1",
                            source_email="ann@example.com", connected_at=1)
KEPT = [MagicMock()]


@pytest.mark.asyncio
async def test_gated_listing_gives_no_grant() -> None:
    connector = _connector(gated=True)
    page = _page()

    # The rows go back as they came: the page is written as content, and the processor keeps what is stored.
    assert await connector._with_user_grants([(page, KEPT)], CONNECTION) == [(page, KEPT)]

    assert page.inherit_permissions is True and page.adds_permissions is False
    connector.data_entities_processor.skips_permission_writes.assert_awaited_once_with("notion-1")


@pytest.mark.asyncio
async def test_licensed_listing_grants_the_user_and_cuts_inheritance() -> None:
    connector = _connector(gated=False)
    page = _page()

    [(record, grants)] = await connector._with_user_grants([(page, KEPT)], CONNECTION)

    assert record is page and page.inherit_permissions is False and page.adds_permissions is True
    assert [(g.email, g.type, g.entity_type) for g in grants] == [
        ("ann@example.com", PermissionType.READ, EntityType.USER),
    ]


@pytest.mark.asyncio
async def test_outside_a_users_pass_the_licence_is_not_asked() -> None:
    connector = _connector(gated=False)
    page = _page()

    assert await connector._with_user_grants([(page, KEPT)], None) == [(page, KEPT)]
    connector.data_entities_processor.skips_permission_writes.assert_not_awaited()


def test_the_connector_takes_its_user_grants_from_the_gated_mixin() -> None:
    assert issubclass(NotionConnector, NotionPerUserPermissionsMixin)
    assert "_with_user_grants" not in NotionConnector.__dict__
