"""A Notion team connector grants its pages to nobody, on PostgreSQL, against a real database.

The PostgreSQL twin of ``graph_db/test_notion_workspace_access_real_backends.py``: Notion's API does not say who may
open a page, so a workspace member linked to the app opens none of its pages, and a workspace stored while it still
inherited the app stops granting them on the next sync.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, PermissionModel
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.notion.connector import NotionConnector
from app.models.entities import AppUser, RecordGroup, RecordGroupType

from .test_access import World

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

APP = "notion-app"
WORKSPACE = "ws-notion-pg"
EMAIL = "member@notion-pg.example"
logger = logging.getLogger("notion-workspace-access-pg")


@pytest.fixture
async def w(pg_provider, pg_db, org_ctx) -> AsyncIterator[World]:
    org = uuid.uuid4().hex[:24]
    with org_ctx(org):
        world = World(pg_provider, org)
        await world.nodes("organizations", {"_key": org, "isActive": True})
        await world.nodes("apps", {"_key": APP, "type": Connectors.NOTION.value, "isActive": True, "scope": "team"})
        await world.edge("orgAppRelation", ("organizations", org), ("apps", APP))
        await world.nodes("users", {"_key": "m", "userId": "um", "email": EMAIL, "isActive": True})
        await world.edge("belongsTo", ("users", "m"), ("organizations", org), entityType="ORGANIZATION")
        yield world
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def _connector(w: World) -> NotionConnector:
    store = GraphDataStore(logger, w.p)
    processor = DataSourceEntitiesProcessor(logger, store, MagicMock())
    processor.org_id = w.org
    processor.messaging_producer = MagicMock(send_message=AsyncMock(return_value=True), send_messages=AsyncMock(return_value=[]))
    # The link the user sync writes for every workspace member /v1/users lists.
    await processor.on_new_app_users([AppUser(
        app_name=Connectors.NOTION, connector_id=APP, source_user_id="notion-m", email=EMAIL, full_name="m", org_id=w.org,
    )])
    connector = NotionConnector(
        logger=logger, data_entities_processor=processor, data_store_provider=store, config_service=AsyncMock(),
        connector_id=APP, scope="team", created_by="it",
    )
    connector.workspace_id, connector.workspace_name = WORKSPACE, "PG workspace"
    return connector


async def _sync_pages(connector: NotionConnector, *, inheriting: tuple[str, ...] = ()) -> list[str]:
    """A top-level page and one under it, saved as the page walk saves them; their record ids.
    ``inheriting``: pages stored as an earlier version stored them, inheriting from what is above them."""
    ids = []
    for page_id, parent in (("page-top", {"type": "workspace", "workspace": True}),
                            ("page-child", {"type": "page_id", "page_id": "page-top"})):
        record = await connector._transform_to_webpage_record({
            "object": "page", "id": page_id, "parent": parent, "url": f"https://www.notion.so/{page_id}",
            "created_time": "2026-01-01T00:00:00.000Z", "last_edited_time": "2026-01-02T00:00:00.000Z",
            "properties": {"title": {"type": "title", "title": [{"plain_text": page_id}]}},
        }, "page")
        record.inherit_permissions = record.inherit_permissions or page_id in inheriting
        await connector.data_entities_processor.on_new_records([(record, [])])
        stored = await connector.data_entities_processor.get_record_by_external_id(APP, page_id)
        ids.append(stored.id)
    return ids


async def test_a_workspace_member_opens_no_page(w: World) -> None:
    connector = await _connector(w)
    await connector._create_workspace_record_group()
    pages = await _sync_pages(connector)
    assert await w.allowed("m", *pages) == set(), "A workspace member opens pages Notion never said they may open."


async def test_a_workspace_that_inherited_the_app_stops_granting_its_pages(w: World) -> None:
    connector = await _connector(w)
    # The state an earlier sync left: the workspace inherits the app, so every member opens every page.
    await connector.data_entities_processor.on_new_record_groups([(RecordGroup(
        org_id=w.org, name="PG workspace", external_group_id=WORKSPACE, connector_name=Connectors.NOTION,
        connector_id=APP, group_type=RecordGroupType.NOTION_WORKSPACE,
        permission_model=PermissionModel.RECORD_GROUP_LEVEL, inherit_permissions=True,
    ), [])])
    pages = await _sync_pages(connector, inheriting=("page-top", "page-child"))
    assert await w.allowed("m", *pages) == set(pages), "the earlier state must be visible for this test to mean anything"

    await connector._create_workspace_record_group()

    assert await w.allowed("m", *pages) == set(), "The next sync left the workspace granting its pages to every member."


async def test_a_page_a_token_lists_opens_with_its_files_and_a_subpage_it_does_not_list_stays_shut(w: World) -> None:
    """The member's own personal access token lists the top page only (the subpage is restricted from them in
    Notion). The subpage was stored inheriting from its parent, as an earlier version left it."""
    connector = await _connector(w)
    await connector._create_workspace_record_group()
    top, child = await _sync_pages(connector, inheriting=("page-child",))
    attachment = connector._transform_to_file_record({
        "id": "block-pdf", "type": "pdf", "pdf": {"type": "external", "external": {"url": "https://files.example/a.pdf"}},
    }, "page-top")
    await connector.data_entities_processor.on_new_records([(attachment, [])])
    file_id = (await connector.data_entities_processor.get_record_by_external_id(APP, "block-pdf")).id
    connector._readers = {"page-top": {EMAIL}}

    await connector._apply_page_access()

    assert await w.allowed("m", top, child, file_id) == {top, file_id}, "a grant on a page must reach its files, never a subpage"

    connector._readers = {}
    await connector._apply_page_access()
    assert await w.allowed("m", top, child, file_id) == set(), "access stayed after the token stopped listing the page"
