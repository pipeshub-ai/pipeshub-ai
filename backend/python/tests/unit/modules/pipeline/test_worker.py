"""StageJobHandler: claim, skip when current, outcomes, abandonment (UNIT-HW-01..)."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.exceptions.indexing_exceptions import ProcessingError
from app.modules.pipeline.connector_gate import ConnectorBeingDeleted
from app.modules.pipeline.models import (
    Priority,
    StageFingerprint,
    StageJob,
    StageOutcome,
    StageResult,
)
from app.modules.pipeline.stage import StageIO
from app.modules.pipeline.worker import (
    StageFailed,
    StageHeldForDelete,
    StageJobHandler,
    StagePaused,
    StageRetry,
)
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from tests.support import stage_worker_harness as _harness

_BudgetHarness = _harness.BudgetHarness
_CLS = _harness.POISON_CLS
_Harness = _harness.WorkerHarness
_IO = _harness.WorkerIO
_PK = _harness.POISON_PK
_charged_pause = _harness.charged_pause
_job = _harness.make_job


@pytest.mark.asyncio
async def test_a_completed_run_records_its_fingerprint_status_and_successors() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    assert await h.deliver(job) == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    state = h.state(job)
    assert state.status is ProgressStatus.COMPLETED
    assert state.fingerprint == h.expected_fingerprint(job) and state.attempt == 1 and state.worker_id is None
    assert (h.headlines.writes[-1][2], h.headlines.cleared[-1]) == (ProgressStatus.COMPLETED, True)
    assert len(h.producer.jobs("pipeline.summary-embed")) == 1


@pytest.mark.asyncio
async def test_unchanged_inputs_complete_without_running() -> None:  # COST-01 at the stage level
    h, job = _Harness(), _job()
    h.seed(job, fingerprint=h.expected_fingerprint(job))
    await h.deliver(job)
    assert h.stage.runs == 0
    assert h.state(job).status is ProgressStatus.COMPLETED
    assert h.headlines.writes[-1][2] is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_changed_config_runs_again() -> None:
    h, job = _Harness(), _job()
    h.seed(job, ProgressStatus.COMPLETED, fingerprint=h.expected_fingerprint(job))
    h.stage.config = "model-b"
    await h.deliver(job)
    assert h.stage.runs == 1
    assert h.state(job).fingerprint == h.expected_fingerprint(job)


@pytest.mark.asyncio
async def test_force_runs_even_when_current() -> None:
    h, job = _Harness(), _job(force=True)
    h.seed(job, fingerprint=h.expected_fingerprint(job))
    await h.deliver(job)
    assert h.stage.runs == 1


@pytest.mark.parametrize("status", [ProgressStatus.FAILED, ProgressStatus.SKIPPED])
@pytest.mark.asyncio
async def test_a_leftover_delivery_of_a_settled_job_does_nothing(status: ProgressStatus) -> None:
    h, job = _Harness(), _job()
    h.seed(job, status)
    assert await h.deliver(job) == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert h.stage.runs == 0 and h.state(job).status is status and h.headlines.writes == []


@pytest.mark.asyncio
async def test_a_crashed_run_is_taken_over() -> None:
    h, job = _Harness(), _job()
    h.seed(job, ProgressStatus.IN_PROGRESS, worker_id="dead", attempt=1)
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED and h.state(job).attempt == 2


@pytest.mark.asyncio
async def test_a_missing_state_is_recreated_from_the_job() -> None:
    h, job = _Harness(), _job()
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED


class _RecordsIO(_IO):
    """An IO that knows which of the job's records are still on its revision."""

    current: ClassVar[list[str]] = []

    async def current_record_ids(self, job: StageJob) -> list[str]:
        return list(self.current)


