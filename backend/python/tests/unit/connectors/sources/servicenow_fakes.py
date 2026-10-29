"""Fakes for ServiceNow connector tests: a Table API that answers encoded queries the way ServiceNow does.

``FakeServiceNow`` holds rows per table and serves ``get_now_table_tableName``. It evaluates the parts of
the encoded query language the connector sends (``=``, ``!=``, ``>``, ``<``, ``>=``, ``<=``, ``IN``,
``ISEMPTY``, ``ISNOTEMPTY``, ``^OR``, ``^NQ``, ``ORDERBY``), cuts the page at ``sysparm_limit`` and only
then drops the rows an ACL hides (``_acl_hidden``), as ServiceNow does, so a page can come back short
while more rows follow. ``total_count`` (X-Total-Count) counts the matching rows before the ACL, as
ServiceNow does. With ``shuffle_ties`` rows that the ORDERBY does not tell apart come back in a
different order on every call, as a database without a total order returns them. Every call is logged
in ``calls`` (and its parameters in ``params``); ``after_call`` runs after each one (to change rows mid-walk); ``fail`` may return an
error to raise for a (table, query).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.sources.external.servicenow.models import (
    ServiceNowAPIError,
    TableAPIRecord,
    TableAPIResponse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_CONDITION = re.compile(r"^([a-z_][\w.]*?)(ISNOTEMPTY|ISEMPTY|IN|<=|>=|!=|=|>|<)(.*)$")


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
    return sorted(found, key=lambda r: tuple(str(r.get(f) or "") for f in order)) if order else found


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


class MemorySyncPoint:
    """A sync point that keeps its data in a dict."""

    def __init__(self, data: dict[str, dict[str, Any]] | None = None) -> None:
        self.data = dict(data or {})

    async def read_sync_point(self, key: str) -> dict[str, Any] | None:
        return self.data.get(key)

    async def update_sync_point(self, key: str, value: dict[str, Any]) -> None:
        self.data[key] = value
