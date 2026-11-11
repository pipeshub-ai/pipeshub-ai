# ruff: noqa: ANN401
"""The operator view shows what hides a connector's stamps and what will clear it, and stays quiet about a connector that is fine.

Each condition is held both ways: present, it is shown (nothing hidden or stuck goes unseen); absent, it is not (no false alarm that trains
the operator to ignore the view). The two conditions the review asked for have an end-to-end case: a write killed inside `leaving_vrid`, and a
pgvector group row far ahead of the database clock.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.permissions.rewrite_lease import REWRITE_LEASE_KEY
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    GENERATION_KEY,
    OPEN_WRITE_GRACE_SECONDS,
    OPEN_WRITE_SECONDS,
    OPEN_WRITES_KEY,
    OPENED_KEY,
    PENDING_KEY,
    UNSAFE_KEY,
    leaving_vrid,
    open_write,
    parse_open_writes,
    recover_expired_writes,
)
from app.services.vector_db.scope_backfill import BACKFILL_KEYS
from app.services.vector_db.scope_sweep import SWEEP_KEY
from app.services.vector_db.scope_view import connector_view, org_view

from .test_open_write_lease import Clock, Graph, a_finished_pass

DEAD = OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS + 1


def codes(app: dict, clock: Clock | None = None) -> list[str]:
    return [a.split(":")[0] for a in connector_view("c", app, clock=clock or Clock()).attention]


def current_app() -> dict:
    app: dict = {GENERATION_KEY: 3}
    a_finished_pass(app)
    return app


def test_a_connector_that_is_fine_shows_nothing() -> None:
    v = connector_view("c", current_app(), clock=Clock())
    assert v.current and not v.attention and not v.info


def test_pending_roots_are_shown_with_how_long_ago_the_oldest_was_registered() -> None:
    app = {**current_app(), GENERATION_KEY: 9, PENDING_KEY: '{"r1":4,"r2":8}'}
    v = connector_view("c", app, clock=Clock())
    assert not v.current and v.attention == ["pending_roots: 2 root(s) hidden until rewritten, the oldest registered 5 change(s) ago"]


def test_a_pending_list_that_does_not_parse_is_shown() -> None:
    assert codes({**current_app(), PENDING_KEY: "{nope"}) == ["pending_unreadable"]


async def test_a_write_in_flight_is_information_and_a_dead_one_is_attention() -> None:
    clock, g = Clock(), Graph()
    g.app.update(current_app())
    await open_write(g, "c", ["r1"], clock=clock)
    v = connector_view("c", g.app, clock=clock)
    assert not v.attention and any("in flight" in i for i in v.info) and not v.current
    clock.now += OPEN_WRITE_SECONDS + 1                 # past its deadline, inside the grace: not dead yet
    assert not connector_view("c", g.app, clock=clock).attention
    clock.now += OPEN_WRITE_GRACE_SECONDS
    assert codes(g.app, clock) == ["open_write_expired"]


async def test_a_write_killed_inside_leaving_vrid_is_shown_until_the_sweep_closes_it_and_then_not() -> None:
    clock, g = Clock(), Graph()
    g.app.update(current_app())
    g.sharers = [{"_key": "r-c", "connectorId": "c"}]
    cm = leaving_vrid(g, "me", "v")
    await cm.__aenter__()                               # ... and the process dies
    assert codes(g.app, clock) == []                    # inside its lease: waiting, not alarming
    clock.now = next(iter(parse_open_writes(g.app[OPEN_WRITES_KEY]).values()))["u"] + OPEN_WRITE_GRACE_SECONDS + 1      # (the write took its deadline from the wall clock)
    assert codes(g.app, clock) == ["open_write_expired"]
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert codes(g.app, clock) == ["pending_roots"]     # recovered: the roots are what is left, and the sweep rewrites them
    await cm.gen.aclose()


def test_writes_without_a_lease_need_the_operator() -> None:
    assert codes({**current_app(), OPENED_KEY: 2, CLOSED_KEY: 1}) == ["open_write_unleased"]            # opened by code that left no lease
    assert codes({**current_app(), OPENED_KEY: 1, CLOSED_KEY: 1}) == []
    assert codes({**current_app(), OPENED_KEY: 1, CLOSED_KEY: 2}) == ["writes_unreadable"]
    assert codes({**current_app(), OPENED_KEY: 1, OPEN_WRITES_KEY: "{nope"}) == ["open_writes_unreadable", "open_write_unleased"]


def test_a_connector_that_is_not_current_for_no_visible_reason_needs_a_full_pass() -> None:
    assert codes({**current_app(), GENERATION_KEY: 4}) == ["needs_full_pass"]                         # moved after the pass, nothing registered
    never = {GENERATION_KEY: 4}
    assert codes(never) == []                                                                          # never back-filled: nothing to keep current


def test_stale_points_and_a_failing_sweep_are_shown() -> None:
    app = {**current_app(), BACKFILL_KEYS["stale"]: 2, SWEEP_KEY: '{"n":3,"e":"Boom: x","t":1000100}'}
    v = connector_view("c", app, clock=Clock())
    assert [a.split(":")[0] for a in v.attention] == ["stale_points", "sweep_failing"] and "Boom: x" in v.attention[1] and "next try in 100 s" in v.attention[1]


def test_a_retry_time_and_a_rewriter_lease_are_read_by_the_host_clock_not_the_store_clock() -> None:
    app = {**current_app(), SWEEP_KEY: '{"n":3,"e":"Boom: x","t":1000100}', REWRITE_LEASE_KEY: '{"h":"w","u":1000300}'}
    v = connector_view("c", app, clock=Clock(), store_time=1_000_000.0 + 5000)      # the store's clock is far ahead of this host's
    assert "next try in 100 s" in v.attention[0] and v.info == ["a rewriter holds the connector until 300 s from now"]


class Apps:
    def __init__(self, *docs: dict) -> None:
        self.docs = docs

    async def get_org_apps(self, org: str, **kw: Any) -> list[dict]:
        return list(self.docs)


class Vdb:
    def __init__(self, answer: Any) -> None:
        self.answer, self.asked = answer, []

    async def group_clock_skew(self, org: str, *, min_ahead_seconds: float) -> tuple[int, float]:
        self.asked.append((org, min_ahead_seconds))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def test_a_pgvector_group_row_far_ahead_of_the_clock_is_shown_even_when_no_root_is_pending_yet() -> None:
    apps, vdb = Apps({"_key": "c", **current_app()}), Vdb((3, 7200.4))
    view = await org_view(apps, "o", vdb, clock=Clock())
    assert view.needs_attention and view.group_rows_ahead == (3, 7200.4) and vdb.asked == [("o", 30.0)]
    assert "3 pgvector group row(s)" in view.lines()[-1] and "7200 s" in view.lines()[-1]


async def test_no_group_row_ahead_is_not_an_alarm() -> None:
    view = await org_view(Apps({"_key": "c", **current_app()}), "o", Vdb((0, 0.0)), clock=Clock())
    assert not view.needs_attention and view.group_rows_ahead is None


async def test_a_store_that_cannot_answer_is_a_note_and_a_store_without_the_question_is_not() -> None:
    apps = Apps({"_key": "c", **current_app()})
    view = await org_view(apps, "o", Vdb(RuntimeError("db down")), clock=Clock())
    assert view.needs_attention and "group_clock_unknown" in view.notes[0]
    assert not (await org_view(apps, "o", object(), clock=Clock())).needs_attention       # (a store with no group rows has nothing to ask)
    assert not (await org_view(apps, "o", None, clock=Clock())).needs_attention


async def test_only_connectors_with_scope_state_are_listed_and_they_can_be_chosen() -> None:
    apps = Apps({"_key": "a", **current_app()}, {"_key": "b", UNSAFE_KEY: 5}, {"_key": "plain", "name": "x"})
    assert [c.connector_id for c in (await org_view(apps, "o", clock=Clock())).connectors] == ["a", "b"]
    assert [c.connector_id for c in (await org_view(apps, "o", connectors=["b"], clock=Clock())).connectors] == ["b"]


async def test_the_group_clock_question_reaches_the_pgvector_statement_with_the_org_and_the_threshold() -> None:
    from app.services.vector_db.pgvector.errors import OrgRequired
    from app.services.vector_db.pgvector.pgvector import PgVectorService

    seen: list[tuple] = []

    class Tx:
        async def fetchrow(self, sql: str, *args: Any) -> dict:
            seen.append((sql, args))
            return {"n": 2, "ahead": 90.5}

    class Db:
        async def run(self, fn: Any, **opts: Any) -> Any:
            return await fn(Tx())

    svc = PgVectorService.__new__(PgVectorService)
    svc._db, svc._catalog = Db(), object()           # type: ignore[assignment]
    assert await svc.group_clock_skew("a" * 24, min_ahead_seconds=12.0) == (2, 90.5)
    sql, args = seen[0]
    assert args == ("a" * 24, 12.0) and "org_id = $1" in sql and "clock_timestamp()" in sql
    with pytest.raises(OrgRequired):
        await svc.group_clock_skew("")                # no org: refused, never every org's rows


async def test_an_unclosed_write_with_no_lease_alone_is_listed_and_needs_attention() -> None:
    """A connector whose only scope state is a counter that was opened and never closed must not read as "no connector has scope state"."""
    view = await org_view(Apps({"_key": "a", OPENED_KEY: 1}), "o", clock=Clock())
    assert [c.connector_id for c in view.connectors] == ["a"] and view.needs_attention
    assert [a.split(":")[0] for a in view.connectors[0].attention] == ["open_write_unleased"]


def test_a_lease_written_without_the_store_clock_is_not_called_dead_on_its_own_deadline() -> None:
    clock = Clock()
    app = {**current_app(), OPENED_KEY: 1, OPEN_WRITES_KEY: f'{{"t":{{"u":{int(clock.now) - 86400},"k":1}}}}'}
    v = connector_view("c", app, clock=clock)
    assert not v.attention and any("in flight" in i for i in v.info)


def test_a_lease_inside_its_grace_never_prints_a_negative_time() -> None:
    clock = Clock()
    app = {**current_app(), OPENED_KEY: 1, OPEN_WRITES_KEY: f'{{"t":{{"u":{int(clock.now) - 10}}}}}'}
    v = connector_view("c", app, clock=clock)
    assert not v.attention and v.info == ["1 write(s) in flight, the next lease has run out and is inside its grace"]


class UnreadableVdb(Vdb):
    def __init__(self, n: int | Exception) -> None:
        super().__init__((0, 0.0))
        self.n = n

    async def unreadable_group_rows(self, org: str) -> int:
        if isinstance(self.n, Exception):
            raise self.n
        return self.n


async def test_group_rows_with_a_trusted_stamp_and_a_null_list_are_shown_and_counted() -> None:
    from app.services.permissions.metrics import SERVING_UNREADABLE_ROWS

    apps = Apps({"_key": "c", **current_app()})
    view = await org_view(apps, "o", UnreadableVdb(4), clock=Clock())
    assert view.needs_attention and "unreadable_group_rows: 4" in view.lines()[-1]
    assert SERVING_UNREADABLE_ROWS._metric.labels("pgvector", "o")._value.get() == 4
    assert not (await org_view(apps, "o", UnreadableVdb(0), clock=Clock())).needs_attention
    assert "unreadable_group_rows_unknown" in (await org_view(apps, "o", UnreadableVdb(RuntimeError("x")), clock=Clock())).notes[0]