@pytest.mark.asyncio
@pytest.mark.parametrize("current, recreated", [(["rec-1"], True), ([], False)], ids=["record live", "record deleted"])
async def test_a_missing_state_is_recreated_only_while_a_record_is_left(
    current: list[str], recreated: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, job = _Harness(), _job()
    monkeypatch.setattr(_RecordsIO, "current", current)
    h.handler._io_for = _RecordsIO  # type: ignore[assignment]

    assert await h.deliver(job) == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]

    assert (job.state_key in h.states.states) is recreated
    assert h.stage.runs == (1 if recreated else 0)
    if not recreated:
        assert h.headlines.writes == [] and h.producer.sent == []
        assert h.handler.outcomes("classify")["stale"] == 1


class _UnreadableIO(_IO):
    async def current_record_ids(self, job: StageJob) -> list[str]:
        raise ConnectionError("graph 503")


@pytest.mark.asyncio
async def test_a_failed_record_read_retries_instead_of_dropping_the_job() -> None:
    h, job = _Harness(), _job()
    h.handler._io_for = _UnreadableIO  # type: ignore[assignment]

    with pytest.raises(ConnectionError):
        await h.deliver(job)

    assert job.state_key not in h.states.states and h.stage.runs == 0
    assert h.handler.outcomes("classify")["stale"] == 0


@pytest.mark.asyncio
async def test_a_superseded_revision_is_settled_without_status_or_successors() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.STALE)
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.SKIPPED
    assert h.state(job).reason == "superseded by a newer revision"
    assert h.headlines.writes == [] and h.producer.sent == []


@pytest.mark.asyncio
async def test_a_skipped_stage_records_its_reason_and_still_dispatches() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.SKIPPED, reason="Extraction skipped: no LLM")
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.SKIPPED
    assert h.headlines.writes[-1][2:] == (ProgressStatus.SKIPPED, "rev-a", "Extraction skipped: no LLM")
    assert len(h.producer.jobs("pipeline.summary-embed")) == 1


@pytest.mark.asyncio
async def test_retry_requeues_and_counts_an_attempt() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.RETRY, reason="LLM returned nothing")
    with pytest.raises(StageRetry):
        await h.deliver(job)
    assert h.state(job).status is ProgressStatus.QUEUED and h.state(job).reason == "LLM returned nothing"
    assert not issubclass(StageRetry, RequeueWithoutAttempt)


@pytest.mark.asyncio
async def test_paused_requeues_without_an_attempt() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.PAUSED, reason="extraction breaker open")
    with pytest.raises(StagePaused) as raised:
        await h.deliver(job)
    assert isinstance(raised.value, RequeueWithoutAttempt)
    assert h.state(job).status is ProgressStatus.PAUSED


@pytest.mark.asyncio
async def test_failed_is_terminal_and_marks_the_record() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.FAILED, reason="document rejected")
    with pytest.raises(StageFailed) as raised:
        await h.deliver(job)
    assert isinstance(raised.value, ProcessingError)
    assert h.state(job).status is ProgressStatus.FAILED
    assert h.headlines.writes[-1][2:] == (ProgressStatus.FAILED, "rev-a", "document rejected")


@pytest.mark.asyncio
async def test_a_run_past_its_budget_is_retried() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.delay = 10
    type(h.stage).budget_s = 0.05
    try:
        with pytest.raises(StageRetry, match="budget"):
            await h.deliver(job)
    finally:
        type(h.stage).budget_s = 5.0
    assert h.state(job).status is ProgressStatus.QUEUED


@pytest.mark.asyncio
async def test_an_unexpected_error_leaves_a_reason_and_propagates() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = ConnectionError("graph unavailable")
    with pytest.raises(ConnectionError):
        await h.deliver(job)
    assert h.state(job).status is ProgressStatus.QUEUED
    assert h.state(job).reason == "ConnectionError: graph unavailable"


@pytest.mark.asyncio
async def test_no_successors_when_every_record_moved_on() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.headlines.moved_on = True
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED
    assert h.producer.sent == []


