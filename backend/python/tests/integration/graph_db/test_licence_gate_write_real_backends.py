"""The write side of the licence gate (design B), checked by what users can read, on a real Neo4j and ArangoDB.

Every check here asks ``check_access``, the one decision point of every read path, so a step that would let someone
in shows up as a read and not as a stored field. The sync runs through the production start path
(``EventService._start_sync_with_lease`` and ``run_sync_task``) with the real processor; only the connector is
scripted. The state table these tests cover is in the Option B plan, ``gate-write-state-tests.md``.

alice created the connector (``createdBy``); bob is linked to it and, at the source, sometimes reads its content.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus, CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.registry.connector_registry import ConnectorRegistry
from app.connectors.core.sync import licence_lift
from app.models.entities import AppUserGroup
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.graph_db.test_full_sync_unlink_real_backends import (
    _app_doc,
    _channel_shared_with_department,
    _cleanup,
    _department,
    _Sync,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("licence-gate-write-it")

RECORDS = CollectionNames.RECORDS.value
RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value
DAY_MS = 86_400_000
NAMES = ("m1", "m2", "m3")


async def _seed(graph: object, org_id: str) -> _Sync:
    """As the full-sync suite seeds its world, under ``org_id`` (PostgreSQL holds an org id to 24 hex digits)."""
    run = uuid.uuid4().hex[:10]
    now = get_epoch_timestamp_in_ms()
    connector_id = f"slack-{run}"
    stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    assert await graph.batch_upsert_nodes(
        [{"id": connector_id, "orgId": org_id, "name": "Slack", "type": Connectors.SLACK_WORKSPACE.value,
          "appGroup": "Slack", "scope": "team", "isActive": True, "createdBy": f"alice-{run}", **stamps}],
        collection=CollectionNames.APPS.value,
    )
    alice, bob = f"alice-{run}", f"bob-{run}"
    assert await graph.batch_upsert_nodes(
        [{"id": key, "userId": key, "orgId": org_id, "email": f"{key}@example.com", "isActive": True, **stamps}
         for key in (alice, bob)],
        collection=CollectionNames.USERS.value,
    )
    # The connector gate: both are linked, so only the limit keeps bob out.
    assert await graph.batch_create_edges(
        [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": connector_id,
          "to_collection": CollectionNames.APPS.value, "syncState": "NOT_STARTED", "lastSyncUpdate": now, **stamps}
         for key in (alice, bob)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
    processor.org_id = org_id
    producer = MagicMock()
    producer.send_messages = AsyncMock(side_effect=lambda topic, messages: [True] * len(messages))
    processor.messaging_producer = producer
    return _Sync(graph, processor, org_id, connector_id, alice, bob, now)


@contextlib.asynccontextmanager
async def _postgres(pool_max: int | None = None) -> AsyncIterator[tuple[object, str]]:
    """The PostgreSQL graph backend on a migrated database (PG_CORE_URL), with this test's org bound, as the
    service binds it; each run writes under its own org and removes it afterwards."""
    if not os.environ.get("PG_CORE_URL"):
        pytest.skip("PG_CORE_URL is not set")
    from app.core.db import DbConfig, KernelDB
    from app.core.org_context import OrgContext, bind
    from app.services.graph_db.postgres.provider import PostgresGraphProvider

    config = DbConfig.from_env("connectors")
    db = KernelDB(config if pool_max is None else dataclasses.replace(config, pool_max=pool_max, pool_min=1))
    await db.start()
    org = uuid.uuid4().hex[:24]
    try:
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            yield PostgresGraphProvider(logger, db), org
            await db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
            await db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)
    finally:
        await db.close()


@pytest.fixture(params=["neo4j", "arango", "postgres"])
async def sync(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Sync]:
    if request.param == "postgres":
        async with _postgres() as (graph, org):
            seeded = await _seed(graph, org)
            try:
                yield seeded
            finally:
                await _cleanup(seeded)
        return
    try:
        graph = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "kh_perm_write")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    seeded = await _seed(graph, f"org-{uuid.uuid4().hex[:10]}")
    try:
        yield seeded
    finally:
        await _cleanup(seeded)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            await disconnect()


# --- helpers ------------------------------------------------------------------------------------------------------

def _licence(proc: DataSourceEntitiesProcessor, value: object) -> None:
    """The licence the Node backend publishes. A new config service each time, so the answer cached for the old one
    (``permission_sync_licensed``) is not reused: the change is seen at once, as it is once the cache has aged."""
    proc._reads_licence = True
    proc.config_service = MagicMock()
    if isinstance(value, BaseException):
        proc.config_service.get_config = AsyncMock(side_effect=value)
    else:
        proc.config_service.get_config = AsyncMock(return_value=value)


LICENSED = {"allowed": True}
UNLICENSED = {"allowed": False}


def _restart(sync: _Sync, licence: object) -> None:
    """A new process: a fresh processor (nothing remembered in memory) on the same store."""
    proc = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, sync.graph), MagicMock())
    proc.org_id = sync.org_id
    proc.messaging_producer = sync.processor.messaging_producer
    sync.processor = proc
    _licence(proc, licence)


async def _readable(sync: _Sync, user: str, names: tuple[str, ...] = NAMES) -> set[str]:
    return {n for n in names if await sync.can_read(user, n)}


async def _run(sync: _Sync, script) -> None:
    """Run a full sync to its end; a run that fails (as some here must) is not an error of the test."""
    task, _lease = await sync.full_sync(script)
    done, _ = await asyncio.wait({task}, timeout=120)
    assert done, "the sync did not end"


async def _until(read, want: object, timeout: float = 30) -> None:
    """Wait for ``await read()`` to answer ``want``: for what the detached finalizer writes after the run's task."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while (got := await read()) != want:
        assert loop.time() < end, f"still {got!r}"
        await asyncio.sleep(0.2)


