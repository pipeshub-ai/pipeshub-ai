"""The property projection sweep: which records it finds, that it resumes, and that it never writes
what it should not. Each test names the mutation of the source it must fail under."""

import asyncio
import copy
import json
import random
from pathlib import Path

import pytest

from app.config.constants.arangodb import Connectors
from app.core.org_context import MissingOrgContext, for_org
from app.models.entities import Status
from app.modules.indexing import property_projection_sweep as sweep_module
from app.modules.indexing.property_projection_sweep import (
    MAX_WALK_ATTEMPTS,
    GraphStateStore,
    Scope,
    SweepState,
    connector_of_app,
)
from app.modules.record_metadata.projection import ScopeUnionCache
from app.services.graph_db import property_projection_queries as queries
from tests.unit.modules.indexing.sweep_world import (
    KB,
    FakeLeases,
    NoLeases,
    World,
    meta,
)
from tests.unit.modules.record_metadata.projection_world import (
    ORG,
    add_ticket,
    kb_doc,
    set_ticket_status,
    ticket,
)

pytestmark = pytest.mark.unit

INTERVAL = 900_000
HOUR = 3_600_000


class Killed(BaseException):
    """The process dies here: not an Exception, so nothing in the sweep can absorb it."""


async def _settle(world: World, sweeper, *, passes: int = 60) -> None:
    """Passes (a lap per scope each) until two in a row write nothing and leave no lap part done."""
    quiet = 0
    for _ in range(passes):
        before = len(world.graph.writes)
        await sweeper.run_pass()
        world.clock.now += INTERVAL
        in_progress = any(json.loads(a["propSweep"]).get("lap") for a in world.graph.apps.values() if a.get("propSweep"))
        quiet = quiet + 1 if len(world.graph.writes) == before and not in_progress else 0
        if quiet >= 2:
            return
    raise AssertionError("the sweep never settled")


class DyingAt:
    """Wraps a callable; the n-th call raises ``Killed`` instead of running."""

    def __init__(self, inner, n: int) -> None:
        self.inner, self.n, self.calls = inner, n, 0

    async def __call__(self, *args, **kwargs) -> object:
        self.calls += 1
        if self.calls == self.n:
            raise Killed
        return await self.inner(*args, **kwargs)


class DyingStore(GraphStateStore):
    def __init__(self, graph, n: int) -> None:
        super().__init__(graph)
        self.n, self.saves = n, 0

    async def save(self, scope, epoch, state):  # noqa: ANN201
        self.saves += 1
        if self.saves == self.n:
            raise Killed
        return await super().save(scope, epoch, state)


# ---- T6: resume after a kill ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("crash", ["before_the_page_is_read", "between_read_and_write", "after_write_before_cursor"])
@pytest.mark.parametrize("lap", ["walk", "seek"])
async def test_resume_after_kill(seed: int, crash: str, lap: str) -> None:
    """Killed at a random batch at each crash point, in each lap kind: the restart ends with every record
    projected exactly once (no gap, no duplicate) and one more pass writes nothing.

    Kills: a cursor stored before the page's writes (gaps); a write not conditional on the record being stale
    (duplicates after the restart replays a page)."""
    rng = random.Random(seed)
    world = World()
    await world.register("team")
    if lap == "seek":
        await world.sweep().run_pass()  # the first walk over nothing sets the watermark
        world.clock.now += INTERVAL
    ids = [f"r{i:03d}" for i in range(180)]
    rng.shuffle(ids)
    for key in ids:
        world.add(key, updated=world.clock.now + rng.randint(0, 5))
    victim = rng.randint(2, 3)
    graph, over = world.graph, {}
    page_name = "get_records_needing_projection" if lap == "walk" else "get_records_updated_since"
    if crash == "before_the_page_is_read":
        setattr(graph, page_name, DyingAt(getattr(graph, page_name), victim))
    elif crash == "between_read_and_write":
        over["reader"] = DyingAt(world.values, 50 * (victim - 1) + 7)
    else:
        over["state"] = DyingStore(graph, victim + 1)  # +1: the lap's first save is its start
    with pytest.raises(Killed):
        await world.sweep(**over).run_pass()
    world.leases.holders.clear()  # the dead process's lease expired with it
    if page_name in vars(graph):
        delattr(graph, page_name)
    world.clock.now += INTERVAL
    await _settle(world, world.sweep())

    assert world.projected() == len(ids)
    assert sorted(w["key"] for w in graph.writes) == sorted(ids), "each record written exactly once"
    written = len(graph.writes)
    world.clock.now += INTERVAL
    await _settle(world, world.sweep())
    assert len(graph.writes) == written


