"""Every knowledge-base answer is read once on Zammad 6.4, and the sync ends.

The connector's knowledge-base sync and the real ``ZammadDataSource`` run as they
do in production; only the HTTP layer is a fake Zammad 6.4.1. Its ``/api/v1/search``
never reads ``offset``, so every page is the first one, as in Zammad's 6.0-6.4
``SearchController``. With ``search_resolves_name=False`` it answers
``objects=KnowledgeBaseAnswerTranslation`` with a 500, which is what a real 6.4.1
does: it looks the name up as a Ruby constant and finds none. ``POST
/api/v1/knowledge_bases/init`` lists every knowledge base, category and answer at
once, in the shape a real 6.4.1 sent back.
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from zammad_behaviour_fakes import T0, FakeHttpResponse, FakeRecordsDb

from app.connectors.sources.zammad.connector import KB_SYNC_POINT_KEY, ZammadConnector
from app.models.entities import Record, WebpageRecord
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.zammad.zammad import ZammadDataSource

CONNECTOR_ID = "zm-kb"
KB_ID = 1
# More than one page of the 50 answers the old sync asked /search for.
ANSWERS = 65
# Far more searches than any correct read needs; past it the fake refuses, so a runaway loop ends.
SEARCH_CALL_CAP = 30
# Category 3 sits under category 2, as Billing > Invoices did in the live check.
CATEGORIES = {1: ("General", None), 2: ("Billing", None), 3: ("Invoices", 2)}


def stamp(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeZammad64Kb:
    """The parts of Zammad 6.4.1's REST API the knowledge-base sync could call."""

    def __init__(self, answer_count: int, *, search_resolves_name: bool = True) -> None:
        self.search_resolves_name = search_resolves_name
        self.answers: dict[int, dict[str, Any]] = {}
        for answer_id in range(1, answer_count + 1):
            visibility = "published_at" if answer_id % 4 else "internal_at"
            self.answers[answer_id] = {
                "id": answer_id,
                "category_id": answer_id % 3 + 1,
                "translation_ids": [1000 + answer_id],
                "attachments": [],
                "tags": [],
                "created_at": stamp(answer_id),
                "updated_at": stamp(answer_id + 0.123),
                "published_at": None,
                "internal_at": None,
                "archived_at": None,
                visibility: stamp(answer_id),
            }
        self.init_status = 200
        self.init_body: object | None = None
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def edit_answer(self, answer_id: int, seconds: float) -> None:
        self.answers[answer_id]["updated_at"] = stamp(seconds)

    def _translations(self, answer_ids: list[int]) -> dict[str, dict[str, Any]]:
        return {
            str(1000 + a): {"id": 1000 + a, "answer_id": a, "kb_locale_id": 1, "content_id": 2000 + a,
                            "title": f"KB answer {a}", "updated_at": self.answers[a]["updated_at"]}
            for a in answer_ids
        }

    def _structure(self) -> dict[str, Any]:
        return {
            "KnowledgeBase": {str(KB_ID): {"id": KB_ID, "translation_ids": [1], "kb_locale_ids": [1],
                                           "category_ids": list(CATEGORIES)}},
            "KnowledgeBaseTranslation": {"1": {"id": 1, "kb_locale_id": 1, "title": "Help Center"}},
            "KnowledgeBaseLocale": {"1": {"id": 1, "system_locale_id": 1, "primary": True}},
            "KnowledgeBaseCategory": {
                str(cid): {"id": cid, "knowledge_base_id": KB_ID, "parent_id": parent, "translation_ids": [100 + cid],
                           "permissions_effective": [{"role_id": 1, "access": "editor"}]}
                for cid, (_, parent) in CATEGORIES.items()
            },
            "KnowledgeBaseCategoryTranslation": {
                str(100 + cid): {"id": 100 + cid, "category_id": cid, "kb_locale_id": 1, "title": title}
                for cid, (title, _) in CATEGORIES.items()
            },
        }

    async def execute(self, request: HTTPRequest) -> FakeHttpResponse:
        path = urlparse(request.url).path
        params = dict(request.query_params or {})
        self.calls.append((request.method, path, params))
        if path == "/api/v1/search":
            if len(self.searches()) > SEARCH_CALL_CAP:
                raise RuntimeError("the test's search call cap was passed: the sync is not paging")
            return self._global_search(params)
        if path == "/api/v1/knowledge_bases/init" and request.method == "POST":
            if self.init_body is not None:
                return FakeHttpResponse(self.init_status, self.init_body)
            return FakeHttpResponse(self.init_status, {
                **self._structure(),
                "KnowledgeBaseAnswer": {str(a): dict(data) for a, data in self.answers.items()},
                "KnowledgeBaseAnswerTranslation": self._translations(list(self.answers)),
            })
        if re.fullmatch(r"/api/v1/knowledge_bases/\d+/categories/\d+/permissions", path):
            return FakeHttpResponse(200, {"permissions": [{"role_id": 1, "access": "editor"}]})
        return FakeHttpResponse(404, {"error": f"no route {path}"})

    def searches(self) -> list[dict[str, str]]:
        return [params for _, path, params in self.calls if path == "/api/v1/search"]

    def _global_search(self, params: dict[str, str]) -> FakeHttpResponse:
        if params.get("objects") == "KnowledgeBaseAnswerTranslation" and not self.search_resolves_name:
            return FakeHttpResponse(500, {"error": "uninitialized constant KnowledgeBaseAnswerTranslation"})
        # Zammad 6.4.1's SearchController passes only limit and ids on: offset is never read.
        newest_first = sorted(self.answers, key=lambda a: self.answers[a]["updated_at"], reverse=True)
        page = newest_first[: int(params.get("limit", 10))]
        return FakeHttpResponse(200, {
            "assets": {
                **self._structure(),
                "KnowledgeBaseAnswer": {str(a): dict(self.answers[a]) for a in page},
                "KnowledgeBaseAnswerTranslation": self._translations(page),
            },
            "result": [{"type": "KnowledgeBaseAnswerTranslation", "id": 1000 + a} for a in page],
        })