@pytest.mark.asyncio
async def test_invalid_payloads_and_unknown_stages_are_terminal() -> None:
    h = _Harness()
    with pytest.raises(StageFailed, match="invalid stage job"):
        _ = [e async for e in h.handler(StreamMessage(eventType="stageJob", payload={"stage": "classify"}))]
    with pytest.raises(StageFailed, match="no stage named 'entities'"):
        await h.deliver(_job(stage="entities"))


@pytest.mark.asyncio
async def test_outcomes_are_counted_per_stage_for_health() -> None:
    h = _Harness()
    done, current, settled, failing = (_job(virtual_record_id=f"vr-{i}") for i in range(4))
    h.seed(done)
    h.seed(current, fingerprint=h.expected_fingerprint(current))
    h.seed(settled, ProgressStatus.SKIPPED)
    h.seed(failing)
    for job in (done, current, settled):
        await h.deliver(job)
    h.stage.result = ConnectionError("graph unavailable")
    with pytest.raises(ConnectionError):
        await h.deliver(failing)
    message = StreamMessage(eventType="stageJob", payload=failing.model_dump(mode="json", by_alias=True))
    await h.handler.on_message_abandoned(message, reason="transient error", attempts=3)

    counted = {k: v for k, v in h.handler.outcomes("classify").items() if v}
    assert counted == {"completed": 1, "unchanged": 1, "duplicate": 1, "error": 1, "abandoned": 1}
    assert h.handler.outcomes("embed") == {}


class TestAbandonment:
    @pytest.mark.asyncio
    async def test_a_dead_lettered_job_is_failed_with_its_record(self) -> None:
        h, job = _Harness(), _job()
        h.seed(job, ProgressStatus.QUEUED)
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        await h.handler.on_message_abandoned(message, reason="transient error", attempts=3)
        assert h.state(job).status is ProgressStatus.FAILED
        assert "after 3 attempt(s)" in (h.state(job).reason or "")
        assert h.headlines.writes[-1][2] is ProgressStatus.FAILED

    @pytest.mark.asyncio
    async def test_a_settled_job_is_left_alone(self) -> None:
        h, job = _Harness(), _job()
        h.seed(job, ProgressStatus.COMPLETED)
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        await h.handler.on_message_abandoned(message, reason="x", attempts=1)
        assert h.state(job).status is ProgressStatus.COMPLETED and h.headlines.writes == []

    @pytest.mark.asyncio
    async def test_never_raises(self) -> None:
        h, job = _Harness(), _job()
        h.seed(job)
        h.states.fail_cas = True
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        await h.handler.on_message_abandoned(message, reason="x", attempts=1)
        await h.handler.on_message_abandoned(None, reason="x", attempts=1)
        await h.handler.on_message_abandoned(StreamMessage(eventType="stageJob", payload={}), reason="x", attempts=1)


@pytest.mark.asyncio
async def test_a_paused_job_waits_longer_each_time_and_never_less_than_asked() -> None:
    from app.services.messaging.config import compute_retry_backoff_seconds

    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.PAUSED, reason="provider down", retry_after_s=40.0)
    delays: list[float | None] = []
    for _ in range(5):
        with pytest.raises(StagePaused) as raised:
            await h.deliver(job)
        assert raised.value.delay_s == max(40.0, compute_retry_backoff_seconds(h.state(job).attempt))
        delays.append(raised.value.delay_s)
    assert delays == sorted(delays) and delays[-1] == 300.0
    # A clean run that found its dependency down: the crash-loop backstop must not count it.
    assert not StagePaused.counts_toward_backstop


@pytest.mark.asyncio
async def test_a_completed_run_keeps_its_output_with_the_state() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED, output='{"summary": "s"}')
    await h.deliver(job)
    assert h.state(job).output == '{"summary": "s"}'


