"""Fakes for ServiceNow connector tests: a Table API that answers encoded queries the way ServiceNow does.

``FakeServiceNow`` holds rows per table and serves ``get_now_table_tableName``. It evaluates the parts of
the encoded query language the connector sends (``=``, ``!=``, ``>``, ``<``, ``>=``, ``<=``, ``IN``,
``NOT IN``, ``ISEMPTY``, ``ISNOTEMPTY``, ``^OR``, ``^NQ``, ``ORDERBY``, ``ORDERBYDESC``), cuts the page at ``sysparm_limit`` and only
then drops the rows an ACL hides (``_acl_hidden``), as ServiceNow does, so a page can come back short
while more rows follow. ``total_count`` (X-Total-Count) counts the matching rows before the ACL, as
ServiceNow does. With ``shuffle_ties`` rows that the ORDERBY does not tell apart come back in a
different order on every call, as a database without a total order returns them. Every call is logged
in ``calls`` (and its parameters in ``params``); ``after_call`` runs after each one (to change rows mid-walk); ``fail`` may return an
error to raise for a (table, query).

``FakeRecordStore`` keeps the records a sync writes, by external id, and answers the processor calls the
connector makes to read and delete them. ``migrated_sync_point`` is the article sync point of an install
whose articles are already keyed by number and were reconciled just now.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx

from app.sources.external.servicenow.models import (
    ServiceNowAPIError,
    TableAPIRecord,
    TableAPIResponse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_CONDITION = re.compile(r"^([a-z_][\w.]*?)(ISNOTEMPTY|ISEMPTY|NOT IN|IN|<=|>=|!=|=|>|<)(.*)$")


def _holds(row: dict[str, Any], condition: str) -> bool:
    m = _CONDITION.match(condition)
    if not m:
        raise ValueError(f"fake ServiceNow cannot read the condition {condition!r}")
    name, op, value = m.groups()
    have = str(row.get(name) if row.get(name) is not None else "")
    if op == "ISNOTEMPTY":
        return have != ""
    if op == "ISEMPTY":
        return have == ""
    if op == "IN":
        return have in value.split(",")
    if op == "NOT IN":
        return have not in value.split(",")
    return {
        "=": have.lower() == value.lower(),  # ServiceNow compares = case-insensitively
        "!=": have.lower() != value.lower(),
        ">": have > value,
        "<": have < value,
        ">=": have >= value,
        "<=": have <= value,
    }[op]


def _block_holds(row: dict[str, Any], block: str) -> bool:
    groups: list[list[str]] = []
    for token in filter(None, block.split("^")):
        if token.startswith("OR") and groups:
            groups[-1].append(token[2:])
        else:
            groups.append([token])
    return all(any(_holds(row, c) for c in group) for group in groups)


def matching(rows: list[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    """The rows ``query`` selects, in its ORDERBY order (stable for ties)."""
    tokens = (query or "").split("^")
    order = [t[len("ORDERBY"):] for t in tokens if t.startswith("ORDERBY")]
    where = "^".join(t for t in tokens if not t.startswith("ORDERBY"))
    blocks = where.split("^NQ") if where else [""]
    found = [r for r in rows if any(_block_holds(r, b) for b in blocks)]
    for f in reversed(order):  # stable sorts, last key first
        desc = f.startswith("DESC")
        found = sorted(found, key=lambda r, f=f.removeprefix("DESC"): str(r.get(f) or ""), reverse=desc)
    return found


@dataclass
class FakeServiceNow:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    shuffle_ties: bool = False
    after_call: Callable[[FakeServiceNow], None] | None = None
    fail: Callable[[str, str | None], Exception | None] | None = None
    calls: list[tuple[str, str | None, int]] = field(default_factory=list)
    params: list[dict[str, str | None]] = field(default_factory=list)
    _rng: random.Random = field(default_factory=lambda: random.Random(7))

    def calls_to(self, table: str) -> list[str | None]:
        return [q for t, q, _ in self.calls if t == table]

    async def get_now_table_tableName(
        self, tableName: str, sysparm_query: str | None = None, sysparm_fields: str | None = None,
        sysparm_limit: str | None = None, sysparm_offset: str | None = None, **rest: str | None,
    ) -> TableAPIResponse:
        limit = int(sysparm_limit or 10000)
        self.calls.append((tableName, sysparm_query, limit))
        self.params.append({"sysparm_offset": sysparm_offset, **rest})
        error = self.fail(tableName, sysparm_query) if self.fail else None
        if error:
            raise error
        rows = list(self.tables.get(tableName, []))
        if self.shuffle_ties:
            self._rng.shuffle(rows)
        offset = int(sysparm_offset or 0)
        matched = matching(rows, sysparm_query)
        visible = [r for r in matched[offset:offset + limit] if not r.get("_acl_hidden")]
        names = sysparm_fields.split(",") if sysparm_fields else None
        response = TableAPIResponse(result=[
            TableAPIRecord(**({n: r.get(n, "") for n in names} if names else
                              {k: v for k, v in r.items() if not k.startswith("_")}))
            for r in visible
        ], total_count=len(matched))
        if self.after_call:
            self.after_call(self)
        return response


def keyset_page(rows: list[dict[str, Any]], sysparm_query: str | None, sysparm_limit: str | None) -> list[dict[str, Any]]:
    """The page of ``rows`` (already in (sys_updated_on, sys_id) order) after the keyset cursor in the query."""
    m = re.search(r"sys_updated_on=([^^]*)\^sys_id>([^^]*)", sysparm_query or "")
    if m:
        rows = [r for r in rows if (r.get("sys_updated_on", ""), r.get("sys_id", "")) > (m[1], m[2])]
    return rows[: int(sysparm_limit or 10000)]


def status_error(status: int, message: str = "error") -> ServiceNowAPIError:
    return ServiceNowAPIError(status_code=status, message=message, details=None)


def file_response(content: bytes, content_type: str = "application/pdf") -> httpx.Response:
    """An opened attachment download, as ``ServiceNowDataSource.stream_attachment`` returns it."""
    return httpx.Response(200, content=content, headers={"content-type": f"{content_type};charset=UTF-8"})


def block_groups(content: bytes) -> list[dict[str, Any]]:
    """The block groups of a streamed article."""
    return json.loads(content)["block_groups"]


class MemorySyncPoint:
    """A sync point that keeps its data in a dict."""

    def __init__(self, data: dict[str, dict[str, Any]] | None = None) -> None:
        self.data = dict(data or {})

    async def read_sync_point(self, key: str) -> dict[str, Any] | None:
        return self.data.get(key)

    async def update_sync_point(self, key: str, value: dict[str, Any]) -> None:
        self.data[key] = value


def migrated_sync_point(checkpoint: str | None = None, **data: dict[str, Any]) -> MemorySyncPoint:
    """The article sync point of an install whose articles are keyed by number, reconciled just now."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stored: dict[str, dict[str, Any]] = {
        "article_key/v2": {"done": True}, "articles_reconcile/last": {"last_sync_time": now}, **data,
    }
    if checkpoint:
        stored["articles"] = {"last_sync_time": checkpoint}
    return MemorySyncPoint(stored)


