"""A small Drupal Wiki in memory, synced by the real connector into a real graph, and read back per user.

The connector, ``DataSourceEntitiesProcessor`` and the graph provider run for real; only the wiki's REST and GraphQL
answers are faked. After each step, what each PipesHub user may open (the provider's batch access check) is
compared with what Drupal Wiki lets them read: a page is read by whoever reads its home space and by the members of
a space it is shared into (handbook node 461), never by a disabled account (node 248). An open space is read by
everyone; a protected one by its members (node 226), as the member query names them, which is the connector's rule
too (node 363 narrows it to the Reader role, which no documented API names): each step checks that nobody reads
more than that allows, and that PipesHub gives exactly those reads, except a personal workspace's, which PipesHub
cannot name. The trash (node 460) and a personal workspace (node 226) are never synced.
Used on Neo4j and ArangoDB by ``test_drupal_wiki_access_real_backends.py`` and on PostgreSQL by
``postgres/test_drupal_wiki_access.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    MultiselectOperator,
)
from app.connectors.sources.drupal_wiki.connector import DrupalWikiConnector
from app.models.entities import (
    AppUser,
    AppUserGroup,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger("drupal-wiki-access-it")

ALICE, BOB, CAROL = "alice@wiki.test", "bob@wiki.test", "carol@wiki.test"
OPEN_SPACE, PRIVATE_SPACE, TRASH_SPACE, PERSONAL_SPACE = 1, 2, 91, 90
OPEN_PAGE, PRIVATE_PAGE, SUB_PAGE, TRASHED_PAGE, PERSONAL_PAGE = 20, 10, 11, 30, 40
PAGES = (OPEN_PAGE, PRIVATE_PAGE, SUB_PAGE, TRASHED_PAGE, PERSONAL_PAGE)
KEPT_ATTACHMENT, DELETED_ATTACHMENT = 201, 202


class _Response:
    def __init__(self, payload: object = None, status: int = 200) -> None:
        self.status, self._payload = status, payload

    def json(self) -> object:
        return self._payload

    def text(self) -> str:
        return ""


@dataclass
class FakeWiki:
    """Alice is the protected space's member; everyone enabled reads the open one. Page 11 sits
    under page 10. The trash and Alice's personal workspace each hold a page."""

    users: dict[int, dict[str, Any]] = field(default_factory=lambda: {
        1: {"id": 1, "email": ALICE, "status": "ENABLED"},
        2: {"id": 2, "email": BOB, "status": "ENABLED"},
        3: {"id": 3, "email": CAROL, "status": "ENABLED"},
    })
    spaces: dict[int, dict[str, Any]] = field(default_factory=lambda: {
        OPEN_SPACE: {"id": OPEN_SPACE, "name": "Open", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
        PRIVATE_SPACE: {"id": PRIVATE_SPACE, "name": "Private", "type": "BASIC", "accessStatus": "PRIVATE"},
        # The status each reports over REST is not documented; the open one is the risky case.
        TRASH_SPACE: {"id": TRASH_SPACE, "name": "Trash", "type": "TRASH", "accessStatus": "AUTHENTICATED"},
        PERSONAL_SPACE: {"id": PERSONAL_SPACE, "name": "Alice", "type": "USERSPACE", "accessStatus": "AUTHENTICATED"},
    })
    # The members of each protected space, as the member query names them.
    readers: dict[int, list[str]] = field(default_factory=lambda: {PRIVATE_SPACE: [ALICE]})
    pages: dict[int, dict[str, Any]] = field(default_factory=lambda: {
        PRIVATE_PAGE: {"space": PRIVATE_SPACE, "parent": None, "modified": 1000},
        SUB_PAGE: {"space": PRIVATE_SPACE, "parent": PRIVATE_PAGE, "modified": 1000},
        OPEN_PAGE: {"space": OPEN_SPACE, "parent": None, "modified": 1000},
        TRASHED_PAGE: {"space": TRASH_SPACE, "parent": None, "modified": 1000},
        PERSONAL_PAGE: {"space": PERSONAL_SPACE, "parent": None, "modified": 1000},
    })
    shares: dict[int, set[int]] = field(default_factory=dict)
    # page -> its attachments, read by whoever reads the page
    attachments: dict[int, list[int]] = field(default_factory=lambda: {OPEN_PAGE: [KEPT_ATTACHMENT, DELETED_ATTACHMENT]})
    # pages the next listing of their home space leaves out, as an offset listing does when
    # another page is deleted between two of its windows
    skipped_once: set[int] = field(default_factory=set)

    def readable(self, *, nameable_only: bool = False, excluded: tuple[int, ...] = ()) -> dict[str, set[str]]:
        """email -> pages the wiki lets them read; with ``nameable_only``, only the reads PipesHub can grant (not
        a personal workspace's, nor those of spaces the sync filter leaves out)."""
        enabled = {u["email"] for u in self.users.values() if u["status"] == "ENABLED"}

        def readers(space: int) -> set[str]:
            kind = self.spaces[space]["type"]
            if nameable_only and space in excluded:
                return set()
            if kind == "TRASH":
                return set()  # node 460: only two system roles, none of these people
            if kind == "USERSPACE":
                return set() if nameable_only else {ALICE} & enabled  # node 226: its owner alone
            if self.spaces[space]["accessStatus"] != "PRIVATE":
                return set(enabled)
            return set(self.readers.get(space, [])) & enabled

        out: dict[str, set[str]] = {email: set() for email in (ALICE, BOB, CAROL)}
        for page_id, page in self.pages.items():
            shared = (readers(s) for s in self.shares.get(page_id, set()) if s in self.spaces)
            for email in readers(page["space"]).union(*shared):
                out[email].add(f"page:{page_id}")
                out[email].update(f"attachment:{a}" for a in self.attachments.get(page_id, []))
        return out

    # ---- the REST data source

    def _excerpt(self, page_id: int) -> dict[str, Any]:
        page = self.pages[page_id]
        return {"id": page_id, "title": f"Page {page_id}", "type": "DOCUMENT", "homeSpace": page["space"],
                "lastModified": page["modified"]}

    async def list_users(self, **_: object) -> _Response:
        return _Response({"content": list(self.users.values()), "last": True})

    async def get_user(self, user_id: int) -> _Response:
        if user_id not in self.users:
            return _Response({"title": "Not Found", "status": 404}, status=404)
        return _Response(self.users[user_id])

    async def list_groups(self, **_: object) -> _Response:
        return _Response({"content": [], "last": True})

    async def list_spaces(self, **_: object) -> _Response:
        return _Response({"content": list(self.spaces.values()), "last": True})

    async def list_pages(self, space_id: int, **_: object) -> _Response:
        ids = [p for p, page in self.pages.items() if page["space"] == space_id or space_id in self.shares.get(p, set())]
        skipped = {p for p in self.skipped_once if self.pages[p]["space"] == space_id}
        self.skipped_once -= skipped
        return _Response({"content": [self._excerpt(p) for p in sorted(set(ids) - skipped)], "last": True})

    async def list_attachments(self, page_id: int, page: int = 0, **_: object) -> _Response:
        return _Response([
            {"id": a, "fileName": f"file-{a}.pdf", "lastModified": 1000} for a in self.attachments.get(page_id, [])
        ] if page == 0 else [])

    async def get_page(self, page_id: int) -> _Response:
        if page_id not in self.pages:
            return _Response({"title": "Not Found", "status": 404}, status=404)
        return _Response(self._excerpt(page_id))

    # ---- the GraphQL data source

    async def get_space_members(self, space_id: int) -> dict[str, Any]:
        return {"users": [{"email": e} for e in self.readers.get(space_id, [])], "groups": []}

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

    async def sync(self, *, excluded_spaces: tuple[int, ...] = ()) -> None:
        filters = FilterCollection(filters=[Filter(
            key="space_ids", type=FilterType.LIST, operator=MultiselectOperator.NOT_IN,
            value=[str(s) for s in excluded_spaces],
        )]) if excluded_spaces else FilterCollection()
        connector = DrupalWikiConnector(
            logger, self.processor, GraphDataStore(logger, self.graph), MagicMock(), self.connector_id, "team", "it",
        )
        connector.base_url = "https://wiki.test"
        connector.data_source = self.wiki
        connector.graphql = self.wiki
        connector.notify = AsyncMock()
        with patch("app.connectors.sources.drupal_wiki.connector.load_connector_filters",
                   AsyncMock(return_value=(filters, FilterCollection()))), \
             patch("app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
                   AsyncMock(return_value=False)):
            await connector.run_sync()
        assert connector._failures == [], connector._failures

    async def store_as_an_older_sync_did(self) -> None:
        """The protected space and its page as an earlier sync wrote them when bob, no member now, was one: bob holds
        a grant on the space, and the page inherits it."""
        await self.processor.on_new_app_users([AppUser(
            app_name=Connectors.DRUPAL_WIKI, connector_id=self.connector_id, org_id=self.org_id, source_user_id="2",
            email=BOB, full_name="bob", is_active=True,
        )])
        await self.processor.on_new_record_groups([(RecordGroup(
            org_id=self.org_id, name="Private", external_group_id=str(PRIVATE_SPACE), connector_name=Connectors.DRUPAL_WIKI,
            connector_id=self.connector_id, group_type=RecordGroupType.DRUPAL_WIKI_SPACE, inherit_permissions=False,
        ), [Permission(external_id=None, email=BOB, type=PermissionType.READ, entity_type=EntityType.USER)])])
        await self.processor.on_new_records([(WebpageRecord(
            org_id=self.org_id, record_name="Page 10", record_type=RecordType.WEBPAGE,
            external_record_id=f"page:{PRIVATE_PAGE}", external_revision_id="1000",
            external_record_group_id=str(PRIVATE_SPACE), record_group_type=RecordGroupType.DRUPAL_WIKI_SPACE, version=0,
            origin=OriginTypes.CONNECTOR, connector_name=Connectors.DRUPAL_WIKI, connector_id=self.connector_id,
            mime_type=MimeTypes.HTML.value, weburl="https://wiki.test/node/10", inherit_permissions=True,
        ), [])])
        assert f"page:{PRIVATE_PAGE}" in (await self.observed())[BOB], "control: the old grant opens the page"

    async def observed(self) -> dict[str, set[str]]:
        ids = {}
        for external_id in [f"page:{p}" for p in PAGES] + [f"attachment:{a}" for a in (KEPT_ATTACHMENT, DELETED_ATTACHMENT)]:
            record = await self.processor.get_record_by_external_id(self.connector_id, external_id)
            if record is not None:
                ids[record.id] = external_id
        out = {}
        for email, user_id in self.user_ids.items():
            allowed = await self.graph.filter_accessible_record_ids(list(ids), user_id, self.org_id) if ids else set()
            out[email] = {ids[i] for i in allowed}
        return out

    async def check(self, step: str, *, excluded: tuple[int, ...] = ()) -> None:
        allowed, nameable = self.wiki.readable(), self.wiki.readable(nameable_only=True, excluded=excluded)
        got = await self.observed()
        extra = {email: got[email] - allowed[email] for email in got if got[email] - allowed[email]}
        assert not extra, f"{step}: PipesHub lets people read what Drupal Wiki does not: {extra}"
        assert got == nameable, f"{step}: PipesHub {got} != the reads it can grant {nameable}"


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


async def a_protected_spaces_members_read_it_until_removed(world: DrupalWorld) -> None:
    await world.sync()
    await world.check("alice is the protected space's member")
    world.wiki.readers[PRIVATE_SPACE].append(CAROL)
    await world.sync()
    await world.check("carol added to the protected space")
    assert f"page:{PRIVATE_PAGE}" in (await world.observed())[CAROL]
    world.wiki.readers[PRIVATE_SPACE].remove(CAROL)
    await world.sync()
    await world.check("carol removed from the protected space")


async def a_reader_enabled_again_gets_only_what_the_wiki_shows_now(world: DrupalWorld) -> None:
    """A withdrawn person's own grants go with the connector: a membership of a group the wiki deleted (the group pass
    leaves it in place) and a grant an earlier sync left on a page no run writes again. Kept, they would come back
    with the link when the person is enabled again, whatever the wiki shows them then."""
    world.wiki.pages[SUB_PAGE]["modified"] = 500  # older than its space's checkpoint: kept as stored, not written again
    await world.sync()
    sub_page = await world.processor.get_record_by_external_id(world.connector_id, f"page:{SUB_PAGE}")
    bob = next(u for u in await world.processor.get_all_app_users(world.connector_id) if u.email == BOB)
    await world.processor.on_new_user_groups([(AppUserGroup(
        app_name=Connectors.DRUPAL_WIKI, connector_id=world.connector_id, org_id=world.org_id,
        source_user_group_id="7", name="Deleted group"), [bob])])
    async with world.processor.data_store_provider.transaction() as tx:
        group = await tx.get_user_group_by_external_id(connector_id=world.connector_id, external_id="7")
        await tx.batch_create_edges([Permission(type=PermissionType.READ, entity_type=EntityType.USER).to_arango_permission(
            bob.id, CollectionNames.USERS.value, sub_page.id, CollectionNames.RECORDS.value,
        )], collection=CollectionNames.PERMISSION.value)

    async def held() -> dict[str, bool]:
        async with world.processor.data_store_provider.transaction() as tx:
            return {name: await tx.get_edge(bob.id, CollectionNames.USERS.value, to, collection,
                                            CollectionNames.PERMISSION.value, raise_on_error=True) is not None
                    for name, to, collection in (("group", group.id, CollectionNames.GROUPS.value),
                                                 ("page", sub_page.id, CollectionNames.RECORDS.value))}

    assert await held() == {"group": True, "page": True}, "control: bob holds both"
    world.wiki.users[2]["status"] = "DISABLED"
    await world.sync()
    await world.check("bob disabled")
    assert await held() == {"group": False, "page": False}, "bob's own grants must go with the connector"
    world.wiki.users[2]["status"] = "ENABLED"
    await world.sync()
    await world.check("bob enabled again")
    assert await held() == {"group": False, "page": False}, "only what the wiki shows comes back"


async def a_deleted_page_goes_on_the_next_sync(world: DrupalWorld) -> None:
    await world.sync()
    del world.wiki.pages[OPEN_PAGE]
    await world.sync()
    await world.check("open page deleted")
    assert await world.processor.get_record_by_external_id(world.connector_id, f"page:{OPEN_PAGE}") is None


async def a_shared_page_reaches_the_other_spaces_readers_and_its_sub_page_does_not(world: DrupalWorld) -> None:
    await world.sync()
    await world.sync()  # the protected space has its checkpoint: every page is STRICT
    world.wiki.shares[PRIVATE_PAGE] = {OPEN_SPACE}
    await world.sync()
    await world.check("protected page shared into the open space")
    assert f"page:{SUB_PAGE}" not in (await world.observed())[BOB], "the share must not reach the sub-page"
    world.wiki.pages[PRIVATE_PAGE]["modified"] = 2000
    await world.sync()
    await world.check("shared page edited, so its own space's pass writes it again")
    world.wiki.skipped_once.add(PRIVATE_PAGE)
    await world.sync()
    await world.check("shared page skipped by its space's listing, so the removal pass writes it again")
    world.wiki.shares.clear()
    await world.sync()
    await world.check("share removed")


async def a_deleted_attachment_goes_on_the_next_sync_that_lists_its_page(world: DrupalWorld) -> None:
    """Deleting an attachment is not documented to change its page's lastModified, so it goes when its page's
    attachments are next listed: when the page changes, and on every full sync."""
    await world.sync()
    await world.check("attachments of the open page")
    world.wiki.attachments[OPEN_PAGE] = [KEPT_ATTACHMENT]
    world.wiki.pages[OPEN_PAGE]["modified"] = 2000
    await world.sync()
    await world.check("attachment deleted and the page edited")
    assert await world.processor.get_record_by_external_id(world.connector_id, f"attachment:{DELETED_ATTACHMENT}") is None
    world.wiki.attachments[OPEN_PAGE] = []
    await world.graph.delete_sync_points_by_connector_id(connector_id=world.connector_id)  # what a full sync does first
    await world.sync()
    await world.check("the last attachment deleted, then a full sync")
    assert await world.processor.get_record_by_external_id(world.connector_id, f"attachment:{KEPT_ATTACHMENT}") is None


async def the_trash_personal_and_deleted_spaces_are_read_by_nobody(world: DrupalWorld) -> None:
    await world.sync()
    await world.check("the trash and a personal workspace, both reported open")
    del world.wiki.spaces[OPEN_SPACE]
    del world.wiki.pages[OPEN_PAGE]
    await world.sync()
    await world.check("open space deleted, with its page")
    assert await world.processor.get_record_by_external_id(world.connector_id, f"page:{OPEN_PAGE}") is None


async def grants_an_older_sync_wrote_on_a_protected_space_go(world: DrupalWorld) -> None:
    """A former member was granted a protected space; the first sync takes that back, whether the space is synced
    or left out by the filter."""
    await world.store_as_an_older_sync_did()
    await world.sync(excluded_spaces=(PRIVATE_SPACE,))
    await world.check("protected space left out by the filter", excluded=(PRIVATE_SPACE,))
    assert await world.processor.get_record_by_external_id(world.connector_id, f"page:{PRIVATE_PAGE}") is None
    await world.store_as_an_older_sync_did()
    await world.sync()
    await world.check("protected space synced")
