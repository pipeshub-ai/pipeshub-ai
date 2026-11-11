"""The embedding loop under the consumer's budget: progress, the stall timer, and what a cut means.

Built on the harness of ``test_vectorstore_resume``: the blocks come from the real JSON parser and the
vector store, point ids and in-memory vector service are the real ones. What is added is a mini
consumer that does what the Redis and Kafka consumers do around an attempt (a real ``asyncio.timeout``,
a ``RecordProgress``, the hand-back decision and the attempt counter), so that a record is followed
from its first attempt to ``COMPLETED`` or to the dead letter.
"""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.modules.indexing.embed_resume import (
    EMBED_DELIVERY_ID,
    EMBED_DONE,
    EMBED_SECONDS,
    EMBED_UNCOUNTED,
    marker_of,
)
from app.modules.indexing.record_progress import (
    RecordProgress,
    StallHistory,
    begin_progress,
    carried_counters,
    current_progress,
    end_progress,
)
from tests.unit.modules.transformers.test_vectorstore_resume import (  # noqa: F401 - fixtures
    BATCH,
    RECORD,
    Chain,
    Cut,
    Embedder,
    _batches,
    _bound_org,
    _json_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

DELIVERY = "d1"


class ChainClock:
    """The clock of ``RecordProgress`` in tests that assert on the chain's embedding time.

    It moves only by what ``SlowEmbedder`` says each batch took, so the time recorded on the node, and
    whether the ceiling is reached, do not depend on how busy the host is. It starts at the loop's time,
    so a stall deadline computed from it is never earlier than the real budget, rounded up to a whole
    second so that the times it measures are exact.
    """

    def __init__(self) -> None:
        self.now = float(math.ceil(asyncio.get_running_loop().time()))

    def __call__(self) -> float:
        return self.now


def _run_out_the_budget() -> None:
    """The consumer's budget expires now, as a cap that ran out during this batch would."""
    progress = current_progress()
    assert progress is not None and progress.budget is not None
    progress.budget.reschedule(asyncio.get_running_loop().time())


class SlowEmbedder(Embedder):
    """Takes ``delay`` per batch; can stop answering, or run out the attempt's budget, after some batches of each attempt.

    With a ``clock`` the delay is added to that clock instead of slept.
    """

    def __init__(self, delay: float = 0.0, clock: ChainClock | None = None) -> None:
        super().__init__()
        self.delay = delay
        self.clock = clock
        self.hang_after: int | None = None
        # Where a cap cuts an attempt, in batches rather than seconds, so a busy host cannot move the cut.
        self.budget_after: int | None = None
        self.fail_first_with: BaseException | None = None
        self._in_attempt = 0

    def new_attempt(self) -> None:
        self._in_attempt = 0

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.fail_first_with is not None:
            error, self.fail_first_with = self.fail_first_with, None
            raise error
        if self.hang_after is not None and self._in_attempt >= self.hang_after:
            await asyncio.sleep(3600)
        if self.budget_after is not None and self._in_attempt >= self.budget_after:
            _run_out_the_budget()
            await asyncio.sleep(3600)
        if self.clock is None:
            await asyncio.sleep(self.delay)
        else:
            self.clock.now += self.delay
        self._in_attempt += 1
        return await super().aembed_documents(texts)


def _cas(chain: Chain) -> Any:  # noqa: ANN401
    """The hand-back write as the graph does it: only for this delivery while IN_PROGRESS."""

    async def write(seconds: float, uncounted: int) -> bool:
        node = chain.graph.node
        if node.get(EMBED_DELIVERY_ID) != DELIVERY or node.get("indexingStatus") != "IN_PROGRESS":
            return False
        node.update({"indexingStatus": "QUEUED", EMBED_SECONDS: seconds, EMBED_UNCOUNTED: uncounted})
        return True

    return write


class Consumer:
    """What a consumer does around attempts, in miniature."""

    def __init__(
        self, chain: Chain, *, cap: float, floor: float, ceiling: float = 21_600.0, max_counted: int = 3,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.chain, self.cap, self.floor, self.ceiling, self.max_counted = chain, cap, floor, ceiling, max_counted
        self.clock = clock
        self.counted = 0
        self.uncounted = 0
        self.attempts = 0
        self.progresses: list[RecordProgress] = []

    def progress(self) -> RecordProgress:
        marker = marker_of(self.chain.graph.node)
        seconds, uncounted = carried_counters(marker, DELIVERY)
        p = RecordProgress(
            floor_seconds=self.floor, ceiling_seconds=self.floor, record_ceiling_seconds=self.ceiling,
            history=StallHistory(), **({} if self.clock is None else {"clock": self.clock}),
        )
        p.bind(
            record_id=RECORD, delivery_id=DELIVERY, prior_seconds=seconds, prior_uncounted=uncounted or 0,
            commit=_cas(self.chain),
        )
        return p

    async def run(self, **attempt_kw: Any) -> str:  # noqa: ANN401
        while True:
            self.attempts += 1
            self.chain.graph.node["indexingStatus"] = "IN_PROGRESS"
            progress = self.progress()
            self.progresses.append(progress)
            begin_progress(progress)
            if isinstance(self.chain.embedder, SlowEmbedder):
                self.chain.embedder.new_attempt()
            try:
                async with asyncio.timeout(self.cap) as budget:
                    progress.attach(budget)
                    await self.chain.attempt(DELIVERY, **attempt_kw)
                return "completed"
            except TimeoutError:
                if await progress.settle_cut(lease_intact=True, running=True) is None:
                    self.uncounted += 1
                    continue
                self.counted += 1
                if self.counted >= self.max_counted:
                    return "dead-lettered"
            finally:
                end_progress()


async def _chain(items: int, delay: float = 0.0, *, budget_after: int | None = None, clock: ChainClock | None = None) -> Chain:
    chain = Chain(items)
    chain.embedder = SlowEmbedder(delay, clock)
    chain.embedder.budget_after = budget_after
    return await chain.start()


#: A cap that never expires on its own: tests that need a cut make one with ``budget_after``.
LONG_CAP = 60.0


@pytest.mark.asyncio
class TestARecordThatMakesProgress:
    @pytest.mark.timeout(120)  # 2 s idle; parsing and building 1,200 rows took 35 s at a 3% CPU share
    async def test_a_1200_row_file_finishes_within_a_short_budget_without_dead_lettering_or_embedding_twice(self) -> None:
        # Kills: a counted cut (dead letter after 3 short budgets); a resume that embeds a batch again;
        # a stall limit that cuts a record that keeps finishing batches.
        chain = Chain(content=_json_bytes(1200))
        chain.embedder = SlowEmbedder(0.02)
        chain.embedder.budget_after = 5  # each budget has room for five batches
        await chain.start()
        consumer = Consumer(chain, cap=LONG_CAP, floor=2.0)

        assert await consumer.run() == "completed"

        wanted = await chain.wanted_ids()
        assert consumer.counted == 0
        # One cut per five batches; a stall cut in between would add one.
        assert consumer.uncounted == math.ceil(_batches(len(wanted)) / 5) - 1
        assert chain.parse_calls == 1
        assert chain.ids() == wanted
        # Every point was embedded exactly once: the batch in flight at a cut never finished.
        assert chain.embedder.texts == len(wanted)
        assert len(chain.embedder.batches) == _batches(len(wanted))
        assert consumer.attempts == consumer.uncounted + 1

    async def test_the_embedding_time_of_the_chain_accumulates_on_the_node_across_cuts(self) -> None:
        # Kills: a clock kept in the process or in a message, which a restart would lose.
        clock = ChainClock()
        chain = await _chain(300, 0.25, budget_after=3, clock=clock)
        consumer = Consumer(chain, cap=LONG_CAP, floor=LONG_CAP, clock=clock)
        cuts: list[float] = []
        original = consumer.progress

        def watch() -> RecordProgress:
            cuts.append(chain.graph.node.get(EMBED_SECONDS, 0) or 0)
            return original()

        consumer.progress = watch  # type: ignore[method-assign]
        assert await consumer.run() == "completed"
        assert consumer.uncounted >= 2
        # Each cut adds its three batches of 0.25 s to what the node already had.
        assert cuts == [0.75 * n for n in range(consumer.attempts)]

    async def test_a_chain_that_keeps_making_progress_ends_at_the_ceiling_and_dead_letters_as_before(self) -> None:
        # Kills: no ceiling, which would let a chain whose points keep vanishing requeue for ever.
        # Every attempt embeds two batches of 1 s: the chain has 2 s, then 4 s, then 6 s > 5 s.
        clock = ChainClock()
        chain = await _chain(300, 1.0, budget_after=2, clock=clock)
        consumer = Consumer(chain, cap=LONG_CAP, floor=LONG_CAP, ceiling=5.0, clock=clock)
        assert await consumer.run() == "dead-lettered"
        assert (consumer.counted, consumer.uncounted, consumer.attempts) == (3, 2, 5)
        assert chain.graph.node[EMBED_SECONDS] == 4.0  # a counted cut writes nothing

    async def test_the_ceiling_stays_on_the_node_through_a_restart_of_the_process(self) -> None:
        # Kills: a ceiling held in memory. A new consumer object, as after a restart or a stale
        # recovery, has only the node. Its ceiling is the time the node already records, so its
        # first productive cut is counted only if it reads that time from the node.
        clock = ChainClock()
        chain = await _chain(300, 1.0, budget_after=2, clock=clock)
        first = Consumer(chain, cap=LONG_CAP, floor=LONG_CAP, ceiling=5.0, max_counted=1, clock=clock)
        assert await first.run() == "dead-lettered"
        assert first.uncounted == 2
        spent = chain.graph.node[EMBED_SECONDS]
        assert spent == 4.0

        second = Consumer(chain, cap=LONG_CAP, floor=LONG_CAP, ceiling=spent, max_counted=1, clock=clock)
        assert second.progress().prior_seconds == spent
        assert await second.run() == "dead-lettered"
        assert second.uncounted == 0


@pytest.mark.asyncio
class TestARecordThatStalls:
    async def test_a_record_that_never_finishes_a_batch_dead_letters_after_three_counted_attempts(self) -> None:
        # Kills: a stall that is requeued without counting; a zero-progress attempt treated as progress.
        chain = await _chain(200)
        chain.embedder.hang_after = 0  # type: ignore[attr-defined]
        consumer = Consumer(chain, cap=5.0, floor=0.15)
        assert await consumer.run() == "dead-lettered"
        assert (consumer.counted, consumer.uncounted, consumer.attempts) == (3, 0, 3)

    async def test_a_record_that_hangs_after_progress_is_cut_at_the_stall_limit_and_resumes(self) -> None:
        # Kills: waiting out the whole cap; counting a stall that followed progress.
        chain = await _chain(200)
        real_upsert, writes = chain.db.upsert_points, 0

        async def hangs_after_two(*args: Any, **kw: Any) -> Any:  # noqa: ANN401
            nonlocal writes
            writes += 1
            if writes % 3 == 0:  # the stall timer is off while an embedding call runs, so the hang is in the write
                await asyncio.sleep(3600)
            return await real_upsert(*args, **kw)

        chain.db.upsert_points = hangs_after_two  # type: ignore[method-assign]
        consumer = Consumer(chain, cap=30.0, floor=0.15, ceiling=60.0, max_counted=1)
        started = asyncio.get_running_loop().time()
        outcome = await asyncio.wait_for(consumer.run(), timeout=6.0)
        assert asyncio.get_running_loop().time() - started < 6.0
        # Each attempt added two batches until the points were all there or the ceiling ended the chain.
        assert consumer.uncounted >= 2
        assert outcome in {"completed", "dead-lettered"}


@pytest.mark.asyncio
class TestASlowBatch:
    async def test_a_batch_slower_than_the_stall_floor_is_not_cut_by_the_stall_timer(self) -> None:
        # Kills: a stall timer that runs during the embedding call, so a batch the call timeout allows is cut.
        chain = await _chain(5, delay=0.5)  # 3 x the floor
        consumer = Consumer(chain, cap=30.0, floor=0.15)
        assert await consumer.run() == "completed"
        assert (consumer.counted, consumer.uncounted, consumer.attempts) == (0, 0, 1)

    async def test_a_hung_call_is_bounded_by_its_own_timeout_not_by_the_stall_timer(self) -> None:
        # What bounds a hung batch now: the call timeout, once per attempt, then the attempt fails as an error.
        chain = await _chain(5)
        chain.embedder.hang_after = 0  # type: ignore[attr-defined]
        consumer = Consumer(chain, cap=30.0, floor=0.1)
        started = asyncio.get_running_loop().time()
        with patch("app.modules.transformers.vectorstore._LOCAL_EMBEDDING_BATCH_TIMEOUT_S", 0.3), patch(
            "app.modules.transformers.vectorstore._REMOTE_EMBEDDING_BATCH_TIMEOUT_S", 0.3
        ), patch("app.modules.transformers.vectorstore.retry_delay_seconds", return_value=0.01):
            with pytest.raises(Exception, match="Failed to store batch") as raised:
                await consumer.run()
        assert "failed after 3 attempts" in str(raised.value.__cause__)
        assert asyncio.get_running_loop().time() - started >= 0.85  # three calls of 0.3 s each
        assert consumer.progresses[0].new_units == 0


@pytest.mark.asyncio
class TestAProviderOutage:
    async def test_waiting_out_a_retry_does_not_trip_the_stall_timer(self) -> None:
        # Kills: a retry sleep that counts as a stall. The sleep is twice the stall limit.
        chain = await _chain(40)
        chain.embedder.fail_first_with = asyncio.TimeoutError()  # type: ignore[attr-defined]
        consumer = Consumer(chain, cap=5.0, floor=0.15)
        with patch("app.modules.transformers.vectorstore.retry_delay_seconds", return_value=0.4):
            assert await consumer.run() == "completed"
        assert consumer.attempts == 1

    async def test_without_the_pause_the_same_outage_would_be_cut(self) -> None:
        # The control for the test above: the retry sleep is longer than the limit.
        chain = await _chain(40)
        chain.embedder.fail_first_with = asyncio.TimeoutError()  # type: ignore[attr-defined]
        consumer = Consumer(chain, cap=5.0, floor=0.15)
        with patch("app.modules.transformers.vectorstore.retry_delay_seconds", return_value=0.4), patch.object(
            RecordProgress, "pause", lambda self: None
        ):
            await consumer.run()
        assert consumer.counted + consumer.uncounted >= 1

    async def test_the_time_spent_waiting_counts_toward_the_ceiling(self) -> None:
        # Kills: a ceiling that leaves the outage out.
        chain = await _chain(40)
        chain.embedder.fail_first_with = asyncio.TimeoutError()  # type: ignore[attr-defined]
        consumer = Consumer(chain, cap=5.0, floor=0.15)
        with patch("app.modules.transformers.vectorstore.retry_delay_seconds", return_value=0.4):
            await consumer.run()
        assert consumer.progresses[0].attempt_seconds() >= 0.4


@pytest.mark.asyncio
class TestTheMarkerCountersAndProgress:
    async def test_a_new_chain_writes_every_counter(self) -> None:
        # Kills: a nested or partial first write that leaves counters unset (unknown is counted).
        chain = await _chain(60)
        consumer = Consumer(chain, cap=5.0, floor=1.0)
        await consumer.run()
        first = chain.graph.writes[0]
        assert first[EMBED_SECONDS] == 0.0 and first[EMBED_UNCOUNTED] == 0 and first[EMBED_DONE] == 0

    async def test_the_plan_reports_how_many_points_the_resume_found(self) -> None:
        chain = await _chain(120, budget_after=2)
        consumer = Consumer(chain, cap=LONG_CAP, floor=LONG_CAP)
        planned: list[tuple[int, int]] = []
        real_plan = RecordProgress.plan

        def spy(self: RecordProgress, total: int, done: int, **kw: Any) -> None:  # noqa: ANN401
            planned.append((total, done))
            real_plan(self, total, done, **kw)

        with patch.object(RecordProgress, "plan", spy):
            await consumer.run()
        assert planned[0][1] == 0
        assert planned[1] == (planned[0][0], 2 * BATCH)

    async def test_progress_is_written_to_the_record_for_display_and_only_every_few_batches(self) -> None:
        # Kills: a display write per batch; a display value read back to decide anything.
        chain = await _chain(120)
        consumer = Consumer(chain, cap=10.0, floor=1.0)
        written: list[int] = []

        async def display(done: int) -> None:
            written.append(done)

        original = consumer.progress

        def with_display() -> RecordProgress:
            p = original()
            p._display = display
            return p

        consumer.progress = with_display  # type: ignore[method-assign]
        await consumer.run()
        batches = _batches(len(chain.ids()))
        assert written[0] == 0 and written[-1] <= len(chain.ids())
        assert 1 < len(written) < batches

    async def test_a_restart_of_the_same_event_keeps_its_clock_and_is_never_uncounted(self) -> None:
        # Kills: a rewritten marker that resets the clock; a restart that made progress counted as
        # progress (a digest that differs every time would never dead-letter).
        chain = await _chain(120, 0.01)
        with pytest.raises(Cut):
            await chain.attempt(DELIVERY, cut_after=3)
        node = chain.graph.node
        node[EMBED_SECONDS], node[EMBED_UNCOUNTED] = 321.0, 7
        chain.store._embed_key = lambda: "another-setup"  # type: ignore[method-assign]
        consumer = Consumer(chain, cap=5.0, floor=1.0)
        p = consumer.progress()
        begin_progress(p)
        try:
            async with asyncio.timeout(5) as b:
                p.attach(b)
                await chain.attempt(DELIVERY, complete=False)
        finally:
            end_progress()
        assert p.restarted is True
        assert chain.graph.node[EMBED_SECONDS] == 321.0 and chain.graph.node[EMBED_UNCOUNTED] == 7

    async def test_a_marker_whose_clock_is_unreadable_is_written_back_as_spent(self) -> None:
        # Kills: unknown read as zero when the marker is rewritten.
        chain = await _chain(60)
        consumer = Consumer(chain, cap=5.0, floor=1.0)
        chain.graph.node.update({
            "embedVrid": "vr-1", "embedRev": "rev1", "embedKey": "x", "embedDigest": "y",
            "embedDeliveryId": DELIVERY, EMBED_SECONDS: None, EMBED_UNCOUNTED: None,
        })
        await consumer.run(complete=False)
        assert chain.graph.node[EMBED_SECONDS] >= 21_600.0 - 1


@pytest.mark.asyncio
class TestAfterTheEmbedding:
    async def test_a_slow_verification_after_a_resume_is_not_cut_by_the_stall_limit(self) -> None:
        # Kills: a stall timer still armed once every point exists, which would cut and count a resume
        # whose verification outlasts the stall limit although the cap has room for it.
        from app.modules.transformers.vectorstore import VectorStore

        # The first attempt is cut after four of the seven batches; the second embeds the rest, then verifies.
        chain = await _chain(120, budget_after=4)
        consumer = Consumer(chain, cap=10.0, floor=0.5, max_counted=1)
        real_verify = VectorStore._fence_and_verify

        async def slow_verify(self: VectorStore, *a: Any, **k: Any) -> None:  # noqa: ANN401
            await asyncio.sleep(1.5)  # three stall limits, well inside the cap
            await real_verify(self, *a, **k)

        with patch.object(VectorStore, "_fence_and_verify", slow_verify):
            outcome = await consumer.run()
        assert outcome == "completed"
        assert (consumer.counted, consumer.uncounted) == (0, 1)
        assert consumer.progresses[-1].finished