async def test_records_with_one_write_time_are_not_split_across_pages() -> None:
    """3000 records share one millisecond, page 500; the first page's records cannot be projected (unreadable),
    so they stay stale and a cursor without the id would read them again for ever or skip the rest.
    Kills: a keyset on time alone (rows skipped), or a cursor that drops the id (the lap never ends)."""
    world = World()
    await world.register("team")
    await world.sweep(page_size=500).run_pass()
    world.clock.now += INTERVAL
    ids = [f"t{i:04d}" for i in range(3000)]
    for key in ids:
        world.add(key, updated=world.clock.now)
    world.values.unreadable = set(ids[:500])
    sweeper = world.sweep(page_size=500)
    await sweeper.run_pass()

    assert world.projected() == 2500
    assert len(world.graph.seeks) <= 8
    assert world.state()["lap"] is None


async def test_one_record_stamped_years_ahead_does_not_hide_later_changes() -> None:
    """Kills: a watermark taken as the largest stamp seen (every later real change is behind it)."""
    world = World()
    await world.register("team")
    world.add("future", updated=world.clock.now + 30 * 365 * 24 * HOUR)
    await world.sweep().run_pass()
    world.clock.now += INTERVAL
    await world.sweep().run_pass()
    assert world.state()["watermark"] == world.clock.now - HOUR

    world.add("later", updated=world.clock.now)
    world.clock.now += INTERVAL
    await world.sweep().run_pass()

    assert "later" in {w["key"] for w in world.graph.writes}
    assert world.state()["watermark"] < world.clock.now


# ---- T34: late arrival ---------------------------------------------------------------------------


async def test_late_arrival_is_seen() -> None:
    """A record first synced after the watermark with an old source time, and an upload with no source clock,
    both have a fresh write time and are projected by the next seek.
    Kills: a seek keyed on the source time (the old-source record is behind the watermark)."""
    world = World()
    await world.register("team")
    await world.sweep().run_pass()
    world.clock.now += INTERVAL
    world.add("old-source", updated=world.clock.now, sourceLastModifiedTimestamp=1_000)
    world.add("no-clock", updated=world.clock.now + 1)
    await world.sweep().run_pass()

    assert {w["key"] for w in world.graph.writes} == {"old-source", "no-clock"}
    assert world.graph.seeks and world.state()["lap"] is None


def test_the_seek_ranges_on_the_write_time_not_the_source_time() -> None:
    """The engines' statements, read as text (the stores run them in the integration suite).
    Kills: a seek on sourceLastModifiedTimestamp (M-K)."""
    for text in (queries.build_records_updated_since_cypher(), queries.build_records_updated_since_aql()):
        where = text.split("RETURN")[0]
        assert "updatedAtTimestamp >= " in where.replace("$", "").replace("@", "").replace("r.", "").replace(" ", " ") or "updatedAtTimestamp >=" in where
        ordered = text.split("ORDER BY")[1] if "ORDER BY" in text else text.split("SORT")[1]
        assert ordered.strip().startswith("r.updatedAtTimestamp, r.")
        assert "sourceLastModifiedTimestamp" not in ordered
    assert "r.id > $after_id" in queries.build_records_updated_since_cypher()
    assert "r._key > @after_id" in queries.build_records_updated_since_aql()


# ---- T28: the lease ------------------------------------------------------------------------------


