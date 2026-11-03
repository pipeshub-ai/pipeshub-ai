"""Zammad knowledge-base categories whose permissions can't be read.

When the knowledge base listing leaves a category's permissions out, the connector
reads them separately. Writing a category replaces its stored role grants, and an
archived or draft answer is granted to the category's editor roles, so a read that
failed for a reason that passes must leave both as stored and be tried again. A
read Zammad refuses (403) or answers "gone" (404) saves the category with no role
grants: access fails closed.

The connector's knowledge-base sync and the real ``ZammadDataSource`` run as they do
in production, over an in-memory Zammad answering HTTP requests. The store is an
in-memory stand-in that replaces grants the way the processor does.
"""

import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from app.connectors.sources.zammad.connector import KB_SYNC_POINT_KEY, ZammadConnector
from app.models.entities import Record
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.zammad.zammad import ZammadDataSource
from zammad_behaviour_fakes import T0, FakeHttpResponse, FakeRecordsDb

CONNECTOR_ID = "zm-kb-perms"
KB_ID = 1
GENERAL, BILLING = 1, 2
EDITORS, READERS, AUDITORS = 2, 3, 4
# answer id -> (category, the timestamp field that sets its visibility)
ANSWERS = {11: (GENERAL, "archived_at"), 12: (GENERAL, "internal_at"), 21: (BILLING, None)}