def stored_record(external_id: str, record_type: str = "WEBPAGE", parent: str | None = None,
                  group: str | None = None) -> SimpleNamespace:
    """A record as the store returns it."""
    return SimpleNamespace(id=f"id-{external_id}", external_record_id=external_id, record_type=record_type,
                           parent_external_record_id=parent, external_record_group_id=group, is_placeholder=False)


class FakeRecordStore:
    """The connector's stored records, by external id. ``attach`` points a mocked processor's record
    calls here. A record deleted by external id takes the attachments under it, as the processor's
    cascade does; ``on_record_deleted`` takes the record alone. Writing a record that exists keeps
    its id and moves it under its new parent."""

    def __init__(self, *records: SimpleNamespace) -> None:
        self.records: dict[str, Any] = {r.external_record_id: r for r in records}
        self.permissions: dict[str, list] = {}
        self.deleted: list[str] = []
        self.deleted_groups: list[str] = []

    def attach(self, processor: object) -> FakeRecordStore:
        processor.on_new_records = AsyncMock(side_effect=self.on_new_records)
        processor.get_record_by_external_id = AsyncMock(side_effect=self.get_record_by_external_id)
        processor.delete_record_by_external_id = AsyncMock(side_effect=self.delete_record_by_external_id)
        processor.on_record_deleted = AsyncMock(side_effect=self.on_record_deleted)
        processor.get_records_by_parent = AsyncMock(side_effect=self.get_records_by_parent)
        processor.get_records_by_record_type = AsyncMock(side_effect=self.get_records_by_record_type)
        processor.on_record_group_deleted = AsyncMock(side_effect=self.on_record_group_deleted)
        return self

    def keys(self, record_type: str | None = None) -> list[str]:
        return sorted(k for k, r in self.records.items() if record_type is None or r.record_type == record_type)

    async def on_new_records(self, pairs: list[tuple[Any, list]]) -> None:
        for record, permissions in pairs:
            existing = self.records.get(record.external_record_id)
            if existing is not None:
                record.id = existing.id
            self.records[record.external_record_id] = record
            self.permissions[record.external_record_id] = permissions

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str, **_: object) -> object:
        return self.records.get(external_record_id)

    async def delete_record_by_external_id(self, connector_id: str, external_id: str, user_id: str | None = None) -> None:
        if self.records.pop(external_id, None) is not None:
            self.deleted.append(external_id)
        for key, child in list(self.records.items()):
            if child.parent_external_record_id == external_id and child.record_type == "FILE":
                del self.records[key]
                self.deleted.append(key)

    async def on_record_deleted(self, record_id: str) -> bool:
        for key, record in list(self.records.items()):
            if record.id == record_id:
                del self.records[key]
                self.deleted.append(key)
        return False

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str,
                                    record_type: str | None = None, **_: object) -> list[Any]:
        return [r for r in self.records.values() if r.parent_external_record_id == parent_external_record_id
                and (record_type is None or r.record_type == record_type)]

    async def get_records_by_record_type(self, connector_id: str, record_type: str) -> list[Any]:
        return [r for r in self.records.values() if r.record_type == record_type]

    async def on_record_group_deleted(self, external_group_id: str, connector_id: str, **_: object) -> bool:
        self.deleted_groups.append(external_group_id)
        for key, record in list(self.records.items()):
            if record.external_record_group_id == external_group_id:
                del self.records[key]
                self.deleted.append(key)
        return True
