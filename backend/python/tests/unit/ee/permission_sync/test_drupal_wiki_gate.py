"""Without a licence key a company-wide Drupal Wiki connector reads no access from Drupal Wiki."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.drupal_wiki.connector import DrupalWikiConnector
from app.ee.permission_sync.drupal_wiki import DrupalWikiPermissionsMixin
from app.models.permission import EntityType


def _connector(*, gated: bool) -> DrupalWikiConnector:
    processor = MagicMock()
    processor.org_id = "org-1"
    processor.skips_permission_writes = AsyncMock(return_value=gated)
    processor.on_new_user_groups = AsyncMock()
    processor.on_new_record_groups = AsyncMock()
    connector = DrupalWikiConnector(
        logging.getLogger("test.drupal_wiki_gate"), processor, MagicMock(), MagicMock(), "conn-1", "team", "creator-1",
    )
    connector.data_source = MagicMock()
    connector.sync_filters = FilterCollection()
    connector.notify = AsyncMock()
    connector.record_sync_point.read_sync_point = AsyncMock(return_value={})
    connector.record_sync_point.update_sync_point = AsyncMock()
    connector.graphql = MagicMock()
    connector.graphql.get_space_members = AsyncMock(return_value={"users": [{"email": "a@example.com"}], "groups": []})
    connector._disabled_emails = set()
    connector.data_source.list_spaces = AsyncMock(return_value=MagicMock(
        status=200, json=MagicMock(return_value={"content": [
            {"id": 3, "name": "Blog", "accessStatus": "AUTHENTICATED"},
            {"id": 12, "name": "HR", "accessStatus": "PRIVATE"},
        ], "last": True}),
    ))
    connector._require_ok = MagicMock(side_effect=lambda response, _context: response.json())
    return connector


@pytest.mark.asyncio
async def test_gated_connector_reads_no_groups() -> None:
    connector = _connector(gated=True)

    assert await connector._sync_groups({}) is None

    assert connector.data_source.method_calls == []
    connector.data_entities_processor.on_new_user_groups.assert_not_awaited()


@pytest.mark.asyncio
async def test_gated_spaces_sync_as_containers_without_reading_members() -> None:
    connector = _connector(gated=True)

    spaces = await connector._sync_spaces()

    # Both spaces are kept for their pages; neither carries grants, no member list is read, and nothing tells
    # the admin that nobody can see them: that is the limit, not a missing member list.
    assert set(spaces) == {3, 12}
    written = connector.data_entities_processor.on_new_record_groups.await_args.args[0]
    assert [(group.external_group_id, grants) for group, grants in written] == [("3", []), ("12", [])]
    connector.graphql.get_space_members.assert_not_awaited()
    connector.notify.assert_not_awaited()
    # No member list was read, so no space is noted as having its access confirmed.
    connector.record_sync_point.update_sync_point.assert_not_awaited()


@pytest.mark.asyncio
async def test_licensed_spaces_still_carry_their_grants() -> None:
    connector = _connector(gated=False)

    await connector._sync_spaces()

    written = connector.data_entities_processor.on_new_record_groups.await_args.args[0]
    assert [[g.entity_type for g in grants] for _, grants in written] == [[EntityType.ORG], [EntityType.USER]]
    connector.graphql.get_space_members.assert_awaited_once()


def test_the_connector_reads_access_only_through_the_mixin() -> None:
    assert DrupalWikiPermissionsMixin in DrupalWikiConnector.__mro__
    assert "_sync_groups" not in DrupalWikiConnector.__dict__
    assert "_build_space_permissions" not in DrupalWikiConnector.__dict__