async def _limit(sync: _Sync) -> object:
    return (await _app_doc(sync)).get("accessLimit")


def _source(readers_of: dict[str, tuple[str, ...]], *, inherit: bool = False):  # noqa: ANN202
    """A source holding the channel (read by nobody directly) and these messages with these readers."""
    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=())])
        await s.processor.on_new_records([s.message(n, readers=r, inherit=inherit) for n, r in readers_of.items()])
    return script


async def _synced_before_the_upgrade(sync: _Sync) -> None:
    """The state a licensed install left: three messages, alice and bob reading each, and no limit."""
    await sync.processor.on_new_record_groups([sync.channel(readers=())])
    await sync.processor.on_new_records(
        [sync.message(n, readers=(sync.alice, sync.bob), inherit=False) for n in NAMES]
    )
    assert await _readable(sync, sync.bob) == set(NAMES)
    assert await _limit(sync) is None


# --- 1. a connector synced before the upgrade --------------------------------------------------------------------

async def test_a_connector_synced_before_the_upgrade_is_limited_before_its_first_unlicensed_write(sync: _Sync) -> None:
    """Ordering: the limit is stored before the script writes anything, so no instant of the run reads wider than it.
    Checked before the first write, after each batch, and after the finalizer; the edges stored before the upgrade
    are still there and admit nobody but the creator."""
    await _synced_before_the_upgrade(sync)
    seen: list[tuple[str, object, set[str], set[str]]] = []

    async def script(s: _Sync) -> None:
        seen.append(("before any write", await _limit(s), await _readable(s, s.bob), await _readable(s, s.alice)))
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice, s.bob))])
        await s.processor.on_new_records([s.message("m4", readers=(s.alice, s.bob), inherit=False)])
        seen.append(("after a new record", await _limit(s), await _readable(s, s.bob, (*NAMES, "m4")),
                     await _readable(s, s.alice, (*NAMES, "m4"))))
        await s.processor.on_new_records([s.message(n, readers=(s.alice, s.bob), inherit=False) for n in NAMES])
        seen.append(("after an update", await _limit(s), await _readable(s, s.bob, (*NAMES, "m4")),
                     await _readable(s, s.alice, (*NAMES, "m4"))))

    _licence(sync.processor, UNLICENSED)
    await _run(sync, script)

    everything = {*NAMES, "m4"}
    assert seen == [
        ("before any write", "CREATOR", set(), set(NAMES)),
        ("after a new record", "CREATOR", set(), everything),
        ("after an update", "CREATOR", set(), everything),
    ]
    assert await _readable(sync, sync.bob, (*NAMES, "m4")) == set()
    assert await _readable(sync, sync.alice, (*NAMES, "m4")) == everything
    assert (await sync.links(RECORDS, await sync.record_id("m1")))["grants"] == 2, "bob's stored grant is kept"
    assert (await sync.links(RECORDS, await sync.record_id("m4")))["grants"] == 0, "no grant was copied"


# --- 2. a lift that fails, and a restart ------------------------------------------------------------------------

async def test_a_failed_lift_and_a_restart_keep_the_connector_limited_until_a_licensed_full_sync_lifts_it(
    sync: _Sync,
) -> None:
    await _synced_before_the_upgrade(sync)
    _licence(sync.processor, UNLICENSED)
    await _run(sync, _source({n: (sync.alice,) for n in NAMES}))
    assert await _limit(sync) == "CREATOR"

    # Licensed: the source lets bob read m1 only. The lift's write fails.
    source = _source({"m1": (sync.alice, sync.bob), "m2": (sync.alice,), "m3": (sync.alice,)})
    _licence(sync.processor, LICENSED)
    with patch.object(DataSourceEntitiesProcessor, "clear_access_limit", AsyncMock(side_effect=ConnectionError("x"))):
        await _run(sync, source)
    assert await _limit(sync) == "CREATOR", "a failed lift keeps the limit"
    assert await _readable(sync, sync.bob) == set(), "and keeps it closed, though bob's grant on m1 is written"

    # A restart, licensed: the start of an incremental sync owes a full sync and lifts nothing.
    _restart(sync, LICENSED)
    assert await sync.processor.apply_licence_gate(sync.connector_id, full_sync=False) is False
    app = await _app_doc(sync)
    assert app.get("accessLimit") == "CREATOR" and app.get(ConnectorStateKeys.PENDING_FULL_SYNC) is True
    assert await _readable(sync, sync.bob) == set()

    await _run(sync, source)
    assert await _limit(sync) is None
    assert await _readable(sync, sync.bob) == {"m1"}, "after the lift, exactly what the source lets bob read"
    assert await _readable(sync, sync.alice) == set(NAMES)


# --- 3. nothing skipped while unlicensed becomes live after the lift ---------------------------------------------