async def test_lease_held_second_pod_writes_nothing() -> None:
    """While pod 1 holds the lease pod 2 selects and writes 0 rows. After pod 1's renewal fails it stops after
    the batch, and its progress write (a stale epoch) is refused.
    Kills: no lease (pod 2 selects). The unfenced progress write (M-M) is killed by
    `test_a_lap_whose_epoch_was_taken_stops_even_with_its_lease_in_hand`, where the lease is still held."""
    world = World()
    await world.register("team")
    for i in range(120):
        world.add(f"r{i:03d}")
    leases = FakeLeases()
    pod1_reader_calls = {"n": 0}
    lease_of_pod1 = {}

    async def reader(doc):  # noqa: ANN202
        pod1_reader_calls["n"] += 1
        if pod1_reader_calls["n"] == 1:
            # Mid-batch: the lease is gone (renewal failed) and pod 2 took the epoch over.
            lease_of_pod1["lease"] = next(iter(leases.holders.values()))
            lease_of_pod1["lease"].lost = True
            state, epoch = await GraphStateStore(world.graph).load(Scope(KB, ORG, Connectors.KNOWLEDGE_BASE))
            assert await GraphStateStore(world.graph).claim(Scope(KB, ORG, Connectors.KNOWLEDGE_BASE), epoch)
        return meta(team="a")

    pod1 = world.sweep(leases=leases, reader=reader)
    pod1_task = pod1.run_pass()

    # Pod 2 meanwhile: while pod 1 holds the lease it does nothing at all.
    class Probe:
        async def acquire(self, key):  # noqa: ANN202
            return await leases.acquire(key)

        async def record_busy(self, rid):  # noqa: ANN202
            return False

    import asyncio

    async def pod2_once() -> dict:
        await asyncio.sleep(0)
        selected = world.graph.selected_rows
        result = await world.sweep(leases=Probe()).run_pass()
        assert world.graph.selected_rows == selected
        return result

    first, second = await asyncio.gather(pod1_task, pod2_once())

    assert second == {"not_leader": 1}
    assert first == {"stopped": 1}
    state = world.state()
    assert state["cursor_id"] is None or state["cursor_id"] < "r050", "pod 1's progress write was refused"
    assert len(world.graph.writes) <= 50, "pod 1 stopped after its batch"


async def test_a_lap_whose_epoch_was_taken_stops_even_with_its_lease_in_hand() -> None:
    """Another process took the epoch while this lap held a lease that has not yet been seen to fail: the next
    progress write is refused and the lap stops there, writing nothing more.
    Kills: a refused progress write that does not end the lap (it would go on through every page)."""
    world = World()
    await world.register("team")
    for i in range(200):
        world.add(f"r{i:03d}")
    taken = {"done": False}
    inner = world.values

    async def reader(doc):  # noqa: ANN202
        if not taken["done"]:
            taken["done"] = True
            scope = Scope(KB, ORG, Connectors.KNOWLEDGE_BASE)
            _, epoch = await GraphStateStore(world.graph).load(scope)
            assert await GraphStateStore(world.graph).claim(scope, epoch)
        return await inner(doc)

    result = await world.sweep(reader=reader).run_pass()

    assert result == {"stopped": 1}
    assert len(world.graph.writes) <= 50, "only the batch in hand was written"


async def test_epoch_claim_is_a_compare_and_set() -> None:
    with for_org(ORG, job="test"):
        await _epoch_claims()


async def _epoch_claims() -> None:
    world = World()
    store = GraphStateStore(world.graph)
    scope = Scope(KB, ORG, Connectors.KNOWLEDGE_BASE)
    first = await store.claim(scope, None)
    assert first == 1
    assert await store.claim(scope, None) is None, "a second claimant of the same epoch loses"
    assert await store.save(scope, 1, SweepState(watermark=5))
    assert await store.claim(scope, 1) == 2
    assert not await store.save(scope, 1, SweepState(watermark=9)), "the old epoch can no longer write"
    assert (await store.load(scope))[0].watermark == 5


async def test_a_held_record_lease_leaves_the_record_to_its_index_run() -> None:
    world = World()
    await world.register("team")
    world.add("busy")
    world.add("free")
    world.leases.busy.add("busy")
    await world.sweep().run_pass()
    assert {w["key"] for w in world.graph.writes} == {"free"}


# ---- T35: the change index -----------------------------------------------------------------------


@pytest.mark.parametrize("state", ["absent", "populating", "failed"])
async def test_index_not_online_falls_back(state: str) -> None:
    """The lap is a walk and the seek (hinted at the index) is never queried.
    Kills: assuming the index exists (the fake refuses a seek while it is not online)."""
    world = World()
    await world.register("team")
    await world.sweep().run_pass()  # a first walk: the scope is walked and has a watermark
    world.clock.now += INTERVAL
    world.add("late")
    world.graph.index = state
    walks = len(world.graph.walks)
    await world.sweep().run_pass()

    assert len(world.graph.walks) > walks
    assert not world.graph.seeks
    assert {w["key"] for w in world.graph.writes} == {"late"}
    assert world.graph.ensure_calls >= 1, "the sweep asks for the index every pass"


