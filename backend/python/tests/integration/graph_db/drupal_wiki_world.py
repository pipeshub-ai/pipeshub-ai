"""A small Drupal Wiki in memory, synced by the real connector into a real graph, and read back per user.

The connector, ``DataSourceEntitiesProcessor`` and the graph provider run for real; only the wiki's REST and GraphQL
answers are faked. Each scenario compares what each PipesHub user may open (the provider's batch access check) with
what Drupal Wiki lets them read: a page is readable by whoever may read its home space, and by whoever may read a
space it is shared into (handbook node 461), never by a disabled account (node 248). Used on Neo4j and ArangoDB by
``test_drupal_wiki_access_real_backends.py`` and on PostgreSQL by ``postgres/test_drupal_wiki_access.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.drupal_wiki.connector import DrupalWikiConnector
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger("drupal-wiki-access-it")

ALICE, BOB, CAROL = "alice@wiki.test", "bob@wiki.test", "carol@wiki.test"
OPEN_SPACE, PRIVATE_SPACE = 1, 2
OPEN_PAGE, PRIVATE_PAGE, SUB_PAGE = 20, 10, 11


class _Response:
    def __init__(self, payload: object = None, status: int = 200) -> None:
        self.status, self._payload = status, payload

    def json(self) -> object:
        return self._payload

    def text(self) -> str:
        return ""


@dataclass
class FakeWiki:
    """Alice reads the private space; everyone enabled reads the open one. Page 11 sits under page 10."""

    users: dict[int, dict[str, Any]] = field(default_factory=lambda: {
        1: {"id": 1, "email": ALICE, "status": "ENABLED"},
        2: {"id": 2, "email": BOB, "status": "ENABLED"},
        3: {"id": 3, "email": CAROL, "status": "ENABLED"},
    })
    spaces: dict[int, dict[str, Any]] = field(default_factory=lambda: {
        OPEN_SPACE: {"id": OPEN_SPACE, "name": "Open", "accessStatus": "AUTHENTICATED"},
        PRIVATE_SPACE: {"id": PRIVATE_SPACE, "name": "Private", "accessStatus": "PRIVATE"},
    })
    members: dict[int, list[str]] = field(default_factory=lambda: {PRIVATE_SPACE: [ALICE]})
    pages: dict[int, dict[str, Any]] = field(default_factory=lambda: {
        PRIVATE_PAGE: {"space": PRIVATE_SPACE, "parent": None, "modified": 1000},
        SUB_PAGE: {"space": PRIVATE_SPACE, "parent": PRIVATE_PAGE, "modified": 1000},
        OPEN_PAGE: {"space": OPEN_SPACE, "parent": None, "modified": 1000},
    })
    shares: dict[int, set[int]] = field(default_factory=dict)
    # pages the next listing of their home space leaves out, as an offset listing does when
    # another page is deleted between two of its windows
    skipped_once: set[int] = field(default_factory=set)

    def readable(self) -> dict[str, set[str]]:
        enabled = {u["email"] for u in self.users.values() if u["status"] == "ENABLED"}

        def readers(space: int) -> set[str]:
            if self.spaces[space]["accessStatus"] != "PRIVATE":
                return set(enabled)
            return set(self.members.get(space, [])) & enabled

        out: dict[str, set[str]] = {email: set() for email in (ALICE, BOB, CAROL)}
        for page_id, page in self.pages.items():
            for email in readers(page["space"]).union(*(readers(s) for s in self.shares.get(page_id, set()))):
                out[email].add(f"page:{page_id}")
        return out

    # ---- the REST data source

    def _excerpt(self, page_id: int) -> dict[str, Any]:
        page = self.pages[page_id]
        return {"id": page_id, "title": f"Page {page_id}", "type": "DOCUMENT", "homeSpace": page["space"],
                "lastModified": page["modified"]}

    async def list_users(self, **_: object) -> _Response:
        return _Response({"content": list(self.users.values()), "last": True})

    async def list_groups(self, **_: object) -> _Response:
        return _Response({"content": [], "last": True})

    async def list_spaces(self, **_: object) -> _Response:
        return _Response({"content": list(self.spaces.values()), "last": True})

    async def list_pages(self, space_id: int, **_: object) -> _Response:
        ids = [p for p, page in self.pages.items() if page["space"] == space_id or space_id in self.shares.get(p, set())]
        skipped = {p for p in self.skipped_once if self.pages[p]["space"] == space_id}
        self.skipped_once -= skipped
        return _Response({"content": [self._excerpt(p) for p in sorted(set(ids) - skipped)], "last": True})

    async def list_attachments(self, **_: object) -> _Response:
        return _Response([])

    async def get_page(self, page_id: int) -> _Response:
        if page_id not in self.pages:
            return _Response({"title": "Not Found", "status": 404}, status=404)
        return _Response(self._excerpt(page_id))

    # ---- the GraphQL data source

    async def get_space_members(self, space_id: int) -> dict[str, Any]:
        return {"users": [{"email": e} for e in self.members.get(space_id, [])], "groups": []}

    async def get_space_tree(self, space_id: int) -> list[dict[str, Any]]:
        return [
            {"id": f"n{p}", "pageId": p, "parentId": f"n{page['parent']}" if page["parent"] else None}
            for p, page in self.pages.items() if page["space"] == space_id
        ]


@dataclass
class DrupalWorld:
    graph: Any
    org_id: str
    connector_id: str
    user_ids: dict[str, str]  # email -> PipesHub userId
    wiki: FakeWiki = field(default_factory=FakeWiki)
    processor: DataSourceEntitiesProcessor | None = None

    async def seed(self) -> None:
        now = get_epoch_timestamp_in_ms()
        stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
        await self.graph.batch_upsert_nodes(
            [{"id": self.org_id, "_key": self.org_id, "name": "Org", "isActive": True, "accountType": "enterprise",
              **stamps}], collection=CollectionNames.ORGS.value)
        await self.graph.batch_upsert_nodes(
            [{"id": self.connector_id, "_key": self.connector_id, "name": "Drupal Wiki",
              "type": Connectors.DRUPAL_WIKI.value, "appGroup": "Drupal Wiki", "scope": "team", "isActive": True,
              "orgId": self.org_id, **stamps}], collection=CollectionNames.APPS.value)
        for email, user_id in self.user_ids.items():
            await self.graph.batch_upsert_nodes(
                [{"id": user_id, "_key": user_id, "userId": user_id, "email": email, "orgId": self.org_id,
                  "isActive": True, "fullName": email.split("@")[0], **stamps}], collection=CollectionNames.USERS.value)
            await self.graph.batch_create_edges(
                [{"from_id": user_id, "from_collection": CollectionNames.USERS.value, "to_id": self.org_id,
                  "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION", **stamps}],
                collection=CollectionNames.BELONGS_TO.value)
        self.processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, self.graph), MagicMock())
        self.processor.org_id = self.org_id
        self.processor.messaging_producer = MagicMock(
            send_message=AsyncMock(return_value=True),
            send_messages=AsyncMock(side_effect=lambda _topic, messages: [True] * len(messages)),
            send_event=AsyncMock(return_value=True),
        )

    async def sync(self) -> None:
        connector = DrupalWikiConnector(
            logger, self.processor, GraphDataStore(logger, self.graph), MagicMock(), self.connector_id, "team", "it",
        )
        connector.base_url = "https://wiki.test"
        connector.data_source = self.wiki
        connector.graphql = self.wiki
        connector.notify = AsyncMock()
        with patch("app.connectors.sources.drupal_wiki.connector.load_connector_filters",
                   AsyncMock(return_value=(FilterCollection(), FilterCollection()))), \
             patch("app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
                   AsyncMock(return_value=False)):
            await connector.run_sync()
        assert connector._failures == [], connector._failures

    async def observed(self) -> dict[str, set[str]]:
        ids = {}
        for page_id in (OPEN_PAGE, PRIVATE_PAGE, SUB_PAGE):
            record = await self.processor.get_record_by_external_id(self.connector_id, f"page:{page_id}")
            if record is not None:
                ids[record.id] = f"page:{page_id}"
        out = {}
        for email, user_id in self.user_ids.items():
            allowed = await self.graph.filter_accessible_record_ids(list(ids), user_id, self.org_id) if ids else set()
            out[email] = {ids[i] for i in allowed}
        return out

    async def check(self, step: str) -> None:
        want, got = self.wiki.readable(), await self.observed()
        assert got == want, f"{step}: PipesHub {got} != Drupal Wiki {want}"


async def a_disabled_reader_loses_every_space_until_enabled_again(world: DrupalWorld) -> None:
    await world.sync()
    await world.check("full sync")
    world.wiki.users[2]["status"] = "DISABLED"
    await world.sync()
    await world.check("bob disabled")
    assert world.wiki.readable()[BOB] == set(), "control: the wiki lets a disabled account read nothing"
    del world.wiki.users[3]
    await world.sync()
    await world.check("carol removed from the wiki")
    world.wiki.users[2]["status"] = "ENABLED"
    await world.sync()
    await world.check("bob enabled again")


async def a_deleted_page_goes_on_the_next_sync(world: DrupalWorld) -> None:
    await world.sync()
    del world.wiki.pages[OPEN_PAGE]
    await world.sync()
    await world.check("open page deleted")
    assert await world.processor.get_record_by_external_id(world.connector_id, f"page:{OPEN_PAGE}") is None


async def a_shared_page_reaches_the_other_spaces_readers_and_its_sub_page_does_not(world: DrupalWorld) -> None:
    await world.sync()
    await world.sync()  # the private space has its checkpoint: every page is STRICT
    world.wiki.shares[PRIVATE_PAGE] = {OPEN_SPACE}
    await world.sync()
    await world.check("private page shared into the open space")
    assert f"page:{SUB_PAGE}" not in (await world.observed())[BOB], "the share must not reach the sub-page"
    world.wiki.pages[PRIVATE_PAGE]["modified"] = 2000
    await world.sync()
    await world.check("shared page edited, so its own space's pass writes it again")
    world.wiki.skipped_once.add(PRIVATE_PAGE)
    await world.sync()
    await world.check("shared page skipped by its space's listing, so the removal pass writes it again")
    world.wiki.members[PRIVATE_SPACE] = [ALICE, CAROL]
    await world.sync()
    await world.check("carol joins the private space")
    world.wiki.shares.clear()
    await world.sync()
    await world.check("share removed")
