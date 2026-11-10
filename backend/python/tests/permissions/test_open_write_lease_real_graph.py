# ruff: noqa: F811
"""The write lease and the rewrite lease on Neo4j and ArangoDB (`KH_PERM_*`): the compare-and-set that counts a write and records its lease in one
step, concurrent opens, a killed write recovered after its lease, and a rewriter refused while another holds the connector.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.permissions.pending import parse
from app.services.permissions.rewrite_lease import REWRITE_LEASE_SECONDS, RewriteLease
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    OPEN_WRITE_GRACE_SECONDS,
    OPEN_WRITE_SECONDS,
    OPEN_WRITES_KEY,
    OPENED_KEY,
    PENDING_KEY,
    close_write,
    leaving_vrid,
    open_write,
    parse_open_writes,
    recover_expired_writes,
    unclosed_writes,
)

from .test_currency_real_graph import _world, current, finish_pass
from .test_generation_paths import _scope_fields_on, backend  # noqa: F401
from .test_open_write_lease import Clock

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


async def app_of(backend, conn: str) -> dict:
    return await backend.provider.get_document(conn, CollectionNames.APPS.value) or {}


async def test_a_write_is_counted_and_leased_in_one_step_and_closed_once(backend) -> None:
    _, conn, *_ = await _world(backend)
    await finish_pass(backend.provider, conn)
    token = await open_write(backend.provider, conn, ["r1"])
    app = await app_of(backend, conn)
    assert unclosed_writes(app) == 1 and list(parse_open_writes(app[OPEN_WRITES_KEY])) == [token] and not await current(backend.provider, conn)
    assert await close_write(backend.provider, conn, token)
    app = await app_of(backend, conn)
    assert (app[OPENED_KEY], app[CLOSED_KEY]) == (1, 1) and not app.get(OPEN_WRITES_KEY)
    assert await current(backend.provider, conn)
    assert await close_write(backend.provider, conn, token)                      # a repeated close counts nothing, and is not trusted
    app = await app_of(backend, conn)
    assert app[CLOSED_KEY] == 1 and not await current(backend.provider, conn)


async def test_concurrent_opens_and_closes_lose_no_write(backend) -> None:
    _, conn, *_ = await _world(backend)
    tokens = await asyncio.gather(*(open_write(backend.provider, conn, [f"r{i}"]) for i in range(8)))
    app = await app_of(backend, conn)
    assert app[OPENED_KEY] == 8 and set(parse_open_writes(app[OPEN_WRITES_KEY])) == set(tokens)
    await asyncio.gather(*(close_write(backend.provider, conn, t) for t in tokens[:5]))
    app = await app_of(backend, conn)
    assert unclosed_writes(app) == 3 and set(parse_open_writes(app[OPEN_WRITES_KEY])) == set(tokens[5:])


async def test_a_killed_leaving_vrid_is_recovered_after_its_lease_and_not_before(backend) -> None:
    _, conn, *_ = await _world(backend)
    await finish_pass(backend.provider, conn)
    clock = Clock()
    token = await open_write(backend.provider, conn, ["root-x"], clock=clock)       # ... the process is killed here
    assert await recover_expired_writes(backend.provider, conn, clock=clock) == 0   # a live write is left alone
    assert unclosed_writes(await app_of(backend, conn)) == 1 and not await current(backend.provider, conn)
    clock.now += OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS + 1
    assert await recover_expired_writes(backend.provider, conn, clock=clock) == 1
    app = await app_of(backend, conn)
    assert unclosed_writes(app) == 0 and set(parse(app[PENDING_KEY])) == {"root-x"} and not app.get(OPEN_WRITES_KEY)
    assert await close_write(backend.provider, conn, token) and (await app_of(backend, conn))[CLOSED_KEY] == 1      # a late closer is not counted twice


async def test_leaving_vrid_on_a_real_connector_closes_and_registers(backend) -> None:
    _, conn, *_ = await _world(backend)
    record = await backend.provider.get_nodes_by_field_in(CollectionNames.RECORDS.value, "connectorId", [conn])
    vrid = "v-lease"
    await backend.provider.update_node(record[0]["_key"], CollectionNames.RECORDS.value, {"virtualRecordId": vrid})
    async with leaving_vrid(backend.provider, "someone-else", vrid):
        assert unclosed_writes(await app_of(backend, conn)) == 1
    app = await app_of(backend, conn)
    assert unclosed_writes(app) == 0 and record[0]["_key"] in parse(app[PENDING_KEY])


async def test_a_second_rewriter_is_refused_until_the_lease_runs_out(backend) -> None:
    _, conn, *_ = await _world(backend)
    clock = Clock()
    a, b = RewriteLease(backend.provider, conn, clock=clock), RewriteLease(backend.provider, conn, clock=clock)
    assert await a.acquire() and not await b.acquire()
    assert await asyncio.gather(*(RewriteLease(backend.provider, conn, clock=clock).acquire() for _ in range(4))) == [False] * 4
    clock.now += REWRITE_LEASE_SECONDS + 1
    results = await asyncio.gather(*(RewriteLease(backend.provider, conn, clock=clock).acquire() for _ in range(4)))
    assert results.count(True) == 1                                             # of several that find it free, exactly one takes it


async def test_the_sweep_records_a_failure_with_its_backoff_and_forgets_it_when_the_connector_is_fine(backend, monkeypatch) -> None:
    from unittest.mock import MagicMock

    from app.services.vector_db.scope_sweep import (
        BACKOFF_FIRST_SECONDS,
        SWEEP_KEY,
        ScopeSweep,
        read_state,
    )

    _, conn, *_ = await _world(backend)
    clock = Clock()
    sweep = ScopeSweep(backend.provider, MagicMock(), MagicMock(), clock=clock)
    await backend.provider.update_node(conn, CollectionNames.APPS.value, {PENDING_KEY: "{nope"})           # a pending value that does not parse
    org = (await app_of(backend, conn))["orgId"]

    async def get_org_apps(*a, **kw) -> list:      # (the seeded app has no organisation edge for the real listing to follow)
        return [await app_of(backend, conn)]

    monkeypatch.setattr(backend.provider, "get_org_apps", get_org_apps)
    row = next(r for r in await sweep.run_once(org, [conn]) if r.connector_id == conn)
    state = read_state((await app_of(backend, conn))[SWEEP_KEY])
    assert row.action == "failed" and state.failures == 1 and state.retry_at == int(clock.now) + BACKOFF_FIRST_SECONDS
    assert next(r for r in await sweep.run_once(org, [conn]) if r.connector_id == conn).action == "waiting"
    await backend.provider.update_node(conn, CollectionNames.APPS.value, {PENDING_KEY: None})
    assert next(r for r in await sweep.run_once(org, [conn]) if r.connector_id == conn).action == "idle"
    assert not (await app_of(backend, conn)).get(SWEEP_KEY)


async def test_the_view_shows_a_killed_write_until_the_sweep_closes_it(backend, monkeypatch) -> None:
    from unittest.mock import MagicMock

    from app.services.vector_db.scope_sweep import ScopeSweep
    from app.services.vector_db.scope_view import org_view

    _, conn, *_ = await _world(backend)
    await finish_pass(backend.provider, conn)
    org = (await app_of(backend, conn))["orgId"]
    clock = Clock()

    async def get_org_apps(*a, **kw) -> list:
        return [await app_of(backend, conn)]

    monkeypatch.setattr(backend.provider, "get_org_apps", get_org_apps)
    await open_write(backend.provider, conn, ["root-x"], clock=clock)                  # the process dies
    assert not (await org_view(backend.provider, org, clock=clock)).needs_attention      # inside its lease
    clock.now += OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS + 1
    seen = await org_view(backend.provider, org, clock=clock)
    assert [a.split(":")[0] for a in seen.connectors[0].attention] == ["open_write_expired"]
    sweep = ScopeSweep(backend.provider, MagicMock(), MagicMock(), clock=clock)
    monkeypatch.setattr(sweep._job, "run_connector", lambda *a, **k: _busy())            # (the rewrite itself is the engine's; the view is what is under test)
    await sweep.run_once(org, [conn])
    assert [a.split(":")[0] for a in (await org_view(backend.provider, org, clock=clock)).connectors[0].attention] == ["pending_roots"]


async def _busy() -> object:
    from app.services.vector_db.scope_rewrite import RewriteResult

    return RewriteResult("o", "c", state="busy")


async def test_the_store_clock_is_close_to_this_hosts_and_a_skewed_sweeper_leaves_a_live_write_alone(backend, monkeypatch) -> None:
    import time
    from types import SimpleNamespace

    from app.services.permissions import structure_generation as sg

    _, conn, *_ = await _world(backend)
    assert abs(await backend.provider.store_time() - time.time()) < 30
    await open_write(backend.provider, conn, ["root-x"])
    monkeypatch.setattr(sg, "time", SimpleNamespace(time=lambda: time.time() + 3600))      # a sweeper host an hour ahead
    assert await recover_expired_writes(backend.provider, conn) == 0
    assert unclosed_writes(await app_of(backend, conn)) == 1