async def test_no_grant_or_membership_kept_while_unlicensed_is_live_after_the_lift(sync: _Sync) -> None:
    """Licensed: bob reads m1 through a department shared the channel, and m2 by his own grant. Unlicensed, the
    source drops bob everywhere; every one of those writes is skipped and the department is kept as seen
    (``keep_skipped_groups``). Licensed again, the source has no department at all: after the lift bob reads
    nothing, so neither a stale grant, a stale membership nor the kept group was carried into the licensed run."""
    department, members = await _department(sync)
    await sync.processor.on_new_record_groups([_channel_shared_with_department(sync)])
    await sync.processor.on_new_records([
        sync.message("m1", readers=(), inherit=True),
        sync.message("m2", readers=(sync.alice, sync.bob), inherit=False),
    ])
    assert await _readable(sync, sync.bob, ("m1", "m2")) == {"m1", "m2"}

    async def unlicensed_source(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)
        await s.processor.on_new_record_groups([_channel_shared_with_department(s)])
        await s.processor.on_new_records([
            s.message("m1", readers=(), inherit=True),
            s.message("m2", readers=(s.alice,), inherit=False),
            s.message("m3", readers=(s.alice, s.bob), inherit=False),
        ])

    _licence(sync.processor, UNLICENSED)
    await _run(sync, unlicensed_source)
    assert await members() == {sync.alice, sync.bob}, "kept as stored while unlicensed"
    assert await _readable(sync, sync.bob) == set()

    _licence(sync.processor, LICENSED)
    await _run(sync, _source({"m1": (sync.alice,), "m2": (sync.alice,), "m3": (sync.alice,)}))

    assert await _limit(sync) is None
    assert await _readable(sync, sync.bob) == set(), "no grant from before or during the limit let bob in"
    assert await _readable(sync, sync.alice) == set(NAMES)
    assert not await members() - {sync.alice}, "bob is in no group of the connector any more"


# --- 4. the licence changing in the middle of a run ---------------------------------------------------------------

async def test_a_licence_that_lapses_mid_sync_leaves_nobody_reading_what_the_source_took_away(sync: _Sync) -> None:
    """The run starts licensed with no limit. Partway the licence lapses (the cached answer ages out), and the source
    then takes bob off m1. That write is skipped, so either the connector is limited or bob's grant must go:
    otherwise bob keeps reading what the source no longer lets him read, on a connector nobody limited."""
    await _synced_before_the_upgrade(sync)

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=())])
        _licence(s.processor, UNLICENSED)
        await s.processor.on_new_records([s.message(n, readers=(s.alice,), inherit=False) for n in NAMES])

    _licence(sync.processor, LICENSED)
    await _run(sync, script)

    assert await _readable(sync, sync.bob) == set(), f"limit={await _limit(sync)!r}"
    assert await _readable(sync, sync.alice) == set(NAMES)


async def test_a_run_that_fails_after_its_licence_lapsed_still_leaves_the_connector_limited(sync: _Sync) -> None:
    await _synced_before_the_upgrade(sync)

    async def script(s: _Sync) -> None:
        _licence(s.processor, UNLICENSED)
        await s.processor.on_new_records([s.message("m1", readers=(s.alice,), inherit=False)])
        raise ConnectionError("the source went away partway")

    _licence(sync.processor, LICENSED)
    await _run(sync, script)
    assert await _limit(sync) == "CREATOR"
    assert await _readable(sync, sync.bob) == set()


async def test_a_run_killed_right_after_its_first_skipped_write_leaves_the_connector_limited(sync: _Sync) -> None:
    """No end of run at all (a crash or a kill), so the end-of-run backstop never runs: the first skipped write
    itself must have set the limit. Restarted licensed, the connector stays limited until a licensed full sync."""
    await _synced_before_the_upgrade(sync)
    _licence(sync.processor, LICENSED)
    assert await sync.processor.apply_licence_gate(sync.connector_id, full_sync=True) is False, "no limit to lift"
    _licence(sync.processor, UNLICENSED)
    await sync.processor.on_new_records([sync.message("m1", readers=(sync.alice,), inherit=False)])
    # The process dies here: no finalizer, no end of run.

    _restart(sync, LICENSED)
    assert await _limit(sync) == "CREATOR"
    assert await _readable(sync, sync.bob) == set(), "nobody outside the limit reads anything"
    assert await _readable(sync, sync.alice) == set(NAMES)
    assert await sync.processor.apply_licence_gate(sync.connector_id, full_sync=False) is False
    assert await _limit(sync) == "CREATOR", "an incremental sync does not lift it"

    await _run(sync, _source({"m1": (sync.alice,), "m2": (sync.alice, sync.bob), "m3": (sync.alice,)}))
    assert await _limit(sync) is None
    assert await _readable(sync, sync.bob) == {"m2"}


