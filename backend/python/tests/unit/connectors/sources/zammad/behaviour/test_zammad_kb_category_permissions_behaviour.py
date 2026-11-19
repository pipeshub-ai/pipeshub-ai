"""Zammad knowledge-base categories whose permissions can't be read.

The connector reads every category's permissions (``GET .../categories/{id}/permissions``):
only that answer says which roles hold ``knowledge_base.editor`` and ``.reader``,
which a role with no row on the category falls back to. Writing a category replaces
its stored role grants, and an archived or draft answer is granted to the category's
editor roles, so a read that failed for a reason that passes must leave both as
stored, be tried again, and end the sync as failed. A read Zammad refuses (403) or
answers "gone" (404) saves the category with no role grants: access fails closed.

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
from app.connectors.sources.zammad import connector as zammad_connector
from app.connectors.sources.zammad.connector import KB_SYNC_POINT_KEY, ZammadConnector
from app.models.entities import Record
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.zammad.zammad import ZammadDataSource
from zammad_behaviour_fakes import T0, FakeHttpResponse, FakeRecordsDb

CONNECTOR_ID = "zm-kb-perms"
KB_ID = 1
GENERAL, BILLING = 1, 2
EDITORS, READERS, AUDITORS = 2, 3, 4
# The knowledge-base permission each role holds: it bounds what a row may give the role,
# and is the role's access to a category with no row for it.
ROLE_PERMISSIONS = {EDITORS: "editor", READERS: "reader", AUDITORS: "editor"}
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
        # category -> roles with no permission row there
        self.no_row: dict[int, set[int]] = {}
        # answer id -> timestamps set on top of its visibility field
        self.extra: dict[int, dict[str, str]] = {}

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
            answer.update(self.extra.get(answer_id, {}))
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
            return FakeHttpResponse(200, self.permissions(category))
        return FakeHttpResponse(404, {"error": f"no route {path}"})

    def permissions(self, category: int) -> dict[str, Any]:
        """What Zammad's permissions endpoint answers: the roles by KB permission, and the category's rows.

        A role left out of ``category_roles`` has a ``none`` row, unless it is in ``no_row``,
        where it has no row at all and so gets its default access.
        """
        rows = self.category_roles[category]
        return {
            "roles_editor": [{"id": r, "name": f"role {r}"} for r, p in ROLE_PERMISSIONS.items() if p == "editor"],
            "roles_reader": [{"id": r, "name": f"role {r}"} for r, p in ROLE_PERMISSIONS.items() if p == "reader"],
            "permissions": [
                {"id": 100 * category + r, "role_id": r, "access": rows.get(r, "none"),
                 "permissionable_type": "KnowledgeBase::Category", "permissionable_id": category}
                for r in ROLE_PERMISSIONS if r not in self.no_row.get(category, set())
            ],
            "inherited": [],
        }


def grant_set(permissions: list[Any]) -> set[tuple[str, str]]:
    return {(p.type.value, f"{p.entity_type.value} {p.external_id}") for p in permissions}


class KbAccess(FakeRecordsDb):
    """Writing a category or a record's permissions replaces its stored grants, as the processor does."""

    def __init__(self) -> None:
        super().__init__()
        self.group_grants: dict[str, set[tuple[str, str]]] = {}
        self.record_grants: dict[str, set[tuple[str, str]]] = {}
        self.answer_writes: list[str] = []

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.group_grants[group.external_group_id] = grant_set(permissions)
        await super().on_new_record_groups(groups)

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        for record, permissions in records_with_permissions:
            # Writing an existing record again only adds grants, as the processor does
            # outside a full sync; a replace goes through on_updated_record_permissions below.
            self.record_grants.setdefault(record.external_record_id, set()).update(grant_set(permissions))
            if "_attachment_" not in record.external_record_id:
                self.answer_writes.append(record.external_record_id)
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
        self.connector._incomplete = []  # run_sync starts every sync with none
        await self.connector._sync_knowledge_bases()

    def category(self, category: int) -> set[tuple[str, str]]:
        return self.access.group_grants[f"cat_{category}"]

    def answer(self, answer_id: int) -> set[tuple[str, str]]:
        return self.access.record_grants[f"kb_answer_{answer_id}"]

    def kb_sync_point(self) -> int | None:
        return self.connector.kb_sync_point.points.get(KB_SYNC_POINT_KEY, {}).get("last_sync_time")

    def incomplete(self) -> list[str]:
        return list(self.connector._incomplete)


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


