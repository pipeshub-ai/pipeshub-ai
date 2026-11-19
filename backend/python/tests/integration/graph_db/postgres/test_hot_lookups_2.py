"""The lookups made on every request or sync step go through the vetted reads of graph_0004 (row-level security keeps a plain statement from their
expression indexes). Their results are covered where those lookups are: test_records, test_identity, test_access and the SQL-level tests of backend/db.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging

import pytest

from app.core.db.uow import Tx
from app.services.graph_db.common.record_visibility import RecordVisibility

from .pg_support import OrgBound, StandInProvider, new_org

pytestmark = pytest.mark.integration


async def test_each_lookup_is_sent_to_its_vetted_read_and_a_pair_out_of_bounds_to_the_plain_statement(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p = OrgBound(StandInProvider(logging.getLogger("hot2"), pg_db), new_org())
    sent: list[str] = []
    for name in ("fetch", "fetchrow"):
        real = getattr(Tx, name)

        async def spy(self, sql, *args, _real=real) -> object:
            sent.append(sql)
            return await _real(self, sql, *args)
        monkeypatch.setattr(Tx, name, spy)
    await p.get_record_by_external_id("c1", "e1")
    await p.get_record_by_external_id("c1", "e1", visibility=RecordVisibility.ALL)
    await p.get_user_by_user_id("u1")
    labels = [s.split("pipeshub:graph.")[1].split(" ")[0] for s in sent if "pipeshub:graph." in s]
    assert labels == ["record_by_external", "record_by_external", "user_by_user_id"]
    sent.clear()
    await p.get_record_by_external_id("c1", "e" * 3000)           # longer than the function takes
    await p.get_user_by_user_id("u" * 300)
    assert [s.split("pipeshub:graph.")[1].split(" ")[0] for s in sent if "pipeshub:graph." in s] == ["get_record_by_external_id", "get_user_by_user_id"]


async def test_a_full_page_of_records_for_one_pair_is_read_by_the_plain_statement(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p = OrgBound(StandInProvider(logging.getLogger("hot2"), pg_db), new_org())
    sent: list[str] = []
    for name in ("fetch", "fetchrow"):
        real = getattr(Tx, name)

        async def spy(self, sql, *args, _real=real) -> object:
            sent.append(sql)
            return await _real(self, sql, *args)
        monkeypatch.setattr(Tx, name, spy)
    monkeypatch.setattr("app.services.graph_db.postgres.records._RECORD_BY_EXTERNAL_PAGE", 0)       # a page can never be short
    await p.get_record_by_external_id("c1", "e1")
    assert [s.split("pipeshub:graph.")[1].split(" ")[0] for s in sent if "pipeshub:graph." in s] == ["record_by_external", "get_record_by_external_id"]