async def test_a_limit_that_cannot_be_set_at_the_first_skipped_write_fails_the_run_before_more_content(
    sync: _Sync,
) -> None:
    await _synced_before_the_upgrade(sync)

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=())])
        _licence(s.processor, UNLICENSED)
        await s.processor.on_new_records([s.message("m4", readers=(s.alice,), inherit=False)])

    real_set = DataSourceEntitiesProcessor.set_access_limit
    calls: list[str] = []

    async def first_write_refused(self: DataSourceEntitiesProcessor, connector_id: str, limit: object) -> None:
        calls.append(connector_id)
        if len(calls) == 1:
            raise ConnectionError("the limit write was refused")
        await real_set(self, connector_id, limit)

    _licence(sync.processor, LICENSED)
    with patch.object(DataSourceEntitiesProcessor, "set_access_limit", first_write_refused):
        await _run(sync, script)
        # The end of the run (its detached finalizer) sets the limit the first skip owed.
        await _until(lambda: _limit(sync), "CREATOR")
    assert await sync.graph.get_record_by_external_id(sync.connector_id, f"m4-{sync.connector_id}") is None, (
        "no record is written under access that could not be limited"
    )
    assert len(calls) == 2
    assert await _readable(sync, sync.bob) == set()


async def test_a_licence_returning_mid_sync_writes_access_again_but_does_not_lift(sync: _Sync) -> None:
    await _synced_before_the_upgrade(sync)

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=())])
        _licence(s.processor, LICENSED)
        await s.processor.on_new_records([s.message(n, readers=(s.alice, s.bob), inherit=False) for n in NAMES])

    _licence(sync.processor, UNLICENSED)
    await _run(sync, script)
    assert await _limit(sync) == "CREATOR", "only a full sync that started licensed lifts"
    assert await _readable(sync, sync.bob) == set()

    await _run(sync, _source({"m1": (sync.alice,), "m2": (sync.alice, sync.bob), "m3": (sync.alice,)}))
    assert await _limit(sync) is None
    assert await _readable(sync, sync.bob) == {"m2"}


async def test_a_licence_read_that_fails_mid_sync_neither_lifts_nor_opens(sync: _Sync) -> None:
    await _synced_before_the_upgrade(sync)
    _licence(sync.processor, UNLICENSED)
    await _run(sync, _source({n: (sync.alice,) for n in NAMES}))

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=())])
        await s.processor.on_new_records([s.message("m1", readers=(s.alice, s.bob), inherit=False)])
        _licence(s.processor, ConnectionError("config store down"))
        await s.processor.on_new_records([s.message("m2", readers=(s.alice, s.bob), inherit=False)])

    _licence(sync.processor, LICENSED)
    await _run(sync, script)
    assert await _limit(sync) == "CREATOR"
    assert await _readable(sync, sync.bob) == set(), "bob's grant on m1 was written, and the limit still holds"


async def test_a_licence_read_that_fails_at_start_writes_nothing_and_opens_nothing(sync: _Sync) -> None:
    await _synced_before_the_upgrade(sync)
    ran = asyncio.Event()

    async def script(s: _Sync) -> None:
        ran.set()

    _licence(sync.processor, ConnectionError("config store down"))
    await _run(sync, script)
    assert not ran.is_set(), "the source is not read"
    assert await _limit(sync) is None
    assert await _readable(sync, sync.bob) == set(NAMES), "unchanged"


# --- 5. a lapse after a lift, and coming back ---------------------------------------------------------------------

async def test_a_lapse_after_a_lift_limits_again_at_the_next_sync_start_and_a_returning_key_lifts_again(
    sync: _Sync,
) -> None:
    await _synced_before_the_upgrade(sync)
    _licence(sync.processor, UNLICENSED)
    await _run(sync, _source({n: (sync.alice, sync.bob) for n in NAMES}))
    _licence(sync.processor, LICENSED)
    await _run(sync, _source({n: (sync.alice, sync.bob) for n in NAMES}))
    assert await _limit(sync) is None and await _readable(sync, sync.bob) == set(NAMES)

    seen: list[set[str]] = []

    async def script(s: _Sync) -> None:
        seen.append(await _readable(s, s.bob))
        await _source({n: (s.alice, s.bob) for n in NAMES})(s)

    _licence(sync.processor, UNLICENSED)
    await _run(sync, script)
    assert seen == [set()], "limited again before the source is read"
    assert await _limit(sync) == "CREATOR"
    assert await _readable(sync, sync.bob) == set()

    _licence(sync.processor, LICENSED)
    await _run(sync, _source({"m1": (sync.alice,), "m2": (sync.alice, sync.bob), "m3": (sync.alice,)}))
    assert await _limit(sync) is None and await _readable(sync, sync.bob) == {"m2"}


# --- 6. what is never limited -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("type_", "scope"), [
    (Connectors.WEB.value, "team"),
    (Connectors.S3.value, "team"),
    (Connectors.POSTGRESQL.value, "team"),
    (Connectors.SLACK_WORKSPACE.value, "personal"),
])
async def test_open_types_and_personal_connectors_are_never_limited_and_copy_access_as_before(
    sync: _Sync, type_: str, scope: str,
) -> None:
    await sync.graph.update_node(sync.connector_id, CollectionNames.APPS.value, {"type": type_, "scope": scope})
    _licence(sync.processor, UNLICENSED)
    await _run(sync, _source({"m1": (sync.alice, sync.bob), "m2": (sync.alice,), "m3": (sync.alice,)}))
    assert await _limit(sync) is None
    assert await _readable(sync, sync.bob) == {"m1"}, "the source's access, copied"


