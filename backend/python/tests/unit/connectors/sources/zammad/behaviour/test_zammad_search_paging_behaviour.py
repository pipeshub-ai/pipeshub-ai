"""A group with more tickets than one search page is read in full, once, on Zammad 6.4.

The connector's ticket sync and the real ``ZammadDataSource`` run as they do
in production; only the HTTP layer is a fake Zammad. Like Zammad before 6.5,
its ``/api/v1/search`` ignores ``offset`` and answers every page with the
first one, while ``/api/v1/tickets/search`` pages by ``page``/``per_page``.
"""

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest

from app.connectors.sources.zammad.connector import ZammadConnector
from app.models.entities import Record, TicketRecord
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.zammad.zammad import ZammadDataSource

CONNECTOR_ID = "zm-paging"
GROUP_ID = 1
# Enough for two full pages of the connector's 50 and a short third one.
TICKETS = 120
# Far more search calls than a correct read needs; past it the fake refuses, so a runaway loop ends.
SEARCH_CALL_CAP = 40


class FakeHttpResponse:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self._body = body

    def text(self) -> str:
        return json.dumps(self._body)

    def json(self) -> object:
        return self._body


class FakeZammad64:
    """The parts of Zammad 6.4.1's REST API the ticket sync calls."""

    def __init__(self, ticket_count: int, *, pages_tickets_search: bool = True) -> None:
        self.tickets = [
            {
                "id": ticket_id,
                "number": str(10000 + ticket_id),
                "title": f"Ticket {ticket_id}",
                "group_id": GROUP_ID,
                "state_id": 1,
                "priority_id": 2,
                "customer_id": None,
                "owner_id": None,
                "created_at": "2024-01-01T00:00:00Z",
                # Pairs of tickets share a timestamp, as a bulk edit leaves them.
                "updated_at": f"2024-01-01T{ticket_id // 2 // 60:02d}:{ticket_id // 2 % 60:02d}:00Z",
            }
            for ticket_id in range(1, ticket_count + 1)
        ]
        self.pages_tickets_search = pages_tickets_search
        self.search_calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, request: HTTPRequest) -> FakeHttpResponse:
        path = urlparse(request.url).path
        params = dict(request.query_params)
        if path in ("/api/v1/search", "/api/v1/tickets/search"):
            self.search_calls.append((path, params))
            if len(self.search_calls) > SEARCH_CALL_CAP:
                raise RuntimeError("the test's search call cap was passed: the sync is not paging")
        if path == "/api/v1/search":
            return self._global_search(params)
        if path == "/api/v1/tickets/search":
            return self._tickets_search(params)
        if path.startswith("/api/v1/ticket_articles/by_ticket/"):
            return FakeHttpResponse(200, [])
        if path == "/api/v1/links":
            return FakeHttpResponse(200, {"links": [], "assets": {}})
        return FakeHttpResponse(404, {"error": f"no route {path}"})

    def _matching(self, query: str) -> list[dict[str, Any]]:
        group = re.fullmatch(r"group_id:(\d+)", query)
        assert group, f"unexpected ticket query {query!r}"
        matches = [t for t in self.tickets if t["group_id"] == int(group.group(1))]
        # Ticket.search's default order, with the id tie-break the connector asks for.
        return sorted(matches, key=lambda t: (t["updated_at"], t["id"]), reverse=True)

    def _global_search(self, params: dict[str, str]) -> FakeHttpResponse:
        # Zammad 6.4.1's SearchController never reads params[:offset].
        page = self._matching(params["query"])[: int(params.get("limit", 10))]
        return FakeHttpResponse(200, {
            "assets": {"Ticket": {str(t["id"]): dict(t) for t in page}},
            "result": [{"type": "Ticket", "id": t["id"]} for t in page],
        })

    def _tickets_search(self, params: dict[str, str]) -> FakeHttpResponse:
        assert params.get("expand") == "true"
        assert (params.get("sort_by"), params.get("order_by")) == ("updated_at,id", "desc,desc")
        per_page = min(int(params.get("per_page", 50)), 200)
        page = int(params.get("page", 1)) if self.pages_tickets_search else 1
        start = (page - 1) * per_page
        rows = self._matching(params["query"])[start:start + per_page]
        # Expanded tickets carry association names next to the ids.
        return FakeHttpResponse(200, [{**t, "group": "Support", "state": "open"} for t in rows])


class FakeRecords:
    def __init__(self) -> None:
        self.org_id = "org-1"
        self.written: list[Record] = []

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        return None

    async def on_new_records(self, batch: list[tuple[Record, list[Any]]]) -> None:
        self.written.extend(record for record, _ in batch)

    def ticket_ids(self) -> list[str]:
        return [r.external_record_id for r in self.written if isinstance(r, TicketRecord)]


class FakeSyncPoint:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def read_sync_point(self, key: str) -> dict[str, Any]:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.points[key] = dict(data)


@contextmanager
def _connector(zammad: FakeZammad64, records: FakeRecords) -> Iterator[ZammadConnector]:
    with patch("app.connectors.sources.zammad.connector.ZammadApp"), \
         patch("app.connectors.sources.zammad.connector.SyncPoint", side_effect=lambda **_: FakeSyncPoint()):
        connector = ZammadConnector(
            logger=logging.getLogger("zammad-paging-behaviour"),
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


async def _sync_group(connector: ZammadConnector) -> None:
    group = SimpleNamespace(external_group_id=f"group_{GROUP_ID}", name="Support")
    await connector._sync_tickets_for_groups([(group, [])])


async def test_every_ticket_in_a_group_larger_than_a_page_is_read_once() -> None:
    zammad, records = FakeZammad64(TICKETS), FakeRecords()
    with _connector(zammad, records) as connector:
        await _sync_group(connector)

    ticket_ids = records.ticket_ids()
    assert sorted(ticket_ids, key=int) == [str(i) for i in range(1, TICKETS + 1)]
    assert len(ticket_ids) == len(set(ticket_ids))
    assert [path for path, _ in zammad.search_calls] == ["/api/v1/tickets/search"] * 3
    assert [params["page"] for _, params in zammad.search_calls] == ["1", "2", "3"]
    newest = max(t["updated_at"] for t in zammad.tickets)
    checkpoint = connector.tickets_sync_point.points["Support"]["last_sync_time"]
    assert checkpoint == connector._parse_zammad_datetime(newest) + 1000


async def test_a_search_that_keeps_answering_with_the_first_page_stops_with_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    zammad, records = FakeZammad64(TICKETS, pages_tickets_search=False), FakeRecords()
    with _connector(zammad, records) as connector, caplog.at_level(logging.ERROR):
        await _sync_group(connector)

    assert len(zammad.search_calls) == 2
    ticket_ids = records.ticket_ids()
    assert len(ticket_ids) == 50 and len(set(ticket_ids)) == 50
    assert any("as on the page before, so it is not paging" in r.getMessage() for r in caplog.records)
