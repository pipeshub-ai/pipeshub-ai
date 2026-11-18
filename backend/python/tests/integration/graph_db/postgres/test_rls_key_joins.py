"""Statements that join a node by a key held in another node's document read it by its primary key under row security.

The services run with row security on graph.nodes, and PostgreSQL does not use a qual that applies a function which is not leakproof (``->>``) to the
other side as an index condition there: ``u.id = r.doc->>'deletedByUserId'`` is a join filter over a scan of every user of the organisation (seen on
17.12 and 18.7). Each statement carries the key as a plain column of a subquery the planner keeps (``r.deleted_by``), and ``u.id = r.deleted_by`` is
an index condition. Each plan here is read as the service runs it (the connectors login, row security on, the custom plan for the values asked), on
an organisation with 20,000 users and 20,000 records, and every read of the joined collection must be bounded by its key (:mod:`plan_gate`).

Needs a migrated database: PG_CORE_URL (a ``pipeshub_connectors_login`` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.core.db import uow
from tests.support import plan_gate

from .test_access import w as w

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration

KEY = r"\bid = "        # the primary key's id, after org_id and coll


async def _bulk(pg_db, w: World) -> None:
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'users', 'u' || g, jsonb_build_object('fullName', 'user ' || g, 'email', 'u' || g || '@example.com')
        FROM generate_series(1, 20000) g""", w.org), write=True)
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'records', 'r' || g, jsonb_build_object('recordName', 'record ' || g, 'orgId', $1::text)
        FROM generate_series(1, 20000) g""", w.org), write=True)


async def _plans(pg_db, admin_url: str, monkeypatch: pytest.MonkeyPatch, marker: str, call) -> list[dict[str, Any]]:
    """Run ``call`` and return the plan of each statement it sent whose text carries ``marker``, as its unit of work would plan it."""
    import asyncpg
    con = await asyncpg.connect(admin_url)
    try:
        await con.execute("ANALYZE graph.nodes; ANALYZE graph.edges")       # as autovacuum would have by now
    finally:
        await con.close()
    sent: list[tuple[str, tuple]] = []
    for name in ("fetch", "fetchval", "fetchrow"):
        real = getattr(uow.Tx, name)

        async def spy(tx: uow.Tx, sql: str, *args: object, _real=real, **kw: object) -> object:
            if marker in sql:
                sent.append((sql, args))
            return await _real(tx, sql, *args, **kw)
        monkeypatch.setattr(uow.Tx, name, spy)
    await call()
    monkeypatch.undo()
    assert sent, f"no statement {marker} was sent"
    out = []
    for sql, args in sent:
        ran = await pg_db.run(lambda tx, sql=sql, args=args: tx._con.fetchval("EXPLAIN (FORMAT JSON) " + sql, *args))
        out.append(json.loads(ran) if isinstance(ran, str) else ran)
    return out


def _admin_url() -> str:
    import os
    url = os.environ.get("PG_ADMIN_URL")
    assert url, "set PG_ADMIN_URL (an owner DSN): this test analyses graph.nodes and graph.edges before it reads the plans"
    return url


def _alias_scans(plan: Any, alias: str) -> list[dict[str, Any]]:  # noqa: ANN401
    return [n for n in plan_gate.nodes(plan) if n["Node Type"] in plan_gate.SCANS and (n.get("Alias") == alias or str(n.get("Alias", "")).startswith(alias + "_")) and str(n.get("Relation Name", "")).startswith("nodes")]


def _assert_keyed(plans: list[Any], alias: str) -> None:
    for plan in plans:
        found = _alias_scans(plan, alias)
        assert found, f"no read of {alias} in the plan: {json.dumps(plan)[:2000]}"
        bad = [f"{n['Node Type']}: {plan_gate.bounds(n) or 'no index condition'}" for n in found if not plan_gate.keyed(n, KEY)]
        assert not bad, bad


async def test_the_trash_list_reads_the_user_who_deleted_each_batch_by_its_key(w: World, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _bulk(pg_db, w)
    await w.nodes("records", {"_key": "gone", "recordName": "gone", "orgId": w.org, "connectorId": "c1", "isDeleted": True, "deleteBatchId": "b1",
                              "deletedAtTimestamp": 5, "deletedByUserId": "u7"})
    out: dict = {}

    async def call() -> None:
        out.update(await w.p.list_trashed_records("c1", w.org))
    plans = await _plans(pg_db, _admin_url(), monkeypatch, "pipeshub:graph.list_trashed_records */", call)
    assert [(i["deletedByName"], i["deletedByEmail"]) for i in out["items"]] == [("user 7", "u7@example.com")] and out["total"] == 1
    _assert_keyed(plans, "u")


async def test_the_records_of_value_mentions_are_read_by_their_keys(w: World, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _bulk(pg_db, w)
    await w.nodes("valueMentions", *({"_key": f"v{i}", "orgId": w.org, "recordId": f"r{i * 7}", "kind": "money", "name": f"${i}", "amountFloat": float(i)} for i in range(1, 6)))
    binds = {"kinds": [], "dateStart": None, "dateEnd": None, "amountMin": None, "amountMax": None, "currency": "", "quantityMin": None, "quantityMax": None,
             "dimension": "", "percentMin": None, "percentMax": None}
    got: list = []

    async def call() -> None:
        got.append(await w.p._value_rows(w.org, w.org, binds, "records", None, 100))                    # every value of the org
        got.append(await w.p._value_rows(w.org, w.org, binds, "records", ["r7", "r14", "r999999"], 100))  # within some records (the vetted read)
    plans = await _plans(pg_db, _admin_url(), monkeypatch, "pipeshub:graph.get_records_for_named_entities_values", call)
    assert len(plans) == 2 and sorted(map(str, got[0])) == sorted(f"r{i * 7}" for i in range(1, 6)) and sorted(map(str, got[1])) == ["r14", "r7"]
    _assert_keyed(plans, "r")


async def test_the_dangling_value_sweep_probes_each_record_by_its_key(w: World, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _bulk(pg_db, w)
    await w.nodes("valueMentions", *({"_key": f"v{i}", "orgId": w.org, "recordId": f"r{i}" if i < 4 else f"gone{i}", "kind": "money"} for i in range(1, 6)))
    n: list = []

    async def call() -> None:
        n.append(await w.p.delete_dangling_named_entity_values(w.org, 10))
    plans = await _plans(pg_db, _admin_url(), monkeypatch, "pipeshub:graph.delete_dangling_named_entity_values */", call)
    assert n == [2]
    _assert_keyed(plans, "r")


@pytest.mark.parametrize("which", ["model", "web search"])
async def test_the_agents_of_a_model_or_provider_read_their_creators_by_their_keys(which: str, w: World, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _bulk(pg_db, w)
    await w.nodes("agentInstances", {"_key": "a1", "name": "agent", "models": ["m1"], "webSearch": "p1", "createdBy": "u9"})
    await w.edge("belongsTo", ("users", "u9"), ("organizations", w.org))
    await w.edge("permission", ("users", "u9"), ("agentInstances", "a1"), type="USER", role="OWNER")
    got: list = []

    async def call() -> None:
        got.extend(await (w.p.get_agents_by_model_key(w.org, "m1") if which == "model" else w.p.get_agents_by_web_search_provider(w.org, "p1")))
    marker = "pipeshub:graph.get_agents_by_model_key */" if which == "model" else "pipeshub:graph.get_agents_by_web_search_provider */"
    plans = await _plans(pg_db, _admin_url(), monkeypatch, marker, call)
    assert got == [{"name": "agent", "_key": "a1", "creatorName": "user 9"}]
    _assert_keyed(plans, "c")