async def test_a_type_missing_from_the_open_list_is_limited(sync: _Sync) -> None:
    """Fail closed: a type nobody listed (a connector added later) is gated."""
    await sync.graph.update_node(sync.connector_id, CollectionNames.APPS.value, {"type": "SOME_NEW_SOURCE"})
    await _synced_before_the_upgrade(sync)
    _licence(sync.processor, UNLICENSED)
    await _run(sync, _source({n: (sync.alice, sync.bob) for n in NAMES}))
    assert await _limit(sync) == "CREATOR"
    assert await _readable(sync, sync.bob) == set()


# --- 7. a stamp refresh that fails after the limit committed (design B, "trap for step 4") -----------------------

async def test_a_refresh_that_fails_after_the_limit_committed_leaves_it_closed_and_the_next_start_refreshes(
    sync: _Sync,
) -> None:
    await _synced_before_the_upgrade(sync)
    real_mark = sync.graph.kh_scope_mark_changed
    marks: list[str] = []

    async def failing_mark(connector_id: str) -> None:
        marks.append(connector_id)
        raise ConnectionError("stamp store down")

    ran = asyncio.Event()

    async def script(s: _Sync) -> None:
        ran.set()
        await _source({n: (s.alice, s.bob) for n in NAMES})(s)

    _licence(sync.processor, UNLICENSED)
    with patch.object(sync.graph, "kh_scope_mark_changed", failing_mark):
        await _run(sync, script)
    assert marks == [sync.connector_id] and not ran.is_set(), "the run did not start"
    assert await _limit(sync) == "CREATOR", "the write had committed"
    assert await _readable(sync, sync.bob) == set(), "closed from the commit on"

    calls: list[str] = []

    async def counting_mark(connector_id: str) -> None:
        calls.append(connector_id)
        await real_mark(connector_id)

    with patch.object(sync.graph, "kh_scope_mark_changed", counting_mark):
        await _run(sync, script)
    assert calls == [sync.connector_id] and ran.is_set(), "the next start runs the refresh again, then the sync"
    assert await _readable(sync, sync.bob) == set()


# --- 8. the startup sweep -----------------------------------------------------------------------------------------

async def test_the_startup_sweep_limits_a_connector_that_never_syncs_again(sync: _Sync) -> None:
    await _synced_before_the_upgrade(sync)
    container = MagicMock()
    container.logger.return_value = logger
    container.config_service.return_value = MagicMock(get_config=AsyncMock(return_value=UNLICENSED))
    container.data_store = AsyncMock(return_value=GraphDataStore(logger, sync.graph))
    registry = ConnectorRegistry(container)

    with patch("app.connectors.core.base.data_processor.data_source_entities_processor."
               "notify_connector_permissions_changed", AsyncMock()):
        await registry._limit_unlicensed_connectors(sync.org_id, [await _app_doc(sync)])

    app = await _app_doc(sync)
    assert app.get("accessLimit") == "CREATOR" and app.get("permissionModel") == "APP_LEVEL"
    assert await _readable(sync, sync.bob) == set()
    assert await _readable(sync, sync.alice) == set(NAMES)


# --- 9. the watcher reads the limit off each backend --------------------------------------------------------------

async def _only_this_org(sync: _Sync):  # noqa: ANN202
    """The watcher walks every org; the graph stores are shared with other suites, so only this test's org is
    visited. It is given what ``get_org_apps`` reads: an org node and its edge to the app."""
    await sync.graph.batch_upsert_nodes([{"id": sync.org_id, "isActive": True, "accountType": "enterprise",
                                          "createdAtTimestamp": sync.now, "updatedAtTimestamp": sync.now}],
                                        collection=CollectionNames.ORGS.value)
    await sync.graph.batch_create_edges([{
        "from_id": sync.org_id, "from_collection": CollectionNames.ORGS.value, "to_id": sync.connector_id,
        "to_collection": CollectionNames.APPS.value, "createdAtTimestamp": sync.now,
    }], collection=CollectionNames.ORG_APP_RELATION.value)

    async def visit(_graph, _job, work, _logger, **_kw) -> list[str]:
        await work(sync.org_id)
        return []
    return patch.object(licence_lift, "for_each_org", visit)


