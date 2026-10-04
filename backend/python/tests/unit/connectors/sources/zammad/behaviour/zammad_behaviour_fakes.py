"""Fakes for behaviour tests of the Zammad connector's ticket removal.

Two things are faked. Zammad is an in-memory set of groups and tickets behind
the ``ZammadDataSource`` methods the ticket sync calls, with a search index that
can lag behind the tickets, as Zammad's Elasticsearch index does. Our databases
are in-memory stand-ins that hand back what the real stores hand back: a plain
``Record`` for a lookup by external id or by record group, never the
``TicketRecord`` or ``FileRecord`` that was written, and sync points that refuse
values Neo4j cannot hold as a node property.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.models.entities import Record
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)
from app.sources.client.zammad.zammad import ZammadResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_ms(day: int) -> int:
    return int(T0.timestamp() * 1000) + day * 86_400_000


@dataclass
class FakeZammad:
    groups: dict[int, str] = field(default_factory=dict)
    tickets: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Ticket ids the search index has not caught up with.
    unindexed: set[int] = field(default_factory=set)
    fail_search: Callable[[str], bool] = lambda _query: False
    # Elasticsearch's index.max_result_window: a page past it is a 400.
    result_window: int = 10_000
    fail_articles_for: set[int] = field(default_factory=set)
    fail_search_at_offset: int | None = None
    fail_list_groups: bool = False
    ticket_read_status: dict[int, int] = field(default_factory=dict)
    search_queries: list[str] = field(default_factory=list)
    refused_searches: list[tuple[str, int | None, int | None]] = field(default_factory=list)
    ticket_reads: list[int] = field(default_factory=list)
    # Groups Zammad lists as inactive.
    inactive: set[int] = field(default_factory=set)
    # Zammad before 6.5 ignores only_total_count and answers with a page of tickets.
    supports_count: bool = True
    count_calls: list[tuple[str, list[int]]] = field(default_factory=list)

    def add_ticket(self, ticket_id: int, group_id: int, *, day: int = 0, minute: int = 0,
                   attachments: int = 0) -> None:
        stamp = iso(epoch_ms(day) + minute * 60_000)
        self.tickets[ticket_id] = {
            "id": ticket_id,
            "title": f"ticket {ticket_id}",
            "group_id": group_id,
            "created_at": stamp,
            "updated_at": stamp,
            "attachments": attachments,
        }

    def delete_ticket(self, ticket_id: int) -> None:
        del self.tickets[ticket_id]

    def move_ticket(self, ticket_id: int, group_id: int, *, day: int) -> None:
        self.tickets[ticket_id].update(group_id=group_id, updated_at=iso(epoch_ms(day)))

    # ---- ZammadDataSource surface ----

    async def list_groups(self, page: int | None = None, per_page: int | None = None) -> ZammadResponse:
        if self.fail_list_groups:
            return ZammadResponse(success=False, message="list_groups failed", status_code=502)
        rows = [{"id": gid, "name": name, "active": gid not in self.inactive}
                for gid, name in sorted(self.groups.items())]
        start = ((page or 1) - 1) * (per_page or 100)
        return ZammadResponse(success=True, data=rows[start:start + (per_page or 100)])

    async def get_group(self, group_id: int) -> ZammadResponse:
        return ZammadResponse(success=True, data={"id": group_id, "user_ids": []})

    async def search_tickets(self, query: str, limit: int | None = None, offset: int | None = None) -> ZammadResponse:
        # ZammadDataSource.search_tickets turns these into page/per_page and refuses what can't be.
        if not 1 <= (limit or 50) <= 200 or (offset or 0) % (limit or 50):
            self.refused_searches.append((query, limit, offset))
            return ZammadResponse(success=False, message="search_tickets failed: invalid limit or offset")
        self.search_queries.append(query)
        if self.fail_search(query) or (offset or 0) == self.fail_search_at_offset:
            return ZammadResponse(success=False, message="search failed", status_code=500)
        if (offset or 0) + (limit or 50) > self.result_window:
            return ZammadResponse(success=False, message="search failed", status_code=400)
        hits = [self._public(t) for t in self._matching(query)]
        start = offset or 0
        return ZammadResponse(success=True, data=hits[start:start + (limit or 50)])

    async def count_tickets(self, query: str, ids: list[int] | None = None) -> ZammadResponse:
        self.count_calls.append((query, list(ids or [])))
        if self.fail_search(query):
            return ZammadResponse(success=False, message="count failed", status_code=500)
        if not self.supports_count:
            return ZammadResponse(success=True, data=None, status_code=200)
        wanted = None if ids is None else set(ids)
        total = sum(1 for t in self._matching(query) if wanted is None or t["id"] in wanted)
        return ZammadResponse(success=True, data={"total_count": total}, status_code=200)

    def _matching(self, query: str) -> list[dict[str, Any]]:
        """Indexed tickets a search query matches, newest updated_at first, then highest id."""
        group = int(re.search(r"group_id:(\d+)", query).group(1))
        id_range = re.search(r"\bid:\[(\d+) TO (\d+|\*)\]", query)
        bounds = {
            (field_name, side): datetime.fromisoformat(value.replace("Z", "+00:00"))
            for field_name, side, value in (
                [(m.group(1), "after", m.group(2)) for m in re.finditer(r"(\w+)_at:\[(\S+) TO \*\]", query)]
                + [(m.group(1), "before", m.group(2)) for m in re.finditer(r"(\w+)_at:\[\* TO (\S+)\]", query)]
            )
        }

        def matches(ticket: dict[str, Any]) -> bool:
            for (field_name, side), bound in bounds.items():
                value = datetime.fromisoformat(ticket[f"{field_name}_at"].replace("Z", "+00:00"))
                if (side == "after" and value < bound) or (side == "before" and value > bound):
                    return False
            return True

        hits = [
            t for tid, t in self.tickets.items()
            if t["group_id"] == group and tid not in self.unindexed and matches(t)
            and (id_range is None or (int(id_range.group(1)) <= tid
                                      and (id_range.group(2) == "*" or tid <= int(id_range.group(2)))))
        ]
        # The sort search_tickets asks for: updated_at desc, id desc.
        return sorted(hits, key=lambda t: (t["updated_at"], t["id"]), reverse=True)

    async def get_ticket(self, id: int, expand: bool | None = None) -> ZammadResponse:  # noqa: A002 - mirrors the real signature
        self.ticket_reads.append(id)
        status = self.ticket_read_status.get(id)
        if status:
            return ZammadResponse(success=False, message="get_ticket failed", status_code=status)
        if id not in self.tickets:
            return ZammadResponse(
                success=False, data={"error": f"Couldn't find Ticket with 'id'={id}"},
                message="get_ticket failed", status_code=404,
            )
        return ZammadResponse(success=True, data=self._public(self.tickets[id]), status_code=200)

    async def list_ticket_articles(self, ticket_id: int) -> ZammadResponse:
        if ticket_id in self.fail_articles_for:
            return ZammadResponse(success=False, message="list_ticket_articles failed", status_code=500)
        count = self.tickets[ticket_id]["attachments"] if ticket_id in self.tickets else 0
        attachments = [
            {"id": n, "filename": f"file-{ticket_id}-{n}.txt", "size": 3,
             "preferences": {"Content-Type": "text/plain"}}
            for n in range(1, count + 1)
        ]
        return ZammadResponse(success=True, data=[{"id": 1, "sender": "Customer", "attachments": attachments}])

    async def list_links(self, link_object: str, link_object_value: int) -> ZammadResponse:
        return ZammadResponse(success=True, data={"links": [], "assets": {}})

    @staticmethod
    def _public(ticket: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in ticket.items() if k != "attachments"}


class FakeRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor`` and the graph behind it."""

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, Any] = {}  # external id -> record as written
        self.record_groups: dict[str, Any] = {}
        self.deleted: list[str] = []  # external ids, in delete order
        self.fail_delete_for: set[str] = set()
        self.group_page_reads: list[str] = []

    def external_ids(self) -> set[str]:
        return set(self.records)

    @staticmethod
    def _plain(record: Record) -> Record:
        return Record.model_validate(record.model_dump(include=set(Record.model_fields)))

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        found = self.records.get(external_record_id)
        return None if found is None else self._plain(found)

    async def get_records_in_record_group(
        self, connector_id: str, external_group_id: str, limit: int, after_key: str | None = None,
        *, visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Record]:
        """Keyset pages ordered by record id, as ``get_records_by_status`` returns them."""
        self.group_page_reads.append(external_group_id)
        if external_group_id not in self.record_groups:
            return []
        members = sorted(
            (
                r for r in self.records.values()
                if r.external_record_group_id == external_group_id and matches_visibility(r, visibility)
            ),
            key=lambda r: r.id,
        )
        if after_key is not None:
            members = [r for r in members if r.id > after_key]
        return [self._plain(r) for r in members[:limit]]

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        for record, _ in records_with_permissions:
            existing = self.records.get(record.external_record_id)
            if existing is not None:
                record.id = existing.id
            self.records[record.external_record_id] = record.model_copy(deep=True)

    async def on_record_deleted(self, record_id: str) -> None:
        record = next((r for r in self.records.values() if r.id == record_id), None)
        if record is None:
            return
        if record.external_record_id in self.fail_delete_for:
            raise RuntimeError(f"delete failed for {record.external_record_id}")
        del self.records[record.external_record_id]
        self.deleted.append(record.external_record_id)

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, _ in groups:
            self.record_groups.setdefault(group.external_group_id, group)

    async def on_new_user_groups(self, user_groups: list[Any]) -> None:
        return None

    async def on_new_app_users(self, users: list[Any]) -> None:
        return None


def _neo4j_property(value: object) -> bool:
    primitive = (str, int, float, bool)
    if isinstance(value, list):
        return all(isinstance(v, primitive) for v in value) and len({type(v) for v in value}) <= 1
    return value is None or isinstance(value, primitive)


class FakeStore:
    """Sync points behind ``DataStoreProvider.transaction()``, refusing what Neo4j refuses."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> dict[str, Any] | None:
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        for name, value in data.items():
            if not _neo4j_property(value):
                raise TypeError(f"Neo4j cannot store {name!r} as a property: {value!r}")
        self.sync_points.setdefault(key, {}).update(data)

    def clear(self) -> None:
        """What saving new filters does: ``delete_sync_points_by_connector_id``."""
        self.sync_points.clear()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[FakeStore]:
        yield self


class FakeConfigService:
    def __init__(self) -> None:
        self.sync_filters: dict[str, Any] = {}

    async def get_config(self, path: str, *args: object, **kwargs: object) -> dict[str, Any]:
        return {
            "auth": {"authType": "API_TOKEN", "baseUrl": "https://zammad.test", "token": "t"},
            "filters": {"sync": {"values": self.sync_filters}, "indexing": {"values": {}}},
        }