# ---- T36: two processes, two unions, no lease ----------------------------------------------------


async def test_two_pods_two_unions_converge() -> None:
    """A pod that has not learned the newest schema sweeps after one that has: the greater union stands.
    Kills: dropping the generation condition from the projection write (the stale pod deletes `region`)."""
    world = World()
    await world.register("team")
    for i in range(30):
        world.add(f"r{i:02d}")
    stale_cache, fresh_cache = ScopeUnionCache(), ScopeUnionCache()
    stale_pod = world.sweep(cache=stale_cache, leases=NoLeases())
    await stale_pod.run_pass()  # learns v1 only, then (below) never refreshes within its TTL
    await world.register("team", "region")
    world.values.default = meta(team="a", region="north")
    fresh_pod = world.sweep(cache=fresh_cache, leases=NoLeases())
    world.clock.now += INTERVAL
    await _settle(world, fresh_pod)
    assert all(world.graph.props(k) == {"team": "a", "region": "north"} for k in world.graph.records)

    world.clock.now += 25 * HOUR  # the repair is due: the stale pod walks too
    await _settle(world, stale_pod)

    assert all(world.graph.props(k) == {"team": "a", "region": "north"} for k in world.graph.records)


# ---- T37, T7, T20, T23 ---------------------------------------------------------------------------


async def test_duplicate_copy_completed_record_is_converged() -> None:
    """A record completed as a copy of another never ran `index()`: it has no projection controls at all and
    only the walk finds it. Kills: a walk that selects on a marker `index()` sets."""
    world = World()
    t = ticket(record_id="copy-1")
    add_ticket(world.graph, t, indexingStatus="COMPLETED", connectorId="conn-1")
    world.graph.apps["conn-1"] = {"_key": "conn-1", "orgId": ORG, "type": "Jira"}
    await world.sweep().run_pass()
    assert world.graph.props("copy-1")["status"] == "OPEN"
    assert world.graph.records["copy-1"]["propsSet"]


async def test_a_record_whose_write_time_did_not_move_is_found_by_the_repair_only() -> None:
    """After the scope was walked, a record that changes with no new write time is behind the seek's watermark: only the daily
    repair walk finds it. Kills: a repair that is never due (the record stays unprojected), a pass that walks every time
    (it would be found before the repair is due)."""
    world = World()
    await world.register("team")
    await world.sweep().run_pass()                       # the first lap is a walk over nothing: the scope is walked
    world.clock.now += INTERVAL
    world.add("quiet", updated=1_000)                    # old write time: the seek cannot see it
    await world.sweep().run_pass()
    assert "quiet" not in {w["key"] for w in world.graph.writes}
    assert world.graph.walks == [{"after_key": None}] and world.graph.seeks, "the second pass was a seek, not a walk"
    world.clock.now += 25 * HOUR                         # the repair is due
    await world.sweep().run_pass()
    assert "quiet" in {w["key"] for w in world.graph.writes}
    assert len(world.graph.walks) == 2


async def test_attachments_are_stamped() -> None:
    """A Jira attachment (no metadata) is stamped with no values, so it is projected and not selected again.
    Kills: a walk that skips records the builder answers `absent` for (they would be selected for ever)."""
    world = World()
    world.graph.apps["conn-1"] = {"_key": "conn-1", "orgId": ORG, "type": "JIRA"}
    world.graph.records["att-1"] = kb_doc("att-1", kb_id="conn-1", connectorName=Connectors.JIRA.value, updated=4_000)
    await world.sweep().run_pass()
    doc = world.graph.records["att-1"]
    assert doc["propsSet"] and doc["propsSrc"] == 4_000 and world.graph.props("att-1") == {}
    selected = world.graph.selected_rows
    world.clock.now += 26 * HOUR
    await _settle(world, world.sweep())
    assert world.graph.selected_rows == selected