async def test_the_licence_watcher_queues_a_limited_idle_connector_and_drains_it_until_it_runs(sync: _Sync) -> None:
    await _synced_before_the_upgrade(sync)
    _licence(sync.processor, UNLICENSED)
    await _run(sync, _source({n: (sync.alice,) for n in NAMES}))
    assert await _limit(sync) == "CREATOR"
    await sync.graph.update_node(sync.connector_id, CollectionNames.APPS.value, {
        "status": AppStatus.IDLE.value, ConnectorStateKeys.IS_ACTIVE: True,
        ConnectorStateKeys.IS_AUTHENTICATED: True, ConnectorStateKeys.PENDING_FULL_SYNC: False,
    })
    drain = AsyncMock(return_value=[])
    try:
        with await _only_this_org(sync), patch.object(licence_lift, "drain_queued_syncs", drain):
            tries: dict[str, int] = {}
            done = await licence_lift.queue_limited_full_syncs(sync.graph, logger, tries)
            app = await _app_doc(sync)
            assert done is False, "watched until it is lifted"
            assert app.get("status") == AppStatus.QUEUED.value
            assert app.get(ConnectorStateKeys.PENDING_FULL_SYNC) is True
            assert app.get(ConnectorStateKeys.PENDING_RESYNC) is True
            assert drain.await_count == 1 and tries == {sync.connector_id: 1}

            # Still queued at the next check (the drain did not publish it): drained again, not queued again.
            before = app.get("queuedAtTimestamp")
            assert await licence_lift.queue_limited_full_syncs(sync.graph, logger, tries) is False
            assert drain.await_count == 2 and tries == {sync.connector_id: 1}
            assert (await _app_doc(sync)).get("queuedAtTimestamp") == before
            assert await _readable(sync, sync.bob) == set(), "queueing opens nothing"

            # The queued full sync runs licensed and lifts (ArangoDB stores null, the others remove the field).
            _licence(sync.processor, LICENSED)
            await sync.graph.update_node(sync.connector_id, CollectionNames.APPS.value, {"status": AppStatus.IDLE.value})
            await _run(sync, _source({"m1": (sync.alice, sync.bob), "m2": (sync.alice,), "m3": (sync.alice,)}))
            assert await _limit(sync) is None
            drain.reset_mock()
            assert await licence_lift.queue_limited_full_syncs(sync.graph, logger, tries) is True, "nothing to watch"
            drain.assert_not_awaited()
            assert await _readable(sync, sync.bob) == {"m1"}
    finally:
        await sync.graph.delete_nodes_and_edges([sync.org_id], CollectionNames.ORGS.value)


# --- 10. a webhook or an event outside a run -----------------------------------------------------------------------

async def test_a_grant_written_outside_a_run_on_a_limited_connector_admits_nobody(sync: _Sync) -> None:
    """Event and webhook writes that do not go through a run (``upsert_permission_edge``,
    ``add_user_to_group``-style calls) are skipped while unlicensed, and the limit holds either way."""
    await _synced_before_the_upgrade(sync)
    _licence(sync.processor, UNLICENSED)
    await _run(sync, _source({n: (sync.alice,) for n in NAMES}))
    from app.models.permission import EntityType, Permission, PermissionType

    old = await sync.processor.upsert_permission_edge(
        sync.bob, CollectionNames.USERS.value, await sync.record_id("m2"), RECORDS,
        Permission(email=f"{sync.bob}@example.com", entity_type=EntityType.USER, type=PermissionType.OWNER),
        connector_id=sync.connector_id,
    )
    assert old is None
    assert await _readable(sync, sync.bob) == set()
    group = AppUserGroup(id=f"g-{sync.connector_id}", app_name=Connectors.SLACK_WORKSPACE,
                         connector_id=sync.connector_id, source_user_group_id="g-1", name="G", org_id=sync.org_id)
    await sync.processor.batch_upsert_user_groups([group])
    assert await sync.graph.get_user_group_by_external_id(sync.connector_id, "g-1") is None, "skipped"


# --- 11. with search stamps on, the limit and its lift are seen ----------------------------------------------------