@pytest.mark.asyncio
async def test_an_unchanged_job_whose_output_was_rewritten_away_restores_it_without_running() -> None:
    """A re-index of unchanged content rewrote the stored record without the classification."""
    h, job = _Harness(), _job()
    kept = '{"summary": "s"}'
    h.seed(job, fingerprint=h.expected_fingerprint(job), output=kept)
    restored: list[str] = []

    async def absent(_job: StageJob, _io: StageIO) -> bool:
        return False

    async def restore(_job: StageJob, _io: StageIO, output: str, *, still_current: Callable[[], Awaitable[bool]]) -> bool:
        restored.append(output)
        return await still_current()

    setattr(h.stage, "output_present", absent)
    setattr(h.stage, "restore_output", restore)
    await h.deliver(job)

    assert restored == [kept] and h.stage.runs == 0
    assert h.state(job).status is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_kept_output_is_not_restored_over_a_run_that_claimed_the_state_meanwhile() -> None:
    """Where the job lease does not hold (it expired, or none is configured), a forced run can
    claim the state between this delivery's read and its restore."""
    h, job = _Harness(), _job()
    h.seed(job, fingerprint=h.expected_fingerprint(job), output='{"summary": "old"}')

    async def absent(_job: StageJob, _io: StageIO) -> bool:
        return False

    async def restore(_job: StageJob, _io: StageIO, _output: str, *, still_current: Callable[[], Awaitable[bool]]) -> bool:
        assert await h.states.cas(job.state_key, expected=ProgressStatus.QUEUED, new=ProgressStatus.IN_PROGRESS)
        return await still_current()

    setattr(h.stage, "output_present", absent)
    setattr(h.stage, "restore_output", restore)
    with pytest.raises(StageRetry):
        await h.deliver(job)

    assert h.stage.runs == 0 and h.state(job).status is ProgressStatus.IN_PROGRESS
    assert not h.headlines.writes


@pytest.mark.asyncio
async def test_an_unchanged_delivery_does_not_report_a_run_claimed_meanwhile_as_complete() -> None:
    h, job = _Harness(), _job()
    h.seed(job, ProgressStatus.COMPLETED, fingerprint=h.expected_fingerprint(job))

    async def present(_job: StageJob, _io: StageIO) -> bool:
        # A forced run claims the state while this delivery checks the stored record.
        assert await h.states.cas(job.state_key, expected=ProgressStatus.COMPLETED, new=ProgressStatus.IN_PROGRESS)
        return True

    setattr(h.stage, "output_present", present)
    with pytest.raises(StageRetry):
        await h.deliver(job)

    assert not h.headlines.writes and h.producer.jobs("pipeline.summary-embed") == []


@pytest.mark.asyncio
async def test_an_unchanged_job_with_nothing_kept_runs_again() -> None:
    h, job = _Harness(), _job()
    h.seed(job, fingerprint=h.expected_fingerprint(job))

    async def absent(_job: StageJob, _io: StageIO) -> bool:
        return False

    setattr(h.stage, "output_present", absent)
    await h.deliver(job)
    assert h.stage.runs == 1


@pytest.mark.asyncio
async def test_a_final_headline_runs_the_settled_hook_and_a_failing_hook_retries() -> None:
    h, job = _Harness(), _job()
    settled: list[tuple[tuple[str, ...], ProgressStatus]] = []
    fail = [True]

    async def on_settled(j: StageJob, record_ids: object, status: ProgressStatus) -> None:
        settled.append((tuple(record_ids), status))  # type: ignore[arg-type]
        if fail[0]:
            fail[0] = False
            raise RuntimeError("copies not released")

    h.handler = StageJobHandler(
        h.registry, h.states, h.headlines, h.coordinator, _IO,
        logger=logging.getLogger("t"), worker_id="w1", clock_ms=h.clock, on_headline_settled=on_settled,
    )
    h.seed(job)
    with pytest.raises(RuntimeError):
        await h.deliver(job)
    # The redelivery finds the run complete and settles it again, without running the stage.
    await h.deliver(job)
    assert h.stage.runs == 1
    assert settled == [(("rec-1",), ProgressStatus.COMPLETED)] * 2