async def test_no_change_resync_selects_nothing_and_a_source_only_change_is_found() -> None:
    """A resync that changes nothing moves no stamp; the next seek selects 0 rows. A change at the source that
    only moves the stamp (no re-index) is found by the next seek, within one interval.
    Kills: a selection on the write time alone, without the staleness test (rows re-selected every lap)."""
    world = World()
    world.graph.apps["conn-1"] = {"_key": "conn-1", "orgId": ORG, "type": "JIRA"}
    t = ticket(record_id="t-1", updated=world.clock.now)
    add_ticket(world.graph, t)
    await world.sweep().run_pass()
    assert world.graph.props("t-1")["status"] == "OPEN"
    writes = len(world.graph.writes)

    world.clock.now += INTERVAL
    world.graph.records["t-1"]["updatedAtTimestamp"] = world.clock.now - 10  # a resync rewrote it, nothing changed
    world.graph.records["t-1"]["propsSrc"] = world.graph.records["t-1"]["updatedAtTimestamp"]
    before = world.graph.selected_rows
    await world.sweep().run_pass()
    assert world.graph.selected_rows == before and len(world.graph.writes) == writes

    world.clock.now += INTERVAL
    set_ticket_status(world.graph, "t-1", Status.DONE, stamp=world.clock.now)
    await world.sweep().run_pass()
    assert world.graph.props("t-1")["status"] == "DONE"


async def test_rows_of_a_grown_union_are_selected_while_stale() -> None:
    """The sweep half of the stale window: after a name enters the union every record of the scope is selected
    (its `propsSet` is another hash) and is rewritten; none is left on the old set.
    Kills: a scope counted as walked whatever its union (the old rows are behind the watermark, only the walk finds them)."""
    world = World()
    await world.register("team")
    for i in range(10):
        world.add(f"r{i}", updated=1_000)  # written long ago: only the walk can find them
    await world.sweep().run_pass()
    assert {d["propsSet"] for d in world.graph.records.values()} != {None}
    old_hash = world.graph.records["r0"]["propsSet"]
    await world.register("team", "region")
    world.values.default = meta(team="a", region="x")
    world.clock.now += INTERVAL
    await _settle(world, world.sweep())
    assert {d["propsSet"] for d in world.graph.records.values()} != {old_hash}
    assert all(world.graph.props(k) == {"team": "a", "region": "x"} for k in world.graph.records)


# ---- values that cannot be read, failures, the flag, the cap -------------------------------------


async def test_unreadable_values_write_nothing_and_are_retried_a_bounded_number_of_times() -> None:
    """Kills: an unreadable record stamped as having no values (it would lose them for good); a walk that
    repeats for ever on a record that keeps failing."""
    world = World()
    await world.register("team")
    world.add("ok")
    world.add("bad")
    world.values.unreadable = {"bad"}
    sweeper = world.sweep()
    for _ in range(MAX_WALK_ATTEMPTS + 2):
        await sweeper.run_pass()
        world.clock.now += INTERVAL
    assert world.graph.records["bad"].get("propsSet") is None
    assert [w["key"] for w in world.graph.writes] == ["ok"]
    walks_before = len(world.graph.walks)
    await sweeper.run_pass()
    assert len(world.graph.walks) == walks_before, "after the attempts the scope counts as walked"
    assert world.state()["covered_hash"]


async def test_flag_off_reads_and_writes_nothing() -> None:
    world = World()
    await world.register("team")
    world.add("r1")
    reads = list(world.graph.reads)
    result = await world.sweep(enabled=lambda: False).run_pass()
    assert result == {} and world.graph.reads == reads and not world.graph.writes and not world.graph.seeks
    assert not world.graph.walks and world.graph.ensure_calls == 0 and "propSweep" not in world.graph.apps[KB]


async def test_at_most_the_capped_number_of_scopes_run_at_once() -> None:
    """Kills: no per-process cap (every scope sweeps at once)."""
    import asyncio

    world = World()
    await world.register("team")
    for n in range(5):
        key = f"kb-x{n}"
        world.graph.apps[key] = {"_key": key, "orgId": ORG, "type": "KB"}
        await world.registry.register(ORG, key, __import__("tests.unit.modules.record_metadata.projection_world", fromlist=["defs"]).defs(("team", __import__("app.models.record_metadata", fromlist=["PropertyType"]).PropertyType.STRING)))
        world.graph.records[f"x{n}"] = kb_doc(f"x{n}", kb_id=key, updated=world.clock.now)
    running = {"now": 0, "max": 0}

    async def reader(doc):  # noqa: ANN202
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return meta(team="a")

    result = await world.sweep(max_scopes=2, reader=reader).run_pass()
    assert running["max"] <= 2 and result["done"] == 6


