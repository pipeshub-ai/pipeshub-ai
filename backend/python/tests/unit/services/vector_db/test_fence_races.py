"""Writers racing a cleanup and a pin replacement: every interleaving is driven by a hook at a named point.

Each race runs with the fence in place and, as a control, with the one rule under test disabled; the control must
show the violation, which proves the hook reaches the window and the assertion can fail.
"""

import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.precision_flip import FenceMoved, PrecisionFlip
from app.services.vector_db.collection_epoch import CollectionEpochs
from app.services.vector_db.errors import OrgRequired
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore, Pin
from app.services.vector_db.precision_guard import PrecisionGuard, PrecisionUnreadable
from app.utils.precision_witness import PrecisionWitness, set_precision_witness

log = logging.getLogger("fence-races")
C, ORG = "c", "o1"


class _Db:
    """Points with an org, a record, a precision tag and an epoch. With ``lag`` a write is counted only after ``make_searchable``."""

    def __init__(self, *, lag: bool = False, per_org: bool = False) -> None:
        self.per_org = per_org
        self.points: list[dict] = []
        self.lag = lag
        self.refreshes = 0
        self.exists = True

    def write(
        self, precision: str | None, epoch: str | None, *, vrid: str = "v1", n: int = 1, ident: str | None = None, org: str = ORG,
    ) -> None:
        for i in range(n):
            self.points.append({
                "orgId": org, "virtualRecordId": vrid, "embedPrecision": precision, "embedEpoch": epoch,
                "id": ident or f"{vrid}-{len(self.points)}-{i}", "visible": not self.lag,
            })

    def get_capabilities(self):  # noqa: ANN202
        return SimpleNamespace(spaces_per_org=self.per_org)

    async def make_searchable(self, collection):  # noqa: ANN202
        self.refreshes += 1
        for p in self.points:
            p["visible"] = True

    async def filter_collection(self, must=None, **_k):  # noqa: ANN202
        return dict(must or {})

    def _match(self, p, expression) -> bool:
        return all((p.get(k) in v) if isinstance(v, list) else p.get(k) == v for k, v in expression.items())

    async def count_points(self, collection, expression):  # noqa: ANN202
        return sum(1 for p in self.points if p["visible"] and self._match(p, expression))

    async def delete_points(self, collection, expression, refresh=False):  # noqa: ANN202
        # Every provider refuses a delete that does not name one organisation (``filters.require_filter_org``).
        if not expression.get("orgId"):
            raise OrgRequired("the filter carries no organisation (metadata.orgId in MUST is required)")
        self.points = [p for p in self.points if not self._match(p, expression)]

    async def get_collection_info(self, collection):  # noqa: ANN202
        return SimpleNamespace(exists=True, points_count=sum(1 for p in self.points if p["visible"]), dense_dimension=4)

    def precisions(self) -> set:
        return {p["embedPrecision"] for p in self.points if p["embedPrecision"]}


class _World:
    def __init__(self, *, lag: bool = False, clock=None, per_org: bool = False) -> None:
        self.config = ConfigurationService(log, InMemoryKeyValueStore(log))
        self.pins = CollectionPinStore(self.config, log, **({"clock": clock} if clock else {}))
        self.db = _Db(lag=lag, per_org=per_org)
        self.guard = PrecisionGuard(self.pins, self.db, log)

    async def cleanup(self, collection: str = C) -> None:
        """What ``CollectionRegistry._drop`` does: new epoch, drop, confirm, release the pins."""
        ticket = await self.pins.epochs.begin_reset(collection)
        self.db.points.clear()
        await self.pins.epochs.confirm_reset(ticket)
        await self.pins.release_collection(collection)

    async def batch(
        self, precision: str, *, before_verify=None, before_write=None, after_write=None, vrid: str = "v1", ident: str | None = None,
    ) -> None:
        """One writer batch as the real writers do it: capture, check, write, confirm, and remove what it wrote on a refusal."""
        fence = await self.guard.capture(C)
        if before_verify:
            await before_verify()
        await self.guard.verify_batch(C, ORG, precision, fallback=False, fence=fence)
        if before_write:
            await before_write()
        self.db.write(precision, fence.token, vrid=vrid, ident=ident)
        if after_write:
            await after_write()
        try:
            await self.guard.confirm_after_write(C, ORG, precision, fence)
        except PrecisionFlip:
            await self.guard.delete_written(C, ORG, precision, fence, {"virtualRecordId": [vrid]})
            raise