@pytest.mark.asyncio
async def test_no_hook_when_no_record_is_still_on_the_revision() -> None:
    h, job = _Harness(), _job()
    settled: list[object] = []

    async def on_settled(j: StageJob, record_ids: object, status: ProgressStatus) -> None:
        settled.append(record_ids)

    h.handler = StageJobHandler(
        h.registry, h.states, h.headlines, h.coordinator, _IO,
        logger=logging.getLogger("t"), worker_id="w1", clock_ms=h.clock, on_headline_settled=on_settled,
    )
    h.headlines.moved_on = True
    h.seed(job)
    await h.deliver(job)
    assert settled == []


async def _reclaimed_and_taken_by(h: _Harness, job: StageJob, *, other: str | None) -> None:
    """While the first run is working: the sweeper takes its worker for dead and re-queues the
    state, then (with ``other``) another worker claims it as the next attempt."""
    await asyncio.sleep(0.01)
    current = h.state(job)
    h.states.states[job.state_key] = current.model_copy(update={"status": ProgressStatus.QUEUED, "worker_id": None})
    if other is not None:
        h.states.states[job.state_key] = h.state(job).model_copy(
            update={"status": ProgressStatus.IN_PROGRESS, "attempt": current.attempt + 1, "worker_id": other}
        )


@pytest.mark.asyncio
async def test_a_run_whose_state_another_worker_claimed_settles_nothing() -> None:  # review: run ownership
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.delay = 0.05
    run = asyncio.create_task(h.deliver(job))
    await _reclaimed_and_taken_by(h, job, other="w2")
    assert await run == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    state = h.state(job)
    assert (state.status, state.attempt, state.worker_id, state.fingerprint) == (ProgressStatus.IN_PROGRESS, 2, "w2", None)
    assert h.headlines.writes == []
    assert h.producer.sent == []
    assert h.handler.outcomes("classify")["duplicate"] == 1


@pytest.mark.asyncio
async def test_a_reclaimed_run_nobody_took_over_still_records_its_result() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.delay = 0.05
    run = asyncio.create_task(h.deliver(job))
    await _reclaimed_and_taken_by(h, job, other=None)
    await run
    assert h.state(job).status is ProgressStatus.COMPLETED
    assert [w[2] for w in h.headlines.writes] == [ProgressStatus.COMPLETED]


@pytest.mark.asyncio
async def test_a_failing_run_that_lost_its_state_does_not_retry_over_the_owner() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.delay = 0.05
    h.stage.result = StageResult(outcome=StageOutcome.RETRY, reason="model produced nothing")
    run = asyncio.create_task(h.deliver(job))
    await _reclaimed_and_taken_by(h, job, other="w2")
    await run  # no StageRetry: the job is the other worker's now
    assert (h.state(job).status, h.state(job).reason) == (ProgressStatus.IN_PROGRESS, None)


@pytest.mark.asyncio
async def test_an_unchanged_completion_loses_to_a_redrive_that_finished_meanwhile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # review: fence the unchanged path
    h, job = _Harness(), _job()
    h.seed(job, ProgressStatus.COMPLETED, fingerprint=h.expected_fingerprint(job), attempt=1)
    real = h.stage.fingerprint

    async def redrive_lands_first(j: StageJob, io: StageIO) -> StageFingerprint:
        # A forced redrive claims (attempt 2) and completes between this delivery's read and its write.
        h.states.states[job.state_key] = h.state(job).model_copy(update={"attempt": 2, "output": "newer"})
        return await real(j, io)

    monkeypatch.setattr(h.stage, "fingerprint", redrive_lands_first)
    with pytest.raises(StageRetry):
        await h.deliver(job)
    assert (h.state(job).attempt, h.state(job).output) == (2, "newer")
    assert h.headlines.writes == []
    assert h.producer.sent == []


class _DeletingIO(_IO):
    """A stage IO whose job's connector is (or is not) being deleted."""

    deleting: ClassVar[set[str]] = set()

    async def connector_deleting(self, job: StageJob) -> bool:
        return job.connector_id in self.deleting