class KbRecords(FakeRecordsDb):
    def __init__(self) -> None:
        super().__init__()
        self.answer_writes: list[str] = []
        self.fail_lookup_for: set[str] = set()

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError("graph unavailable")
        return await super().get_record_by_external_id(connector_id, external_record_id)

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        self.answer_writes.extend(
            r.external_record_id for r, _ in records_with_permissions if isinstance(r, WebpageRecord)
        )
        await super().on_new_records(records_with_permissions)

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        return None


class FakeSyncPoint:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def read_sync_point(self, key: str) -> dict[str, Any]:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.points.setdefault(key, {}).update(data)


@contextmanager
def _connector(zammad: FakeZammad64Kb, records: KbRecords) -> Iterator[ZammadConnector]:
    with patch("app.connectors.sources.zammad.connector.ZammadApp"), \
         patch("app.connectors.sources.zammad.connector.SyncPoint", side_effect=lambda **_: FakeSyncPoint()):
        connector = ZammadConnector(
            logger=logging.getLogger("zammad-kb-behaviour"),
            data_entities_processor=records,
            data_store_provider=AsyncMock(),
            config_service=AsyncMock(),
            connector_id=CONNECTOR_ID,
            scope="team",
            created_by="admin",
        )
    client = MagicMock()
    client.get_client.return_value = zammad
    client.get_base_url.return_value = "https://zammad.test"
    connector._get_fresh_datasource = AsyncMock(return_value=ZammadDataSource(client))
    connector.base_url = "https://zammad.test"
    connector.sync_filters = None
    connector.indexing_filters = None
    yield connector


def _kb_sync_point(connector: ZammadConnector) -> int | None:
    return connector.kb_sync_point.points.get(KB_SYNC_POINT_KEY, {}).get("last_sync_time")


def _newest(zammad: FakeZammad64Kb, connector: ZammadConnector) -> int:
    return max(connector._parse_zammad_datetime(a["updated_at"]) for a in zammad.answers.values())