@pytest.fixture
def world() -> _World:
    return _World()


@pytest.fixture(autouse=True)
def _witness():  # noqa: ANN202
    witness = PrecisionWitness()
    witness.record("m", FP32)
    set_precision_witness(witness)
    yield witness
    set_precision_witness(None)


def _fence_off(monkeypatch) -> None:
    """The control: the epoch never moves."""
    async def never(self, fence):  # noqa: ANN202
        return False

    monkeypatch.setattr(CollectionEpochs, "moved", never)


# -- a rollback on a store that refuses a delete without an organisation --------------------------------------

@pytest.mark.asyncio
async def test_the_rollback_of_a_batch_of_the_old_precision_removes_its_points_on_a_shared_store(world) -> None:
    world.db.write(INT8, None, vrid="other")
    await world.pins._claim_unfenced(C, INT8)

    async def replaced_and_claimed() -> None:
        await world.pins.release(C, None)
        world.db.points = [p for p in world.db.points if p["virtualRecordId"] != "other"]
        await world.pins._claim_unfenced(C, FP32)

    with pytest.raises(PrecisionFlip):
        await world.batch(INT8, before_write=replaced_and_claimed)
    assert INT8 not in world.db.precisions()  # the rollback carried the writer's organisation, so the store accepted it


@pytest.mark.asyncio
async def test_a_sweep_of_a_shared_store_deletes_for_the_organisation_that_asked(world) -> None:
    old = await world.guard.capture(C)
    world.db.write(INT8, old.token)
    await world.cleanup()
    world.db.write(INT8, old.token)  # a straggler of the retired token
    await world.guard.sweep_dead(C, ORG)
    assert world.db.points == []


@pytest.mark.asyncio
@pytest.mark.parametrize("per_org", [True, False])
async def test_a_sweep_for_one_organisation_does_not_hide_another_organisations_retired_points(monkeypatch, per_org) -> None:
    now = [1_000_000.0]
    world = _World(clock=lambda: now[0], per_org=per_org)
    monkeypatch.setattr("app.services.vector_db.precision_guard.sweep_window_seconds", lambda: 5700.0)
    old = await world.guard.capture(C)
    await world.cleanup()
    world.db.write(INT8, old.token, org="o1", vrid="a")  # stragglers of the retired token, in two organisations
    world.db.write(INT8, old.token, org="o2", vrid="b")
    now[0] += 6000  # the window is over
    await world.guard.sweep_dead(C, "o1")
    assert [p["virtualRecordId"] for p in world.db.points] == ["b"]
    # o2's points are still there, so o2 still sees the token as dead and a derivation subtracts them
    assert [d.token for d in await world.pins.epochs.dead(C, "o2" if per_org else None)] == [old.token]
    await world.guard.sweep_dead(C, "o2")
    assert world.db.points == []
    assert await world.pins.epochs.dead(C, "o2" if per_org else None) == []


# -- an index written before pins existed, and a check that claims nothing --------------------------------------

@pytest.mark.asyncio
async def test_a_batch_into_an_index_with_only_untagged_points_completes_and_the_index_gets_a_pin(world) -> None:
    world.db.write(None, None, vrid="old", n=3)  # points from before the tags and the pins
    await world.batch(FP32, vrid="new")  # the confirmation after the write needs a pin, so the check made one
    pin = await world.pins.get(C, fresh=True)
    assert pin is not None and pin.precision == FP32 and pin.derived
    assert {p["virtualRecordId"] for p in world.db.points} == {"old", "new"}