def _harness_with(io: type[_IO]) -> _Harness:
    h = _Harness()
    h.handler = StageJobHandler(
        h.registry, h.states, h.headlines, h.coordinator, io,
        logger=logging.getLogger("t"), worker_id="w1", clock_ms=h.clock,
    )
    return h


@pytest.mark.asyncio
async def test_a_job_of_a_connector_being_deleted_is_held_without_a_write() -> None:
    """Its records and stage states are the delete's: a claim or headline write here makes the
    delete's batch lose a write conflict on ArangoDB. Held, not settled: the delete may be put back."""
    _DeletingIO.deleting = {"conn-1"}
    h, job = _harness_with(_DeletingIO), _job()
    h.seed(job)
    with pytest.raises(StageHeldForDelete) as held:
        await h.deliver(job)
    assert held.value.counts_toward_backstop is False and held.value.delay_s == 60.0
    assert h.stage.runs == 0
    assert h.state(job).status is ProgressStatus.QUEUED and h.state(job).attempt == 0
    assert h.headlines.writes == [] and h.producer.jobs("pipeline.summary-embed") == []


@pytest.mark.asyncio
async def test_a_held_job_does_not_recreate_its_state() -> None:
    _DeletingIO.deleting = {"conn-1"}
    h, job = _harness_with(_DeletingIO), _job()
    with pytest.raises(StageHeldForDelete):
        await h.deliver(job)
    assert job.state_key not in h.states.states


@pytest.mark.asyncio
async def test_a_held_job_runs_once_the_delete_is_put_back() -> None:
    _DeletingIO.deleting = {"conn-1"}
    h, job = _harness_with(_DeletingIO), _job()
    h.seed(job)
    with pytest.raises(StageHeldForDelete):
        await h.deliver(job)
    _DeletingIO.deleting = set()
    await h.deliver(job)
    assert h.stage.runs == 1 and h.state(job).status is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_delete_that_starts_mid_run_leaves_the_claim_for_the_redelivery() -> None:
    """The IO raises under the lease; nothing is settled, so the claim stays IN_PROGRESS and a
    redelivery after a revert takes it over like a crashed run's."""
    _DeletingIO.deleting = set()
    h, job = _harness_with(_DeletingIO), _job()
    h.seed(job)
    h.stage.result = ConnectorBeingDeleted("conn-1")
    with pytest.raises(StageHeldForDelete):
        await h.deliver(job)
    claimed = h.state(job)
    assert claimed.status is ProgressStatus.IN_PROGRESS and claimed.attempt == 1
    assert h.headlines.writes == []

    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED)
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED and h.state(job).attempt == 2


@pytest.mark.asyncio
async def test_a_job_of_another_connector_runs_as_before() -> None:
    _DeletingIO.deleting = {"conn-other"}
    h, job = _harness_with(_DeletingIO), _job()
    h.seed(job)
    await h.deliver(job)
    assert h.stage.runs == 1 and h.state(job).status is ProgressStatus.COMPLETED


# ---- the pause budget: a paused job is bounded -------------------------------------------------------------

from app.modules.pipeline.pause_budget import PauseLimits, PauseState  # noqa: E402
from app.modules.pipeline.pause_judge import PauseJudge  # noqa: E402
from app.modules.pipeline.pause_store import PauseStore, PauseStoreUnavailable  # noqa: E402
from app.services.llm_gateway.fleet_breaker import FleetBreaker  # noqa: E402
from app.services.llm_gateway.fleet_redis import FleetRedis  # noqa: E402


@pytest.mark.asyncio
async def test_a_poison_job_fails_after_six_charged_sends(fleet: FleetRedis) -> None:
    """Healthy traffic keeps the key's success rate high, so each failed send is charged to the document: at the
    6th the job is FAILED with the reason, and its headline says so. (The main-line worker pauses for ever.)"""
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    h.stage.result = _charged_pause()
    for attempt in range(1, 6):
        with pytest.raises(StagePaused):
            await h.deliver(job)
        state = await h.pause_state(job)
        assert state is not None and (state.charged, state.sends) == (attempt, attempt)
        assert h.state(job).status is ProgressStatus.PAUSED
    with pytest.raises(StageFailed) as failed:
        await h.deliver(job)
    assert "Reindex failed" in str(failed.value) and "6 times" in str(failed.value)
    assert h.state(job).status is ProgressStatus.FAILED
    assert h.state(job).reason is not None and "Reindex failed" in h.state(job).reason
    assert h.headlines.writes[-1][2] is ProgressStatus.FAILED
    assert h.stage.runs == 6