def stamp(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeZammadKb:
    def __init__(self) -> None:
        self.category_roles: dict[int, dict[int, str]] = {
            GENERAL: {EDITORS: "editor", READERS: "reader"},
            BILLING: {EDITORS: "editor"},
        }
        self.updated: dict[int, float] = dict.fromkeys(ANSWERS, 10.0)
        # category -> HTTP status answered instead of its permissions
        self.permissions_status: dict[int, int] = {}
        self.permissions_unanswered: set[int] = set()

    def _listing(self) -> dict[str, Any]:
        answers = {}
        for answer_id, (category, visible) in ANSWERS.items():
            answer = {
                "id": answer_id, "category_id": category, "translation_ids": [1000 + answer_id],
                "attachments": [], "created_at": stamp(1), "updated_at": stamp(self.updated[answer_id]),
                "published_at": None, "internal_at": None, "archived_at": None,
            }
            if visible:
                answer[visible] = stamp(1)
            answers[str(answer_id)] = answer
        return {
            "KnowledgeBase": {str(KB_ID): {"id": KB_ID, "translation_ids": [1], "category_ids": [GENERAL, BILLING]}},
            "KnowledgeBaseTranslation": {"1": {"id": 1, "kb_locale_id": 1, "title": "Help Center"}},
            # No permissions_effective, so the connector reads each category's permissions.
            "KnowledgeBaseCategory": {
                str(cid): {"id": cid, "knowledge_base_id": KB_ID, "parent_id": None, "translation_ids": [100 + cid]}
                for cid in self.category_roles
            },
            "KnowledgeBaseCategoryTranslation": {
                str(100 + cid): {"id": 100 + cid, "category_id": cid, "title": f"category {cid}"}
                for cid in self.category_roles
            },
            "KnowledgeBaseAnswer": answers,
            "KnowledgeBaseAnswerTranslation": {
                str(1000 + a): {"id": 1000 + a, "answer_id": a, "title": f"answer {a}", "updated_at": stamp(self.updated[a])}
                for a in ANSWERS
            },
        }

    async def execute(self, request: HTTPRequest) -> FakeHttpResponse:
        path = urlparse(request.url).path
        if path == "/api/v1/knowledge_bases/init" and request.method == "POST":
            return FakeHttpResponse(200, self._listing())
        prefix = f"/api/v1/knowledge_bases/{KB_ID}/categories/"
        if path.startswith(prefix) and path.endswith("/permissions"):
            category = int(path[len(prefix):-len("/permissions")])
            if category in self.permissions_unanswered:
                raise ConnectionError("connection reset by peer")
            if category in self.permissions_status:
                return FakeHttpResponse(self.permissions_status[category], {"error": "not now"})
            return FakeHttpResponse(200, {"permissions": [
                {"role_id": role, "access": access} for role, access in self.category_roles[category].items()
            ]})
        return FakeHttpResponse(404, {"error": f"no route {path}"})


def grant_set(permissions: list[Any]) -> set[tuple[str, str]]:
    return {(p.type.value, f"{p.entity_type.value} {p.external_id}") for p in permissions}


class KbAccess(FakeRecordsDb):
    """Writing a category or a record's permissions replaces its stored grants, as the processor does."""

    def __init__(self) -> None:
        super().__init__()
        self.group_grants: dict[str, set[tuple[str, str]]] = {}
        self.record_grants: dict[str, set[tuple[str, str]]] = {}

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.group_grants[group.external_group_id] = grant_set(permissions)
        await super().on_new_record_groups(groups)

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        for record, permissions in records_with_permissions:
            # Writing an existing record again only adds grants; a replace goes through
            # on_updated_record_permissions below.
            self.record_grants.setdefault(record.external_record_id, set()).update(grant_set(permissions))
        await super().on_new_records(records_with_permissions)

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        self.record_grants[record.external_record_id] = grant_set(permissions)


class FakeSyncPoint:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def read_sync_point(self, key: str) -> dict[str, Any]:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.points.setdefault(key, {}).update(data)


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammadKb()
        self.access = KbAccess()
        with patch("app.connectors.sources.zammad.connector.ZammadApp"), \
             patch("app.connectors.sources.zammad.connector.SyncPoint", side_effect=lambda **_: FakeSyncPoint()):
            self.connector = ZammadConnector(
                logger=logging.getLogger("zammad-kb-permissions"),
                data_entities_processor=self.access,
                data_store_provider=AsyncMock(),
                config_service=AsyncMock(),
                connector_id=CONNECTOR_ID,
                scope="team",
                created_by="admin",
            )
        client = MagicMock()
        client.get_client.return_value = self.zammad
        client.get_base_url.return_value = "https://zammad.test"
        self.connector._get_fresh_datasource = AsyncMock(return_value=ZammadDataSource(client))
        self.connector.base_url = "https://zammad.test"
        self.connector.sync_filters = None
        self.connector.indexing_filters = None

    async def sync(self) -> None:
        await self.connector._sync_knowledge_bases()

    def category(self, category: int) -> set[tuple[str, str]]:
        return self.access.group_grants[f"cat_{category}"]

    def answer(self, answer_id: int) -> set[tuple[str, str]]:
        return self.access.record_grants[f"kb_answer_{answer_id}"]

    def kb_sync_point(self) -> int | None:
        return self.connector.kb_sync_point.points.get(KB_SYNC_POINT_KEY, {}).get("last_sync_time")


GENERAL_GRANTS = {("WRITER", f"ROLE {EDITORS}"), ("READER", f"ROLE {READERS}")}
EDITORS_ONLY = {("WRITER", f"ROLE {EDITORS}")}


@pytest.fixture
async def world() -> AsyncIterator[World]:
    w = World()
    await w.sync()
    assert w.category(GENERAL) == GENERAL_GRANTS
    assert w.category(BILLING) == EDITORS_ONLY
    assert w.answer(11) == EDITORS_ONLY  # archived: the category's editors only
    yield w


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_category_whose_permissions_fail_to_read_keeps_its_grants(world: World, status: int) -> None:
    world.zammad.permissions_status[GENERAL] = status
    world.zammad.category_roles[BILLING][AUDITORS] = "reader"

    await world.sync()

    assert world.category(GENERAL) == GENERAL_GRANTS
    # The other categories still update.
    assert world.category(BILLING) == EDITORS_ONLY | {("READER", f"ROLE {AUDITORS}")}


async def test_a_category_whose_permissions_get_no_answer_keeps_its_grants(world: World) -> None:
    world.zammad.permissions_unanswered.add(GENERAL)

    await world.sync()

    assert world.category(GENERAL) == GENERAL_GRANTS


async def test_an_archived_answer_in_that_category_keeps_its_grants_and_is_read_again(world: World) -> None:
    world.zammad.updated[11] = 20.0  # edited, so its grants are written again
    world.zammad.permissions_status[GENERAL] = 503

    await world.sync()

    assert world.answer(11) == EDITORS_ONLY
    # Not moved past the answer, so the next sync reads it again.
    assert world.kb_sync_point() <= int((T0 + timedelta(seconds=20)).timestamp() * 1000)

    del world.zammad.permissions_status[GENERAL]
    world.zammad.category_roles[GENERAL] = {AUDITORS: "editor"}
    await world.sync()

    assert world.answer(11) == {("WRITER", f"ROLE {AUDITORS}")}


@pytest.mark.parametrize("status", [403, 404])
async def test_a_category_zammad_refuses_or_no_longer_has_is_saved_with_no_role_grants(world: World, status: int) -> None:
    world.zammad.permissions_status[GENERAL] = status

    await world.sync()

    assert world.category(GENERAL) == set()


async def test_a_complete_read_removes_a_role_taken_off_a_category(world: World) -> None:
    del world.zammad.category_roles[GENERAL][READERS]

    await world.sync()

    assert world.category(GENERAL) == EDITORS_ONLY
