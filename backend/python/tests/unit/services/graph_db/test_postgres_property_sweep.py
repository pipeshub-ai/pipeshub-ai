"""The PostgreSQL provider's reads for the property projection sweep: which statement is sent, with what arguments, and what is refused.

No database: ``PgBase._q`` is replaced by a recorder (the real closure runs against a fake transaction). What the statements select is
proved against a real database in tests/integration/graph_db/postgres/test_property_sweep.py.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("app.services.graph_db.postgres.provider")

from app.core.org_context import MissingOrgContext, OrgContext, bind
from app.services.graph_db.postgres.provider import PostgresGraphProvider

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

ORG = "org-1"


class Tx:
    def __init__(self, rows: list[dict], scalar: Any) -> None:  # noqa: ANN401
        self.sent: list[tuple[str, tuple]] = []
        self._rows, self._scalar = rows, scalar

    async def fetch(self, sql: str, *args: Any) -> list[dict]:  # noqa: ANN401
        self.sent.append((sql, args))
        return self._rows

    async def fetchval(self, sql: str, *args: Any) -> Any:  # noqa: ANN401
        self.sent.append((sql, args))
        return self._scalar


def provider(tx: Tx) -> PostgresGraphProvider:
    p = PostgresGraphProvider(logging.getLogger("pg-sweep-test"), MagicMock())

    async def q(fn: Any, transaction: Any = None, *, write: bool = False) -> Any:  # noqa: ANN401
        return await fn(tx)
    p._q = q
    return p


def org() -> AbstractContextManager:
    return bind(OrgContext(ORG, None, "job", "job:test", "t"))


async def test_the_seek_calls_its_vetted_function_with_the_cursor_in_the_documented_order() -> None:
    tx = Tx([{"node_id": "r1", "doc": json.dumps({"id": "r1", "connectorId": "c", "updatedAtTimestamp": 5})}], None)
    with org():
        rows = await provider(tx).get_records_updated_since("c", ORG, since=7, after_id="r0", props_hash="h", props_gen=3, limit=500)
    (sql, args), = tx.sent
    assert "graph.records_updated_since($1, $2, $3, $4, $5, $6)" in sql and sql.lstrip().startswith("/* pipeshub:graph.records_updated_since */")
    assert args == ("c", 7, "h", 3, "r0", 500), "connector, since, hash, generation, after id, limit: the function's argument order"
    assert rows[0]["_key"] == "r1" and rows[0]["updatedAtTimestamp"] == 5


async def test_the_walk_calls_its_vetted_function_and_an_absent_cursor_is_the_empty_string() -> None:
    tx = Tx([], None)
    with org():
        assert await provider(tx).get_records_needing_projection("c", ORG, after_key=None, props_hash="h", props_gen=3, limit=50) == []
    (sql, args), = tx.sent
    assert "graph.records_needing_projection($1, $2, $3, $4, $5)" in sql
    assert args == ("c", "h", 3, "", 50)


@pytest.mark.parametrize("name", ["get_records_updated_since", "get_records_needing_projection"])
async def test_a_read_outside_an_org_is_refused_before_any_statement(name: str) -> None:
    tx = Tx([], None)
    kw = {"since": 0, "after_id": ""} if name == "get_records_updated_since" else {"after_key": None}
    with pytest.raises(MissingOrgContext):
        await getattr(provider(tx), name)("c", ORG, props_hash="h", props_gen=1, limit=10, **kw)
    assert tx.sent == []


@pytest.mark.parametrize("name", ["get_records_updated_since", "get_records_needing_projection"])
async def test_a_page_over_the_functions_bound_raises_and_sends_nothing(name: str) -> None:
    """Kills: clamping the limit, which the sweep would read as a short page, the end of the walk."""
    tx = Tx([], None)
    kw = {"since": 0, "after_id": ""} if name == "get_records_updated_since" else {"after_key": None}
    with org(), pytest.raises(ValueError):
        await getattr(provider(tx), name)("c", ORG, props_hash="h", props_gen=1, limit=1001, **kw)
    assert tx.sent == []


async def test_the_index_check_reads_validity_of_the_revisions_index_and_writes_nothing() -> None:
    for valid in (True, False):
        tx = Tx([], valid)
        assert await provider(tx).record_change_index_online() is valid
        (sql, args), = tx.sent
        assert "indisvalid" in sql and args == ("graph.nodes_record_change",)
        assert "INSERT" not in sql.upper() and "CREATE" not in sql.upper()


async def test_ensure_runs_no_ddl_and_refuses_where_the_migration_has_not_run() -> None:
    present = Tx([], True)
    await provider(present).ensure_record_change_index()
    assert all("CREATE" not in sql.upper() for sql, _ in present.sent)
    with pytest.raises(RuntimeError, match="graph_0024"):
        await provider(Tx([], False)).ensure_record_change_index()


async def test_a_fractional_cursor_reaches_the_function_exactly() -> None:
    """The function takes numeric, and the cursor goes as the exact decimal of the stored number. Kills: int() on the cursor (the page is read again)
    and a float parameter (0.1 widened to its binary expansion)."""
    tx = Tx([], None)
    with org():
        await provider(tx).get_records_updated_since("c", ORG, since=1700000000000.5, after_id="a", props_hash="h", props_gen=1, limit=10)
        await provider(tx).get_records_updated_since("c", ORG, since=0.1, after_id="a", props_hash="h", props_gen=1, limit=10)
    assert [args[1] for _, args in tx.sent] == [Decimal("1700000000000.5"), Decimal("0.1")]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "7"])
async def test_a_cursor_that_is_not_a_finite_number_is_refused(bad: object) -> None:
    tx = Tx([], None)
    with org(), pytest.raises(ValueError):
        await provider(tx).get_records_updated_since("c", ORG, since=bad, after_id="", props_hash="h", props_gen=1, limit=10)
    assert tx.sent == []