@pytest.mark.asyncio
async def test_a_job_is_failed_at_twenty_sends_when_none_is_charged(fleet: FleetRedis) -> None:
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    h.stage.result = _charged_pause(chargeable=False)
    for _ in range(19):
        with pytest.raises(StagePaused):
            await h.deliver(job)
    with pytest.raises(StageFailed) as failed:
        await h.deliver(job)
    assert "20 times" in str(failed.value)


@pytest.mark.asyncio
async def test_a_refusal_by_an_open_breaker_is_not_a_send(fleet: FleetRedis) -> None:
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    h.stage.result = _charged_pause(sent=False, chargeable=False)
    for _ in range(30):
        with pytest.raises(StagePaused):
            await h.deliver(job)
    state = await h.pause_state(job)
    assert state is not None and (state.sends, state.charged) == (0, 0)


@pytest.mark.asyncio
async def test_a_day_long_quota_outage_ends_failed_at_the_wall(fleet: FleetRedis) -> None:
    """A persistent 429 for 25 h (every send is uncharged and the counts barely move): FAILED at 24 h with the
    provider's reason."""
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    # the breaker is open: refusals, no sends
    h.stage.result = _charged_pause(sent=False, chargeable=False, reason="Model provider unavailable: ChatX:m@host is failing")
    elapsed_h = 0
    with pytest.raises(StagePaused):
        await h.deliver(job)
    while True:
        h.clock.advance(3_600_000)
        elapsed_h += 1
        try:
            await h.deliver(job)
        except StagePaused:
            assert elapsed_h < 24
            continue
        except StageFailed as failed:
            assert "24 h" in str(failed) and "ChatX:m@host" in str(failed)
            break
    assert elapsed_h == 24
    state = await h.pause_state(job)
    assert state is not None and state.sends == 0 and state.charged == 0