@pytest.mark.parametrize("status", [429, 500, 503, None], ids=["429", "500", "503", "no-answer"])
async def test_a_category_whose_permissions_fail_to_read_makes_the_sync_incomplete(world: World, status: int | None) -> None:
    if status is None:
        world.zammad.permissions_unanswered.add(GENERAL)
    else:
        world.zammad.permissions_status[GENERAL] = status

    await world.sync()

    assert world.incomplete() == [f"the permissions of knowledge base category {GENERAL}"]


@pytest.mark.parametrize("status", [403, 404])
async def test_a_category_zammad_refuses_does_not_make_the_sync_incomplete(world: World, status: int) -> None:
    world.zammad.permissions_status[GENERAL] = status

    await world.sync()

    assert world.incomplete() == []


async def test_a_role_with_no_row_on_a_category_has_its_default_access(world: World) -> None:
    world.zammad.no_row[BILLING] = {READERS, AUDITORS}

    await world.sync()

    # AUDITORS holds knowledge_base.editor, READERS knowledge_base.reader.
    assert world.category(BILLING) == {
        ("WRITER", f"ROLE {EDITORS}"), ("WRITER", f"ROLE {AUDITORS}"), ("READER", f"ROLE {READERS}"),
    }


async def test_a_row_gives_a_role_no_more_than_its_own_kb_permission(world: World) -> None:
    world.zammad.category_roles[GENERAL][READERS] = "editor"

    await world.sync()

    assert world.category(GENERAL) == GENERAL_GRANTS


async def test_the_listings_permissions_effective_is_not_what_grants(world: World) -> None:
    listing = world.zammad._listing

    def with_effective_rows() -> dict[str, Any]:
        data = listing()
        for category in data["KnowledgeBaseCategory"].values():
            category["permissions_effective"] = [{"role_id": AUDITORS, "access": "editor"}]
        return data

    world.zammad._listing = with_effective_rows

    await world.sync()

    assert world.category(GENERAL) == GENERAL_GRANTS
    assert world.category(BILLING) == EDITORS_ONLY


# ---- archived and draft answers follow their category's editor roles ----------

async def test_an_archived_answer_is_written_again_when_its_category_editors_change(world: World) -> None:
    world.access.answer_writes.clear()
    world.zammad.category_roles[GENERAL] = {AUDITORS: "editor", READERS: "reader"}

    await world.sync()

    # 11 is archived in GENERAL, granted to its editors by name. 12 is internal and
    # inherits from the category; 21 is a draft in BILLING, whose editors did not change.
    assert world.access.answer_writes == ["kb_answer_11"]
    assert ("WRITER", f"ROLE {AUDITORS}") in world.answer(11)

    world.access.answer_writes.clear()
    await world.sync()
    assert world.access.answer_writes == []


async def test_an_archived_answer_loses_a_role_taken_off_its_category_editors(world: World) -> None:
    world.zammad.category_roles[GENERAL] = {AUDITORS: "editor", READERS: "reader"}

    await world.sync()

    assert world.answer(11) == {("WRITER", f"ROLE {AUDITORS}")}


async def test_every_answer_is_written_again_once_when_no_editors_were_recorded(world: World) -> None:
    """An install synced under the earlier rules (an archived answer stayed public) is rewritten once."""
    del world.connector.kb_sync_point.points[KB_SYNC_POINT_KEY]["category_editors"]
    world.access.answer_writes.clear()

    await world.sync()
    assert sorted(world.access.answer_writes) == ["kb_answer_11", "kb_answer_12", "kb_answer_21"]

    world.access.answer_writes.clear()
    await world.sync()
    assert world.access.answer_writes == []


def _at(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    now = int((T0 + timedelta(seconds=seconds)).timestamp() * 1000)
    monkeypatch.setattr(zammad_connector, "get_epoch_timestamp_in_ms", lambda: now)


async def test_a_scheduled_archive_takes_a_published_answer_out_of_everyones_reach(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _at(monkeypatch, 1_000)
    world.zammad.extra[21] = {"published_at": stamp(100)}
    world.zammad.updated[21] = 200.0
    await world.sync()
    assert world.answer(21) == {("READER", "ORG None")}

    # Archiving is scheduled: the answer is saved (updated_at moves) with archived_at ahead.
    world.zammad.extra[21]["archived_at"] = stamp(5_000)
    world.zammad.updated[21] = 2_000.0
    _at(monkeypatch, 3_000)
    await world.sync()
    assert world.answer(21) == {("READER", "ORG None")}

    _at(monkeypatch, 6_000)
    await world.sync()

    assert world.answer(21) == EDITORS_ONLY