@pytest.mark.asyncio
async def test_a_batch_of_the_other_precision_into_an_untagged_index_is_still_refused(world) -> None:
    world.db.write(None, None, vrid="old")
    with pytest.raises(PrecisionFlip):
        await world.batch(INT8, vrid="new")
    assert world.db.precisions() == set()


@pytest.mark.asyncio
async def test_a_check_that_claims_nothing_replaces_a_stale_pin_over_an_empty_index_and_returns() -> None:
    now = [1_000_000.0]
    world = _World(clock=lambda: now[0])
    await world.pins._claim_unfenced(C, INT8)
    now[0] += 3600  # the pin is old, and the index has nothing in it
    assert await world.guard.verify_batch(C, ORG, FP32, fallback=False, claim=False) is None
    assert await world.pins.get(C, fresh=True) is None  # the leftover is gone; nothing was claimed in its place


# -- a batch in flight across a cleanup ------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("pin_at_cleanup", [False, True])
async def test_a_batch_embedded_before_a_cleanup_claims_nothing_on_the_new_index(world, pin_at_cleanup) -> None:
    if pin_at_cleanup:
        world.db.write(INT8, None)
        await world.pins._claim_unfenced(C, INT8)
    with pytest.raises(FenceMoved):
        await world.batch(INT8, before_verify=world.cleanup)
    assert world.db.points == [] and await world.pins.get(C, fresh=True) is None
    await world.batch(FP32)  # the index takes the new precision
    assert (await world.pins.get(C, fresh=True)).precision == FP32


@pytest.mark.asyncio
async def test_control_without_the_fence_the_old_batch_claims_the_new_index(world, monkeypatch) -> None:
    _fence_off(monkeypatch)
    await world.batch(INT8, before_verify=world.cleanup)
    assert (await world.pins.get(C, fresh=True)).precision == INT8  # the violation: int8 pin over the recreated index


@pytest.mark.asyncio
async def test_a_cleanup_between_the_claim_and_its_recheck_withdraws_the_claim(world) -> None:
    real = world.pins._try_create

    async def create_then_cleanup(*a, **k):  # noqa: ANN202
        result = await real(*a, **k)
        await world.cleanup()
        return result

    world.pins._try_create = create_then_cleanup
    with pytest.raises(FenceMoved):
        await world.batch(INT8)
    assert await world.pins.get(C, fresh=True) is None


@pytest.mark.asyncio
async def test_a_cleanup_after_the_write_lands_removes_the_points_and_restores_no_pin(world) -> None:
    with pytest.raises(FenceMoved):
        await world.batch(INT8, after_write=world.cleanup)
    assert world.db.points == [] and await world.pins.get(C, fresh=True) is None