async def test_the_sweep_yields_for_its_duty_share() -> None:
    """Kills: no pause between batches (the sweep takes a whole core)."""
    world = World()
    await world.register("team")
    for i in range(120):
        world.add(f"r{i:03d}")
    ticks = iter(x * 0.5 for x in range(10_000))
    await world.sweep(monotonic=lambda: next(ticks), duty=0.1).run_pass()
    assert world.sleeps and all(abs(s - 0.5 * 9) < 1e-9 for s in world.sleeps)


def test_the_sweep_is_its_own_loop_and_not_a_step_of_the_recovery_lap() -> None:
    """Kills: running the sweep inside `recover_in_progress_records` (a long sweep would starve the lap)."""
    source = Path(sweep_module.__file__).parents[2].joinpath("indexing_main.py").read_text()
    lap = source.split("async def recover_in_progress_records")[1].split("\nasync def run_stale_recovery_loop")[0]
    assert "property_projection" not in lap
    assert source.count("run_property_projection_sweep_loop(app_container, graph_provider, _all_org_ids)") == 2


def test_state_that_cannot_be_read_is_a_scope_never_swept() -> None:
    assert SweepState.load("not json") == SweepState()
    assert SweepState.load(None) == SweepState()
    state = SweepState(watermark=3, covered_hash="h", lap="seek", cursor_id="x")
    assert SweepState.load(state.dump()) == state
    assert SweepState.load(json.dumps({"watermark": 4, "unknown": 1})).watermark == 4


def test_an_app_maps_to_its_connector_or_to_nothing() -> None:
    assert connector_of_app({"type": "KB"}) is Connectors.KNOWLEDGE_BASE
    assert connector_of_app({"type": "Jira"}) is Connectors.JIRA
    assert connector_of_app({"type": "No Such Thing"}) is None
    assert connector_of_app({}) is None


def test_the_statements_select_the_same_staleness_on_both_engines() -> None:
    cypher = queries.build_records_needing_projection_cypher(has_after_key=True)
    aql = queries.build_records_needing_projection_aql(has_after_key=True)
    for field in ("propsSet", "propsGen", "propsSrc"):
        assert field in cypher and field in aql
    assert "isDeleted" in cypher and "isDeleted" in aql
    assert "r.orgId = $org_id" in cypher and "r.orgId == @org_id" in aql
    assert "ORDER BY r.id" in cypher and "SORT r._key" in aql


# ---- org context: a store that keeps each org apart (PostgreSQL) fails a read made outside one ------------------------------


async def test_every_read_and_write_of_a_pass_runs_in_the_org_of_its_scope() -> None:
    """Kills: sweeping without binding the org (the fake, like PostgreSQL, refuses an unbound read)."""
    world = World()
    await world.register("team")
    world.add("a")
    await world.sweep().run_pass()
    assert world.graph.orgs_seen == {ORG}
    assert {w["key"] for w in world.graph.writes} == {"a"}


async def test_an_app_of_another_org_is_not_a_scope_of_this_one() -> None:
    """A store that lists every org's apps in one read is cut to the org asked for. Kills: taking its scopes as they come."""
    world = World()
    world.graph.apps["foreign"] = {"_key": "foreign", "orgId": "org-2", "type": "KB"}
    world.graph.lists_every_org = True
    scopes = await world.sweep(org_ids=lambda: _orgs([ORG])).scopes()
    assert [(s.key, s.org_id) for s in scopes] == [(KB, ORG)]


async def test_a_read_outside_an_org_fails_the_fake_as_it_fails_postgres() -> None:
    world = World()
    with pytest.raises(MissingOrgContext):
        await world.graph.get_all_documents("apps")


async def _orgs(orgs: list[str]) -> list[str]:
    return orgs


# ---- a write time that is not a whole number ----------------------------------------------------------------------