@pytest.mark.parametrize("search_resolves_name", [True, False], ids=["search-ignores-offset", "search-500s"])
async def test_every_answer_in_a_knowledge_base_larger_than_a_page_is_read_once(search_resolves_name: bool) -> None:
    zammad, records = FakeZammad64Kb(ANSWERS, search_resolves_name=search_resolves_name), KbRecords()
    with _connector(zammad, records) as connector:
        await connector._sync_knowledge_bases()

    assert sorted(records.answer_writes) == sorted(f"kb_answer_{a}" for a in range(1, ANSWERS + 1))
    assert len(records.answer_writes) == len(set(records.answer_writes))
    assert zammad.searches() == []
    assert [(m, p) for m, p, _ in zammad.calls] == [("POST", "/api/v1/knowledge_bases/init")]
    assert _kb_sync_point(connector) == _newest(zammad, connector) + 1000
    # Answers land in their category; the nested one hangs under its parent category.
    assert records.records["kb_answer_2"].external_record_group_id == "cat_3"
    assert records.record_groups["cat_3"].parent_external_group_id == "cat_2"


async def test_an_install_upgraded_from_the_search_based_sync_reads_every_answer_once() -> None:
    zammad, records = FakeZammad64Kb(ANSWERS, search_resolves_name=False), KbRecords()
    with _connector(zammad, records) as connector:
        # The old sync saved this after reading nothing, so it is newer than every answer.
        connector.kb_sync_point.points["kb_sync"] = {"last_sync_time": _newest(zammad, connector) + 60_000}
        await connector._sync_knowledge_bases()

    assert sorted(records.answer_writes) == sorted(f"kb_answer_{a}" for a in range(1, ANSWERS + 1))


async def test_the_next_sync_reads_only_the_answers_edited_since_and_ends() -> None:
    zammad, records = FakeZammad64Kb(ANSWERS), KbRecords()
    with _connector(zammad, records) as connector:
        await connector._sync_knowledge_bases()
        records.answer_writes.clear()
        zammad.edit_answer(7, 3600)
        zammad.edit_answer(30, 3600.5)
        await connector._sync_knowledge_bases()
        edited = list(records.answer_writes)
        records.answer_writes.clear()
        await connector._sync_knowledge_bases()

    assert sorted(edited) == ["kb_answer_30", "kb_answer_7"]
    assert records.answer_writes == []
    assert _kb_sync_point(connector) == connector._parse_zammad_datetime(stamp(3600.5)) + 1000
    assert zammad.searches() == []


@pytest.mark.parametrize(("status", "body"), [
    (500, {"error": "internal server error"}),
    # An answer that is not an object: skipping it would move the sync point past it.
    (200, {"KnowledgeBaseAnswer": {"1": {"id": 1, "updated_at": stamp(10_000)}, "2": "not an answer"}}),
], ids=["listing-fails", "non-object-answer"])
async def test_a_listing_that_cannot_be_read_leaves_the_sync_point_alone(
    status: int, body: object, caplog: pytest.LogCaptureFixture,
) -> None:
    zammad, records = FakeZammad64Kb(ANSWERS), KbRecords()
    with _connector(zammad, records) as connector, caplog.at_level(logging.WARNING):
        connector.kb_sync_point.points[KB_SYNC_POINT_KEY] = {"last_sync_time": 5_000}
        zammad.init_status, zammad.init_body = status, body
        await connector._sync_knowledge_bases()

    assert records.answer_writes == []
    assert _kb_sync_point(connector) == 5_000
    assert any("Knowledge base not synced" in r.getMessage() for r in caplog.records)


async def test_an_answer_that_fails_to_write_is_read_again_on_the_next_sync() -> None:
    zammad, records = FakeZammad64Kb(ANSWERS), KbRecords()
    records.fail_lookup_for = {"kb_answer_20"}
    with _connector(zammad, records) as connector:
        await connector._sync_knowledge_bases()
        first = list(records.answer_writes)
        held_at = _kb_sync_point(connector)
        records.fail_lookup_for.clear()
        records.answer_writes.clear()
        await connector._sync_knowledge_bases()

    assert "kb_answer_20" not in first and len(first) == ANSWERS - 1
    # Held at the failed answer, not past the newer answers that were written.
    assert held_at == connector._parse_zammad_datetime(zammad.answers[20]["updated_at"])
    assert "kb_answer_20" in records.answer_writes
    assert _kb_sync_point(connector) == _newest(zammad, connector) + 1000