@pytest.mark.asyncio
async def test_control_a_confirm_that_restores_from_the_points_recreates_the_pin(world) -> None:
    """What the check after the write did before: the points it had just written made the pin."""
    async def restoring(collection, org, precision, fence):  # noqa: ANN202
        if await world.pins.get(collection, None, fresh=True) is None:
            await world.pins._claim_unfenced(collection, precision, None, derived=True)

    world.guard.confirm_after_write = restoring
    await world.batch(INT8, after_write=world.cleanup)
    assert (await world.pins.get(C, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_a_reader_never_restores_a_pin_over_an_index_reset_under_it(world) -> None:
    world.db.write(INT8, None)
    real = world.pins._try_create

    async def cleanup_then_create(*a, **k):  # noqa: ANN202
        await world.cleanup()
        return await real(*a, **k)

    world.pins._try_create = cleanup_then_create
    standing = await world.guard.standing_for_read(C, ORG)
    assert standing.source == "unknown" and await world.pins.get(C, fresh=True) is None


@pytest.mark.asyncio
async def test_a_kv_that_cannot_read_the_epoch_stops_the_batch_before_anything_is_embedded(world, monkeypatch) -> None:
    async def down(self, collection):  # noqa: ANN202
        raise ConnectionError("kv down")

    monkeypatch.setattr(CollectionEpochs, "current", down)
    with pytest.raises(PrecisionUnreadable):
        await world.guard.capture(C)


# -- replacing a pin left by a crash -----------------------------------------------------------------------------

async def _stale(world: _World, precision: str = INT8) -> float:
    """A pin written an hour before the replacer looks; returns the replacer's clock."""
    import time

    await world.pins._config.create_config_if_absent(
        world.pins.key(C), Pin(precision, int((time.time() - 3600) * 1000)).to_value(),
    )
    return time.time()


@pytest.mark.asyncio
async def test_a_new_precision_writer_running_inside_the_replacement_cannot_mix_the_index(world) -> None:
    """The interleaving of the review: the replacer has released the pin; a writer of the new precision runs a whole
    batch before the replacer counts. Old points exist, so the writer is refused and the old pin comes back."""
    now = await _stale(world)
    world.db.write(INT8, None, n=3)
    real = world.db.make_searchable
    ran: list[str] = []

    async def writer_runs_here() -> None:
        try:
            await world.batch(FP32)
        except PrecisionFlip:
            ran.append("refused")

    async def refresh_then_writer(collection):  # noqa: ANN202
        await real(collection)
        if not ran:
            ran.append("running")
            await writer_runs_here()

    world.db.make_searchable = refresh_then_writer
    assert await world.guard.replace_stale(C, FP32, now) is False
    assert ran == ["running", "refused"]
    assert world.db.precisions() == {INT8} and (await world.pins.get(C, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_control_a_replacement_that_does_not_count_the_points_mixes_the_index(world) -> None:
    now = await _stale(world)
    world.db.write(INT8, None, n=3)

    async def sees_nothing(collection, org):  # noqa: ANN202
        from app.services.vector_db.precision_guard import Standing
        return Standing(None, "none")

    world.guard.derive = sees_nothing
    assert await world.guard.replace_stale(C, FP32, now) is True
    await world.batch(FP32)
    assert world.db.precisions() == {INT8, FP32}  # the old points beside the new writer's: what the count prevents


@pytest.mark.asyncio
async def test_an_old_precision_writer_inside_the_replacement_removes_what_it_wrote(world) -> None:
    """W verified under the stale pin before the replacer; the replacer finds nothing, a writer of the new precision
    claims; W then writes and meets a pin that is not its own."""
    now = await _stale(world)
    fence = await world.guard.capture(C)
    await world.guard.verify_batch(C, ORG, INT8, fallback=False, fence=fence)  # W accepted under the old pin
    assert await world.guard.replace_stale(C, FP32, now) is True
    await world.batch(FP32)  # the new writer claims and writes
    world.db.write(INT8, fence.token)  # W's write lands now
    with pytest.raises(PrecisionFlip):
        await world.guard.confirm_after_write(C, ORG, INT8, fence)
    await world.guard.delete_written(C, ORG, INT8, fence, {"virtualRecordId": ["v1"]})
    assert world.db.precisions() == {FP32} and (await world.pins.get(C, fresh=True)).precision == FP32


@pytest.mark.asyncio
async def test_an_old_precision_writer_that_confirmed_before_the_release_is_counted_by_the_replacer(world) -> None:
    now = await _stale(world)
    await world.batch(INT8)  # wrote and confirmed under the stale pin
    assert await world.guard.replace_stale(C, FP32, now) is False  # its points are seen: the old pin comes back
    assert (await world.pins.get(C, fresh=True)).precision == INT8
    with pytest.raises(PrecisionFlip):
        await world.batch(FP32)
    assert world.db.precisions() == {INT8}


@pytest.mark.asyncio
async def test_a_replacer_whose_cleanup_ran_before_its_restore_restores_nothing(world) -> None:
    now = await _stale(world)
    world.db.write(INT8, None)
    real = world.pins._try_create

    async def cleanup_first(*a, **k):  # noqa: ANN202
        await world.cleanup()
        return await real(*a, **k)

    world.pins._try_create = cleanup_first
    with pytest.raises(FenceMoved):
        await world.guard.replace_stale(C, FP32, now)
    assert await world.pins.get(C, fresh=True) is None


@pytest.mark.asyncio
async def test_a_blind_delete_of_a_pin_claimed_meanwhile_churns_but_never_mixes(world) -> None:
    """The replacer read the stale pin; before it deletes, a cleanup and a new writer replace the pin. Its delete removes
    the writer's pin; the points the writer wrote are found by the count and the pin is made again."""
    now = await _stale(world)
    real_get = world.pins.get
    calls = {"n": 0}

    async def get_then_world_moves(collection, org=None, *, fresh=False):  # noqa: ANN202
        pin = await real_get(collection, org, fresh=fresh)
        calls["n"] += 1
        if calls["n"] == 2:  # after the replacer's second read, before its delete
            await world.cleanup()
            await world.batch(FP32)
        return pin

    world.pins.get = get_then_world_moves
    try:
        await world.guard.replace_stale(C, FP32, now)
    except FenceMoved:  # the cleanup in the middle fenced the replacer too: it retries later
        pass
    finally:
        world.pins.get = real_get
    pin = await world.pins.get(C, fresh=True)
    assert world.db.precisions() <= {FP32} and (pin is None or pin.precision == FP32)


# -- a lagging store ----------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_claim_over_points_not_yet_visible_refreshes_first_and_a_rider_cannot_ride_it() -> None:
    world = _World(lag=True)
    world.db.write(INT8, None)  # a writer's point that a count cannot see yet
    with pytest.raises(PrecisionFlip):
        await world.batch(FP32)  # N: refresh, derive sees int8: refused, nothing claimed
    with pytest.raises(PrecisionFlip):
        await world.batch(FP32)  # the rider meets no pin either
    pin = await world.pins.get(C, fresh=True)
    assert (pin is None or pin.precision == INT8) and world.db.precisions() == {INT8}  # at most the pin the points name


@pytest.mark.asyncio
async def test_control_without_the_refresh_the_claim_is_made_over_an_index_that_is_not_empty(monkeypatch) -> None:
    world = _World(lag=True)
    world.db.write(INT8, None)

    async def no_refresh(collection):  # noqa: ANN202
        return None

    world.db.make_searchable = no_refresh
    await world.batch(FP32)
    await world.batch(FP32)  # the rider
    assert world.db.precisions() == {INT8, FP32}


# -- the dead points of retired epochs --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_dead_int8_point_is_neither_int8_nor_legacy_fp32(world) -> None:
    world.db.write(INT8, (await world.guard.capture(C)).token)
    await world.cleanup_keep_points()
    standing = await world.guard.derive(C, ORG)
    assert standing.precision is None


@pytest.mark.asyncio
async def test_the_old_untagged_arithmetic_reads_a_dead_point_as_legacy_fp32(world, monkeypatch) -> None:
    """Control: a total that subtracts only the live counts reads the dead int8 point as a legacy fp32 one."""
    world.db.write(INT8, (await world.guard.capture(C)).token)
    await world.cleanup_keep_points()

    async def live_only(self, collection, org):  # noqa: ANN202
        return {INT8: 0, FP32: 0, "image": 0, "stamp": 0}

    monkeypatch.setattr(PrecisionGuard, "tag_counts", live_only)
    assert (await world.guard.derive(C, ORG)).source == "legacy"


@pytest.mark.asyncio
async def test_a_crashed_writer_after_a_cleanup_does_not_wedge_the_index(world) -> None:
    """W (int8) captured before the cleanup, wrote into the new index and died before it confirmed."""
    fence = await world.guard.capture(C)
    await world.cleanup()
    world.db.write(INT8, fence.token)  # the straggler
    await world.batch(FP32)  # a fp32 writer: the dead point is ignored, then swept
    assert (await world.pins.get(C, fresh=True)).precision == FP32
    await world.guard.sweep_dead(C, ORG)
    assert world.db.precisions() == {FP32}


@pytest.mark.asyncio
async def test_a_failed_drop_leaves_every_valid_point_live(world) -> None:
    await world.batch(INT8)
    await world.pins.epochs.begin_reset(C)  # the drop raised, confirm_reset never ran
    await world.guard.sweep_dead(C, ORG)
    assert len(world.db.points) == 1 and (await world.guard.derive(C, ORG)).precision == INT8


@pytest.mark.asyncio
async def test_a_confirm_that_fails_for_the_epoch_alone_deletes_only_its_own_epochs_points(world) -> None:
    """A second writer wrote the same record under the new epoch; the first one's rollback leaves it."""
    fence_old = await world.guard.capture(C)
    ticket = await world.pins.epochs.begin_reset(C)
    await world.pins.epochs.confirm_reset(ticket)
    await world.batch(INT8, vrid="v1")  # the new epoch's writer, same record
    world.db.write(INT8, fence_old.token, vrid="v1")
    await world.guard.delete_written(C, ORG, INT8, fence_old, {"virtualRecordId": ["v1"]})
    assert [p["embedEpoch"] for p in world.db.points] != [] and all(p["embedEpoch"] != fence_old.token for p in world.db.points)


@pytest.mark.asyncio
async def test_control_an_ids_only_rollback_removes_the_other_writers_points(world) -> None:
    fence_old = await world.guard.capture(C)
    await world.pins.epochs.confirm_reset(await world.pins.epochs.begin_reset(C))
    await world.batch(INT8, vrid="v1")
    world.db.write(INT8, fence_old.token, vrid="v1")
    await world.db.delete_points(C, {"orgId": ORG, "virtualRecordId": ["v1"], "embedPrecision": [INT8]})
    assert world.db.points == []


# -- the sweep window ------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inside_the_window_derive_sweeps_and_counts_the_dead_and_after_it_the_four_count_path_returns(monkeypatch) -> None:
    now = [1_000_000.0]
    world = _World(clock=lambda: now[0])
    monkeypatch.setattr("app.services.vector_db.precision_guard.sweep_window_seconds", lambda: 5700.0)
    await world.batch(INT8)
    await world.cleanup_keep_points()
    counts: list[int] = []
    real = world.db.count_points

    async def counting(collection, expression):  # noqa: ANN202
        counts.append(1)
        return await real(collection, expression)

    world.db.count_points = counting
    await world.guard.derive(C, ORG)
    assert len(counts) == 6  # four totals and the two dead counts
    now[0] += 6000
    await world.guard.sweep_dead(C, ORG)  # the first sweep after the window marks the token swept
    counts.clear()
    await world.guard.derive(C, ORG)
    assert len(counts) == 4 and await world.pins.epochs.dead(C) == [] and await world.pins.epochs.quiet(C, None)


@pytest.mark.asyncio
async def test_control_a_sweep_that_never_marks_swept_keeps_the_six_counts_forever(monkeypatch) -> None:
    now = [1_000_000.0]
    world = _World(clock=lambda: now[0])
    await world.batch(INT8)
    await world.cleanup_keep_points()

    async def never(self, collection, token):  # noqa: ANN202
        return None

    monkeypatch.setattr(CollectionEpochs, "mark_swept", never)
    now[0] += 10 * 86400
    await world.guard.sweep_dead(C, ORG)
    assert await world.pins.epochs.dead(C) != []


def _cleanup_keep_points(self) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        ticket = await self.pins.epochs.begin_reset(C)
        await self.pins.epochs.confirm_reset(ticket)  # the drop left the points here: they are dead stragglers
    return run


async def _cleanup_keep(self: _World) -> None:
    await _cleanup_keep_points(self)()


_World.cleanup_keep_points = _cleanup_keep


@pytest.mark.asyncio
async def test_control_retiring_the_old_token_at_the_start_of_a_reset_hides_valid_points_when_the_drop_fails(world) -> None:
    await world.batch(INT8)
    ticket = await world.pins.epochs.begin_reset(C)
    for token in ticket.seen:  # the broken rule: retire before the drop is confirmed
        await world.pins.epochs._create(world.pins.epochs._state_key(C, token, "retired"), {"retired_at": 0})
    # the drop raised: every valid point is still there, and the index now reads as empty
    assert len(world.db.points) == 1 and (await world.guard.derive(C, ORG)).precision is None


@pytest.mark.asyncio
async def test_the_straggler_of_a_retired_epoch_is_swept_at_the_end_of_the_record_and_by_the_cleanups_tail(world) -> None:
    fence = await world.guard.capture(C)
    await world.cleanup()
    world.db.write(INT8, fence.token, vrid="late")
    world.db.write(FP32, (await world.guard.capture(C)).token)
    await world.guard.sweep_dead(C, ORG)
    assert world.db.precisions() == {FP32}


@pytest.mark.asyncio
async def test_control_without_a_sweep_the_straggler_stays(world) -> None:
    fence = await world.guard.capture(C)
    await world.cleanup()
    world.db.write(INT8, fence.token, vrid="late")
    world.db.write(FP32, (await world.guard.capture(C)).token)
    assert world.db.precisions() == {INT8, FP32}


# -- a batch whose pin is its own precision but whose index was reset and claimed again ----------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("precision", [INT8, FP32])
async def test_a_batch_confirming_after_a_cleanup_and_a_claim_of_its_own_precision_is_removed(world, precision) -> None:
    """The pin is present and of the batch's precision, but it is another writer's pin over a recreated index: the batch's
    points belong to the dropped index and must go, and the other writer's must stay."""
    async def cleanup_then_another_writer_claims() -> None:
        await world.cleanup()
        await world.batch(precision, vrid="other")

    with pytest.raises(FenceMoved):
        await world.batch(precision, before_write=cleanup_then_another_writer_claims, vrid="v1")
    assert {p["virtualRecordId"] for p in world.db.points} == {"other"}
    assert (await world.pins.get(C, fresh=True)).precision == precision


# -- the claim's own epoch check -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_claim_whose_epoch_cannot_be_read_afterwards_is_withdrawn(world, monkeypatch) -> None:
    fence = await world.guard.capture(C)
    real = CollectionEpochs.moved
    calls = {"n": 0}

    async def fails_the_second_read(self, f):  # noqa: ANN202
        calls["n"] += 1
        if calls["n"] == 2:  # the check before the claim passes; the one after it cannot read
            raise ConnectionError("kv down")
        return await real(self, f)

    monkeypatch.setattr(CollectionEpochs, "moved", fails_the_second_read)
    with pytest.raises(FenceMoved):
        await world.guard.verify_batch(C, ORG, INT8, fallback=False, fence=fence)
    assert calls["n"] == 2 and await world.pins.get(C, fresh=True) is None


# -- the sweep -----------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_claim_sweeps_the_points_of_retired_epochs(world) -> None:
    fence = await world.guard.capture(C)
    await world.cleanup()
    world.db.write(INT8, fence.token, vrid="straggler")  # a writer of the dropped index lands late
    await world.batch(FP32, vrid="new")  # the first claim of the new index
    assert {p["virtualRecordId"] for p in world.db.points} == {"new"}