async def test_a_fractional_write_time_tie_with_failing_rows_ends_the_lap() -> None:
    """1200 records share the write time `now + 0.5`, page 500, and 600 of them cannot be projected, so they stay stale. The cursor holds the
    time exactly as the store returned it; truncated, it re-selects the whole tie and the lap never ends.
    Kills: `int()` on the cursor's write time (the lap re-reads one page for ever)."""
    world = World()
    await world.register("team")
    await world.sweep(page_size=500).run_pass()
    world.clock.now += INTERVAL
    ids = [f"t{i:04d}" for i in range(1200)]
    for key in ids:
        world.add(key, updated=world.clock.now + 0.5)
    world.values.unreadable = set(ids[:600])
    await asyncio.wait_for(world.sweep(page_size=500).run_pass(), 20)
    assert world.state()["lap"] is None
    assert len(world.graph.seeks) <= 6, "the lap read each page once"


async def test_a_page_that_moves_no_cursor_ends_the_lap_and_counts_it() -> None:
    """A store that answers the same full page twice (whatever the cause) must not be read for ever.
    Kills: a lap without the progress check (it loops until the process dies)."""
    world = World()
    await world.register("team")
    for i in range(4):
        world.add(f"r{i}")
    page = [dict(world.graph.records[f"r{i}"]) for i in range(2)]

    async def same_page(*args, **kwargs):  # noqa: ANN202
        return copy.deepcopy(page)

    world.graph.get_records_needing_projection = same_page
    before = _lap_count("walk", "stalled")
    assert await asyncio.wait_for(world.sweep(page_size=2).run_pass(), 5) == {"stalled": 1}
    assert _lap_count("walk", "stalled") == before + 1


async def test_one_scope_cannot_hold_the_pass_for_ever() -> None:
    """A scope whose read never answers is cut at its time bound, its lease is released, and the pass ends.
    Kills: a pass that awaits every scope without a bound (one stuck scope stalls all of them)."""
    world = World()
    await world.register("team")
    world.add("a")

    async def hang(*args, **kwargs):  # noqa: ANN202
        await asyncio.sleep(3600)

    world.graph.get_records_needing_projection = hang
    result = await asyncio.wait_for(world.sweep(scope_budget_s=0.05).run_pass(), 5)
    assert result == {"budget": 1}
    assert world.leases.holders == {}, "the lease is released"


async def test_a_scope_works_a_bounded_number_of_pages_per_pass_and_resumes() -> None:
    """50 stale records, page 10, two pages a pass: the first pass pauses with its cursor saved and later passes finish.
    Kills: no page bound (the first pass would read all five pages)."""
    world = World()
    await world.register("team")
    for i in range(50):
        world.add(f"r{i:02d}")
    sweeper = world.sweep(page_size=10, max_pages=2)
    assert await sweeper.run_pass() == {"paused": 1}
    assert world.projected() == 20 and world.state()["lap"] == "walk"
    world.clock.now += INTERVAL
    await _settle(world, sweeper)
    assert world.projected() == 50
    assert sorted(w["key"] for w in world.graph.writes) == sorted(world.graph.records), "each record once"


async def test_resume_after_a_kill_leaves_no_gap_without_the_skew_overlap() -> None:
    """With no skew and no repair the only protection of a record stamped before the lap started is the cursor: it is saved after the page's
    writes, so a kill in the middle of a page's writes replays the page. Kills: a cursor saved before the page's writes."""
    world = World()
    await world.register("team")
    await world.sweep(skew_ms=0, repair_ms=10**12).run_pass()
    world.clock.now += INTERVAL
    ids = [f"r{i:03d}" for i in range(180)]
    for i, key in enumerate(ids):
        world.add(key, updated=world.clock.now - INTERVAL + 1 + i)       # after the watermark, before this lap starts
    over = {"skew_ms": 0, "repair_ms": 10**12, "reader": DyingAt(world.values, 57)}    # dies in the 7th record of the second page
    with pytest.raises(Killed):
        await world.sweep(**over).run_pass()
    world.leases.holders.clear()
    world.clock.now += INTERVAL
    sweeper = world.sweep(skew_ms=0, repair_ms=10**12)
    await _settle(world, sweeper)
    assert world.projected() == len(ids), "no record is left behind the cursor"


def _lap_count(kind: str, outcome: str) -> float:
    """The counter's value as the exposition text states it (0 when the series does not exist yet)."""
    from app.telemetry.backend import METRICS_BACKEND

    prefix = f'pipeshub_record_property_sweep_laps_total{{kind="{kind}",outcome="{outcome}"}}'
    for line in METRICS_BACKEND.serialize().splitlines():
        if line.startswith(prefix):
            return float(line.split()[-1])
    return 0.0