@pytest.mark.asyncio
async def test_an_automatic_redispatch_does_not_reset_the_pause_budget(fleet: FleetRedis) -> None:
    """A FAILED-by-budget revision gets an automatic non-forced re-dispatch: the worker FAILs it before it runs, so
    nothing is sent. A forced redrive starts a fresh budget and runs."""
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    h.stage.result = _charged_pause()
    for _ in range(5):
        with pytest.raises(StagePaused):
            await h.deliver(job)
    with pytest.raises(StageFailed):
        await h.deliver(job)
    assert h.stage.runs == 6

    # the sweeper or a connector re-sync dispatches it again: claimed QUEUED, not forced
    h.states.states[job.state_key] = h.state(job).model_copy(update={"status": ProgressStatus.QUEUED})
    with pytest.raises(StageFailed) as failed:
        await h.deliver(job)
    assert h.stage.runs == 6, "the stage must not run again: an automatic re-dispatch makes 0 sends"
    assert "Reindex failed" in str(failed.value)

    # a user's forced redrive: its claim deletes the budget, and the stage runs
    forced = _job(force=True)
    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED)
    view = job.view
    assert await h.coordinator.redrive(view, "classify", priority=Priority.BULK, force=True) is not None
    assert await h.pause_state(job) is None
    await h.deliver(forced)
    assert h.stage.runs == 7 and h.state(job).status is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_redis_down_defers_paused_jobs(fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pause KV raises while the job's state is PAUSED: StagePaused before ``stage.run``, 0 model calls, nothing
    written. A job with no prior pause runs once."""
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    h.stage.result = _charged_pause(chargeable=False)
    with pytest.raises(StagePaused):
        await h.deliver(job)
    assert h.stage.runs == 1 and h.state(job).status is ProgressStatus.PAUSED
    before = h.state(job)

    async def broken(self, key):  # noqa: ANN202
        raise PauseStoreUnavailable("redis is down")

    monkeypatch.setattr(PauseStore, "get", broken)
    for _ in range(3):
        with pytest.raises(StagePaused) as deferred:
            await h.deliver(job)
        assert deferred.value.delay_s > 0
    assert h.stage.runs == 1, "a paused job must not run while its budget cannot be read"
    assert h.state(job) == before, "a deferral writes nothing"

    # a job that has never paused is not blocked by the outage: it runs once
    other = _job(virtual_record_id="vr-2")
    h.seed(other)
    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED)
    await h.deliver(other)
    assert h.stage.runs == 2 and h.state(other).status is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_completion_and_skip_delete_the_budget_and_failure_keeps_it(fleet: FleetRedis) -> None:
    for result, kept in [
        (StageResult(outcome=StageOutcome.COMPLETED), False),
        (StageResult(outcome=StageOutcome.SKIPPED, reason="nothing"), False),
        (StageResult(outcome=StageOutcome.FAILED, reason="bad"), True),
    ]:
        h, job = _BudgetHarness(fleet), _job(virtual_record_id=f"vr-{result.outcome.value}")
        h.seed(job)
        await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=2, sends=3))
        h.stage.result = result
        with contextlib.suppress(StageFailed):
            await h.deliver(job)
        assert (await h.pause_state(job) is not None) is kept, result.outcome


@pytest.mark.asyncio
async def test_an_unchanged_completion_deletes_the_budget(fleet: FleetRedis) -> None:
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job, ProgressStatus.COMPLETED, fingerprint=h.expected_fingerprint(job))
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=2, sends=3))
    await h.deliver(job)
    assert h.stage.runs == 0 and await h.pause_state(job) is None


@pytest.mark.asyncio
async def test_a_job_that_waited_through_a_long_key_hold_is_not_failed_on_wake(fleet: FleetRedis) -> None:
    """A long key hold, through the worker's pre-run check: the job pauses once, the key is held for 30 h while the job wakes
    hourly (refused, nothing sent), then it runs. The wall excludes the key's hold ledger on every path."""
    from app.services.llm_gateway.fleet_hold import HoldEntry, HoldKeeper

    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    h.stage.result = _charged_pause(sent=False, chargeable=False)
    with pytest.raises(StagePaused):
        await h.deliver(job)
    breaker = FleetBreaker(fleet)
    keeper = HoldKeeper(fleet, breaker, clock_ms=h.clock)
    for i in range(3):
        capped = await breaker.register_capped(_PK, _CLS, f"o-{i}", label="ChatX:m@host", reason="charged_cap")
    await keeper.note_hold(HoldEntry(_PK, _CLS, capped.episode, "ChatX:m@host", h.clock(), "org-1", 3))
    for _ in range(30):
        h.clock.advance(3_600_000)
        assert await keeper.tick() == 1
        with pytest.raises(StagePaused):
            await h.deliver(job)  # the pre-run check passes (the hold is excluded) and the stage refuses again
    assert h.stage.runs == 31, "the job ran (and paused) on every wake: it was never failed before it ran"
    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED)
    await fleet.client().delete(breaker.hold_key(_PK, _CLS))
    h.clock.advance(1000)
    await keeper.tick()
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_the_budget_clock_is_redis_time(fleet: FleetRedis) -> None:
    """Replicas with different host clocks must agree on a job's wall: the judge reads Redis TIME, not the host clock."""
    judge = PauseJudge(PauseStore(), limits=PauseLimits())  # no injected clock
    fleet.clock_ms = None  # the real Redis TIME
    job = _job()
    decision = await judge.judge(job, _charged_pause(sent=False, chargeable=False))
    assert decision is not None
    server_ms = await FleetBreaker(fleet).now_ms()
    assert abs(decision.decision.state.since_ms - server_ms) < 5000