async def test_with_stamps_on_setting_and_lifting_the_limit_each_move_the_generation_and_refresh(
    sync: _Sync, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ACL_SCOPE_FIELDS`` on: each write of the limit bumps the connector's structure generation in its own
    transaction (stamps computed before it are stale), then marks the knowledge-hub stamps changed and drops the
    search cache's access lists."""
    from app.config.constants.arangodb import AccessLimit, PermissionModel
    from app.services.permissions.structure_generation import structure_generation_of

    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    await _synced_before_the_upgrade(sync)
    marks: list[str] = []
    real_mark = sync.graph.kh_scope_mark_changed

    async def mark(connector_id: str) -> None:
        marks.append(connector_id)
        await real_mark(connector_id)

    drops = AsyncMock()
    with patch.object(sync.graph, "kh_scope_mark_changed", mark), patch(
        "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_permissions_changed",
        drops,
    ):
        before = structure_generation_of(await _app_doc(sync))
        await sync.processor.set_access_limit(sync.connector_id, AccessLimit.CREATOR)
        limited = structure_generation_of(await _app_doc(sync))
        assert await _readable(sync, sync.bob) == set()
        await sync.processor.clear_access_limit(sync.connector_id, PermissionModel.RECORD_LEVEL)
        lifted = structure_generation_of(await _app_doc(sync))

    assert before is not None and limited == before + 1 and lifted == limited + 1, (before, limited, lifted)
    assert marks == [sync.connector_id, sync.connector_id]
    assert drops.await_count == 2
    assert await _readable(sync, sync.bob) == set(NAMES), "lifted: the stored grants decide again"


# --- 12. PostgreSQL: the limit is not written inside the record transaction ------------------------------------

async def test_on_postgres_concurrent_slices_whose_licence_lapses_limit_without_waiting_on_the_pool() -> None:
    """Every sync in flight sees the lapse at once (the licence answer is process-wide). Four connectors write a slice
    each at the same moment on a pool of two connections: had each taken a second connection for the limit while
    holding its slice's, all would wait for the pool until the acquire timed out and none would be limited."""
    async with _postgres(pool_max=2) as (graph, org):
        syncs = [await _seed(graph, org) for _ in range(4)]
        config = MagicMock(get_config=AsyncMock(return_value=LICENSED))
        for s in syncs:
            await s.processor.on_new_record_groups([s.channel(readers=())])
            await s.processor.on_new_records([s.message("m1", readers=(s.alice, s.bob), inherit=False)])
            s.processor._reads_licence = True
            s.processor.config_service = config
            assert await s.processor.apply_licence_gate(s.connector_id, full_sync=False) is False
        lapsed = MagicMock(get_config=AsyncMock(return_value=UNLICENSED))
        for s in syncs:
            s.processor.config_service = lapsed

        await asyncio.wait_for(asyncio.gather(*(
            s.processor.on_new_records([s.message(n, readers=(s.alice,), inherit=False) for n in ("m1", "m2")])
            for s in syncs
        )), timeout=60)

        for s in syncs:
            assert await _limit(s) == "CREATOR"
            assert await _readable(s, s.bob, ("m1", "m2")) == set()
            assert await _readable(s, s.alice, ("m1", "m2")) == {"m1", "m2"}


# --- 13. inherit edges, source-account links and empty grants written while limited ----------------------------

def _groups(s: _Sync, *, channel_inherits: bool, thread_inherits: bool, readers: tuple[str, ...]):  # noqa: ANN202
    """The channel (top level, under the app) and a thread under it, with whether each inherits from above."""
    channel, grants = s.channel(readers=readers)
    channel.inherit_permissions = channel_inherits
    thread, _ = s.thread("t", created=s.now - DAY_MS)
    thread.inherit_permissions = thread_inherits
    return [(channel, grants), (thread, [s.grant(s.alice)])]


async def test_inherit_edges_written_while_limited_admit_nobody_after_the_lift_once_the_source_breaks_them(
    sync: _Sync,
) -> None:
    """Group inherit edges are structure, not grants, so they are written while unlicensed: the channel comes to
    inherit from the app (everyone linked to it) and the thread from the channel. Licensed again, the source has
    broken both: after the lift bob reads nothing, the edges written while limited included."""
    def source(channel_inherits: bool, thread_inherits: bool):  # noqa: ANN202
        async def script(s: _Sync) -> None:
            await s.processor.on_new_record_groups(_groups(
                s, channel_inherits=channel_inherits, thread_inherits=thread_inherits, readers=(s.alice,),
            ))
            await s.processor.on_new_records([
                s.message("mc", readers=(), inherit=True),
                s.message("mt", thread="t", readers=(), inherit=True),
            ])
        return script

    await source(False, False)(sync)
    assert await _readable(sync, sync.bob, ("mc", "mt")) == set()

    _licence(sync.processor, UNLICENSED)
    await _run(sync, source(True, True))
    channel_id = (await sync.graph.get_record_group_by_external_id(sync.connector_id, f"C-{sync.connector_id}")).id
    assert (await sync.links(RECORD_GROUPS, channel_id))["inherits"] == 1, "the channel's edge to the app, written"
    assert (await sync.links(RECORD_GROUPS, await sync.group_id("t")))["inherits"] == 1
    assert await _readable(sync, sync.bob, ("mc", "mt")) == set(), "the limit holds"

    _licence(sync.processor, LICENSED)
    await _run(sync, source(False, False))
    assert await _limit(sync) is None
    assert (await sync.links(RECORD_GROUPS, channel_id))["inherits"] == 0
    assert (await sync.links(RECORD_GROUPS, await sync.group_id("t")))["inherits"] == 0
    assert await _readable(sync, sync.bob, ("mc", "mt")) == set(), "no inherit edge from the limited time admits bob"
    assert await _readable(sync, sync.alice, ("mc", "mt")) == {"mc", "mt"}


async def test_a_source_account_link_is_not_written_while_unlicensed_and_a_stale_one_admits_nobody_after_the_lift(
    sync: _Sync,
) -> None:
    """bob authenticated the connector as a source account (svc) that reads m1, so he reads m1 through it. Unlicensed,
    the source says he is now authenticated as himself (the link should go) and later as another account: neither
    write happens, and the limit hides m1. The licensed full sync removes the link: after the lift bob reads nothing."""
    svc = f"svc-{sync.connector_id}"
    await sync.graph.batch_upsert_nodes(
        [{"id": svc, "userId": svc, "orgId": sync.org_id, "email": f"{svc}@example.com", "isActive": True,
          "createdAtTimestamp": sync.now, "updatedAtTimestamp": sync.now}],
        collection=CollectionNames.USERS.value,
    )
    links = CollectionNames.AUTHENTICATED_AS.value

    async def stored_links() -> int:
        return len(await sync.graph.get_edges_from_node(f"{CollectionNames.USERS.value}/{sync.bob}", links) or [])

    def source(authenticated_as: str):  # noqa: ANN202
        async def script(s: _Sync) -> None:
            await s.processor.link_authenticator_to_source_user(
                s.connector_id, s.bob, f"{authenticated_as}@example.com", authenticated_as, Connectors.SLACK_WORKSPACE,
            )
            await _source({n: (s.alice, svc) for n in NAMES})(s)
        return script

    await source(svc)(sync)
    assert await stored_links() == 1
    assert await _readable(sync, sync.bob) == set(NAMES), "through the source account's grants"

    _licence(sync.processor, UNLICENSED)
    await _run(sync, source(sync.bob))
    assert await stored_links() == 1, "not removed while unlicensed: left as stored"
    assert await _readable(sync, sync.bob) == set(), "and the limit hides what it reaches"
    await sync.processor.link_authenticator_to_source_user(
        sync.connector_id, sync.bob, f"{sync.alice}@example.com", sync.alice, Connectors.SLACK_WORKSPACE,
    )
    assert await stored_links() == 1, "no new link while unlicensed"

    _licence(sync.processor, LICENSED)
    await _run(sync, source(sync.bob))
    assert await _limit(sync) is None
    assert await stored_links() == 0
    assert await _readable(sync, sync.bob) == set(), "the stale link admits nobody after the lift"
    await sync.graph.delete_nodes_and_edges([svc], CollectionNames.USERS.value)


async def test_a_gated_connectors_empty_grants_keep_the_stored_ones_and_the_limit_still_hides_them(sync: _Sync) -> None:
    """The connector PRs answer a gated read with empty grants (None means "could not read" there); unlicensed, the
    processor keeps what is stored for both a group and a record, and the limit hides it. Licensed, the source's
    real grants replace them at the lift."""
    async def first(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice, s.bob))])
        await s.processor.on_new_records([s.message(n, readers=(s.alice, s.bob), inherit=False) for n in NAMES])

    await first(sync)
    assert await _readable(sync, sync.bob) == set(NAMES)
    channel_id = (await sync.graph.get_record_group_by_external_id(sync.connector_id, f"C-{sync.connector_id}")).id

    async def gated_answers(s: _Sync) -> None:
        await s.processor.on_new_record_groups([(s.channel(readers=())[0], [])])
        await s.processor.on_new_records([s.message(n, readers=(), inherit=False) for n in NAMES])

    _licence(sync.processor, UNLICENSED)
    await _run(sync, gated_answers)
    assert (await sync.links(RECORD_GROUPS, channel_id))["grants"] == 2, "the group's grants, kept"
    assert (await sync.links(RECORDS, await sync.record_id("m1")))["grants"] == 2, "the record's grants, kept"
    assert await _readable(sync, sync.bob) == set(), "and hidden by the limit"

    _licence(sync.processor, LICENSED)
    await _run(sync, _source({"m1": (sync.alice,), "m2": (sync.alice, sync.bob), "m3": (sync.alice,)}))
    assert await _limit(sync) is None
    assert await _readable(sync, sync.bob) == {"m2"}


# --- 14. an old run's end of run limits over a licensed new owner: the watcher heals it ---------------------------

async def test_an_old_runs_finalizer_that_limits_over_a_licensed_new_owner_is_lifted_by_the_full_sync_the_watcher_queues(
    sync: _Sync,
) -> None:
    """Fail closed, then heal. The old run's licence lapsed, its first-skip limit failed and it lost its lease; a
    licensed new owner, which found no limit at its start, finished; the old run's finalizer then stores CREATOR.
    The watcher, already settled, looks again and queues a full sync, and that sync lifts the limit. From the
    moment the limit is stored until the lift, bob reads nothing."""
    from app.connectors.core.sync.sync_coordinator import SyncLease
    from app.connectors.core.sync.sync_runner import _finalize

    await _synced_before_the_upgrade(sync)
    source = _source({"m1": (sync.alice,), "m2": (sync.alice, sync.bob), "m3": (sync.alice,)})

    async def status() -> object:
        return (await _app_doc(sync)).get("status")

    drain = AsyncMock(return_value=[])
    try:
        with await _only_this_org(sync), patch.object(licence_lift, "drain_queued_syncs", drain):
            watcher = asyncio.create_task(licence_lift.watch_licence(
                MagicMock(get_config=AsyncMock(return_value=LICENSED)), sync.graph, logger, 0.05,
            ))
            try:
                await asyncio.sleep(0.5)
                drain.assert_not_awaited()                       # settled: nothing limited

                old = sync.processor
                _licence(old, LICENSED)
                assert await old.apply_licence_gate(sync.connector_id, full_sync=False) is False
                _licence(old, UNLICENSED)
                with patch.object(old, "limit_unlicensed", AsyncMock(side_effect=ConnectionError("refused"))), \
                        pytest.raises(ConnectionError):
                    await old.on_new_records([sync.message("m1", readers=(sync.alice,), inherit=False)])
                lease = SyncLease(sync.connector_id, "old", 1)
                lease.lost.set()

                _restart(sync, LICENSED)                         # the new owner
                await _run(sync, source)
                assert await _limit(sync) is None and await _readable(sync, sync.bob) == {"m2"}

                await _finalize(sync.graph, logger, sync.connector_id, lease, AsyncMock(), access_gate=old)
                assert await _limit(sync) == "CREATOR", "fail closed"
                assert await _readable(sync, sync.bob) == set()

                await _until(status, AppStatus.QUEUED.value)
                assert (await _app_doc(sync)).get(ConnectorStateKeys.PENDING_FULL_SYNC) is True
                assert await _readable(sync, sync.bob) == set(), "still nothing while queued"
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
        await sync.graph.update_node(sync.connector_id, CollectionNames.APPS.value, {"status": AppStatus.IDLE.value})
        await _run(sync, source)                                 # the queued full sync, licensed
        assert await _limit(sync) is None, "healed"
        assert await _readable(sync, sync.bob) == {"m2"}
    finally:
        await sync.graph.delete_nodes_and_edges([sync.org_id], CollectionNames.ORGS.value)
