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
from tests.unit.modules.pipeline.fakes import ClassifyStage, StubIO

_BudgetHarness = _harness.BudgetHarness
_CLS = _harness.POISON_CLS
_Harness = _harness.WorkerHarness
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


class _RecordsIO(StubIO):
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


class _UnreadableIO(StubIO):
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
        h.registry, h.states, h.headlines, h.coordinator, StubIO,
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
        h.registry, h.states, h.headlines, h.coordinator, StubIO,
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


class _DeletingIO(StubIO):
    """A stage IO whose job's connector is (or is not) being deleted."""

    deleting: ClassVar[set[str]] = set()

    async def connector_deleting(self, job: StageJob) -> bool:
        return job.connector_id in self.deleting


def _harness_with(io: type[StubIO]) -> _Harness:
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

from app.modules.pipeline.pause_budget import PauseLimits, PauseState, Verdict  # noqa: E402
from app.modules.pipeline.pause_judge import PauseJudge  # noqa: E402
from app.modules.pipeline.pause_store import PauseStore, PauseStoreUnavailable  # noqa: E402
from app.services.llm_gateway.fleet_breaker import FleetBreaker, FleetBreakerUnavailable  # noqa: E402
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
    """The pause KV raises: StagePaused before ``stage.run``, 0 model calls, nothing written, whatever the job's status.
    An automatic redrive leaves a budget-failed job QUEUED with its spent budget, so an unreadable budget is never read
    as an absent one; the job runs once Redis answers."""
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

    other = _job(virtual_record_id="vr-2")
    h.seed(other)
    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED)
    with pytest.raises(StagePaused):
        await h.deliver(other)
    assert h.stage.runs == 1 and h.state(other).status is ProgressStatus.QUEUED

    monkeypatch.undo()
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
async def test_the_budget_clock_is_redis_time(fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replicas with different host clocks must agree on a job's wall: the judge reads Redis TIME, not the host clock.
    The Redis server here runs an hour ahead of this host, so a judge that read the host clock would be an hour off."""
    import time

    judge = PauseJudge(PauseStore(), limits=PauseLimits())  # no injected clock
    fleet.clock_ms = None  # the real Redis TIME
    client = fleet.client()
    real_time = client.time

    async def an_hour_ahead() -> tuple[int, int]:
        seconds, micros = await real_time()
        return int(seconds) + 3600, int(micros)

    monkeypatch.setattr(client, "time", an_hour_ahead)
    assert fleet.client().time is an_hour_ahead, "the fleet must hand out the client the skew was applied to"
    job = _job()
    decision = await judge.judge(job, _charged_pause(sent=False, chargeable=False))
    assert decision is not None
    server_ms = await FleetBreaker(fleet).now_ms()
    host_ms = int(time.time() * 1000)
    assert server_ms - host_ms > 3_500_000
    assert abs(decision.decision.state.since_ms - server_ms) < 5000


@pytest.mark.asyncio
async def test_a_paused_job_warns_once_per_episode_not_on_every_wake() -> None:
    """The first pause of an episode is a WARNING; a wake that pauses again is routine. After the job completes, the
    next pause starts a new episode and warns again."""
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.PAUSED, reason="Model provider unavailable: down")
    levels = []
    for _ in range(3):
        with pytest.raises(StagePaused) as paused:
            await h.deliver(job)
        levels.append(paused.value.log_level)
    assert levels == [logging.WARNING, logging.DEBUG, logging.DEBUG]
    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED)
    await h.deliver(job.model_copy(update={"force": True}))
    h.stage.result = StageResult(outcome=StageOutcome.PAUSED, reason="Model provider unavailable: down")
    with pytest.raises(StagePaused) as paused:
        await h.deliver(job.model_copy(update={"force": True}))
    assert paused.value.log_level == logging.WARNING


@pytest.mark.asyncio
async def test_a_job_with_a_tier_and_priority_a_newer_release_added_still_runs() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    payload = {**job.model_dump(mode="json", by_alias=True), "tier": "nano", "priority": "urgent"}

    events = [event.event async for event in h.handler(StreamMessage(eventType="stageJob", payload=payload))]

    assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert h.stage.runs == 1
    assert h.state(job).status is ProgressStatus.COMPLETED


# ---- the budget is updated atomically, fenced by attempt, and a hold's redrives are kept until done -----------------


@pytest.mark.asyncio
async def test_concurrent_pauses_of_one_job_lose_no_send(fleet: FleetRedis) -> None:
    """Two deliveries of one job judge their pauses at the same time: every send is counted (each is added in Redis, not
    read, changed and written back)."""
    h, job = _BudgetHarness(fleet), _job()
    judge = PauseJudge(h.store, clock_ms=h.clock, limits=PauseLimits(absolute_sends=1000, charged_sends=1000))
    pause = _charged_pause(chargeable=False, requests=1)
    await asyncio.gather(*(judge.judge(job, pause, attempt=i) for i in range(1, 41)))
    state = await h.pause_state(job)
    assert state is not None and state.sends == 40


@pytest.mark.asyncio
async def test_a_finished_run_leaves_the_budget_a_later_attempt_wrote(fleet: FleetRedis) -> None:
    """Run 1 completes while run 2 (a redrive that claimed the revision meanwhile) has already paused: run 1's clean-up
    must not hand run 2 a fresh allowance."""
    h, job = _BudgetHarness(fleet), _job()
    await h.judge.judge(job, _charged_pause(chargeable=False, requests=3), attempt=2)
    await h.judge.clear(job.state_key, attempt=1)
    kept = await h.pause_state(job)
    assert kept is not None and kept.sends == 3
    await h.judge.clear(job.state_key, attempt=2)
    assert await h.pause_state(job) is None


@pytest.mark.asyncio
async def test_a_known_cap_holds_when_the_hold_ledger_cannot_be_read(fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """20 sends are known from the budget; the ledger (needed only for the wall) is down. The job is failed, not run."""
    h, job = _BudgetHarness(fleet), _job()
    h.seed(job, ProgressStatus.PAUSED)
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), sends=20, scope=f"{_PK}:{_CLS}"))

    async def down(self, *scopes):  # noqa: ANN202
        raise FleetBreakerUnavailable("ledger down")

    monkeypatch.setattr(FleetBreaker, "ledger", down)
    with pytest.raises(StageFailed):
        await h.deliver(job)
    assert h.stage.runs == 0 and h.state(job).status is ProgressStatus.FAILED


@pytest.mark.asyncio
async def test_a_hold_redrive_is_owed_until_the_job_settles_and_its_reset_succeeds(
    fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hold names a capped job that is still running: it is owed, not dropped. Once it has settled FAILED, the
    leader's pass redrives it, but only after its charge count was reset; a reset that fails leaves it owed."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    h.seed(job, ProgressStatus.IN_PROGRESS)
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    await h.store.add_redrives(scope, [job.state_key])

    assert await h.handler.retry_hold_redrives() == 0
    assert await h.store.pending_redrives() == {scope: [(job.state_key, "")]}, "still running: owed"

    h.states.put(h.state(job).model_copy(update={"status": ProgressStatus.FAILED}))
    real_release = PauseStore.release_charged
    calls = {"n": 0}

    async def flaky(self, key, episode=""):  # noqa: ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise PauseStoreUnavailable("redis blinked")
        return await real_release(self, key, episode)

    monkeypatch.setattr(PauseStore, "release_charged", flaky)
    assert await h.handler.retry_hold_redrives() == 0
    assert h.state(job).status is ProgressStatus.FAILED, "no redrive with the old charge count"
    assert await h.store.pending_redrives() == {scope: [(job.state_key, "")]}

    assert await h.handler.retry_hold_redrives() == 1
    after = await h.pause_state(job)
    assert after is not None and (after.charged, after.sends) == (0, 6)
    assert h.state(job).status is not ProgressStatus.FAILED
    assert await h.store.pending_redrives() == {}


@pytest.mark.asyncio
async def test_a_hold_whose_own_reset_fails_owes_every_job_and_the_job_is_not_failed_on_wake(
    fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third capped job opens the hold, and its own charge-count reset fails right after. Every capped job is still
    owed (the breaker's script recorded them with the hold), and the job's next wake resets it instead of failing it at
    the charged cap while the key is on hold."""
    h = _BudgetHarness(fleet)
    jobs = [_job(virtual_record_id=f"vr-{i}") for i in range(3)]
    h.stage.result = _charged_pause(charged=6)
    for job in jobs[:2]:
        h.seed(job)
        with pytest.raises(StageFailed):
            await h.deliver(job)
    third = jobs[2]
    h.seed(third)
    real_release = PauseStore.release_charged

    async def down_for_the_third(self, key, episode=""):  # noqa: ANN202
        if key == third.state_key:
            raise PauseStoreUnavailable("redis blinked")
        return await real_release(self, key, episode)

    monkeypatch.setattr(PauseStore, "release_charged", down_for_the_third)
    with pytest.raises(StagePaused):
        await h.deliver(third)
    monkeypatch.undo()
    scope = f"{_PK}:{_CLS}"
    assert await h.store.is_owed(scope, third.state_key)
    assert all(h.state(job).status is not ProgressStatus.FAILED for job in jobs[:2]), "the hold redrove the others"

    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED)
    await h.deliver(third)
    assert h.state(third).status is ProgressStatus.COMPLETED, "its charges were the key's: not failed on wake"
    assert not await h.store.is_owed(scope, third.state_key)


# ---- a run's spend is reserved when it starts and committed whatever its outcome --------------------------------------


class _SpendingStage(ClassifyStage):
    """A stage whose run makes ``failed`` failed sends (as its model calls would) and then returns ``result``."""

    def __init__(self, failed: int, result: StageResult) -> None:
        super().__init__()
        self.failed = failed
        self.result = result
        self.allowances: list[int | None] = []

    async def run(self, job: StageJob, io: StageIO) -> StageResult:
        from app.services.llm_gateway.gateway import current_job_trace

        trace = current_job_trace()
        assert trace is not None
        self.allowances.append(trace.meter.allowance)
        trace.meter.failed += self.failed
        self.runs += 1
        return self.result


def _with_stage(h: _BudgetHarness, stage: ClassifyStage) -> None:
    h.stage = stage
    h.registry._stages[stage.name] = stage  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_sends_of_a_run_that_retries_are_committed(fleet: FleetRedis) -> None:
    """A run that sent and then asked to retry (the save found the record busy) still spent them: the next run gets
    only what is left, so retries cannot take the job past the absolute cap."""
    h, job = _BudgetHarness(fleet), _job()
    stage = _SpendingStage(3, StageResult(outcome=StageOutcome.RETRY, reason="record busy"))
    _with_stage(h, stage)
    h.seed(job)
    with pytest.raises(StageRetry):
        await h.deliver(job)
    state = await h.pause_state(job)
    assert state is not None and state.sends == 3
    with pytest.raises(StageRetry):
        await h.deliver(job)
    assert stage.allowances == [20, 17]


@pytest.mark.asyncio
async def test_twin_runs_share_one_allowance(fleet: FleetRedis) -> None:
    """While one run holds its reservation, a twin of the same job gets only what is left, and nothing once the first
    holds it all; a run that died is counted as having spent its share once its reservation goes stale."""
    h, job = _BudgetHarness(fleet), _job()
    stale_ms = 10_000
    assert await h.judge.reserve(job, attempt=1, stale_ms=stale_ms) == 20
    assert await h.judge.reserve(job, attempt=2, stale_ms=stale_ms) == 0, "the first run holds the whole allowance"
    from app.services.llm_gateway.gateway import JobTrace

    trace = JobTrace()
    trace.meter.failed = 4
    await h.judge.commit(job, attempt=1, trace=trace)
    assert await h.judge.reserve(job, attempt=3, stale_ms=stale_ms) == 16
    h.clock.advance(stale_ms + 1)
    assert await h.judge.reserve(job, attempt=4, stale_ms=stale_ms) == 0, "run 3 died holding 16: they count as spent"
    state = await h.pause_state(job)
    assert state is not None and state.sends == 20


@pytest.mark.asyncio
async def test_owed_redrives_are_found_from_the_owed_sets_when_the_index_lost_them(fleet: FleetRedis) -> None:
    """A hold made its capped jobs owed (in the breaker's script), but noting the scope in the index failed after: the
    leader's first pass finds the owed set by scanning and redrives the FAILED job, which would not wake by itself."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    h.seed(job, ProgressStatus.FAILED)
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    await fleet.client().hset(fleet.key(f"sg:{{p:{scope}}}:owed"), job.state_key, "ep-1")
    assert await h.store.pending_redrives() == {}, "the index does not know the scope"
    assert await h.handler.retry_hold_redrives() == 1
    assert h.state(job).status is not ProgressStatus.FAILED


@pytest.mark.asyncio
async def test_an_owed_scope_noted_again_is_not_removed_by_a_pass_that_saw_it_empty(fleet: FleetRedis) -> None:
    """A pass read the scope's set empty and is about to drop it from the index while a hold notes it again: the
    removal compares the add count it read, so the new note stays."""
    from app.modules.pipeline import pause_store as store_module

    h = _BudgetHarness(fleet)
    index = h.store._redrive_index()  # type: ignore[attr-defined]
    await h.store.note_owed_scope("pk:s")
    seen = await fleet.client().zscore(index, "pk:s")
    await h.store.note_owed_scope("pk:s")  # noted again after the pass read it
    await h.store._eval(store_module._REMOVE_IF_SCORE, [index], "pk:s", repr(float(seen)))  # type: ignore[attr-defined]
    assert await fleet.client().zscore(index, "pk:s") is not None



@pytest.mark.asyncio
async def test_a_repeated_redrive_pass_does_not_clear_the_charges_of_the_run_it_started(fleet: FleetRedis) -> None:
    """A pass reset the job's charges and redrove it, but marking it done failed. The new run charges 2 and pauses. The
    next pass finds the job still owed: the reset already happened for this hold episode, so the 2 charges stay."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    h.seed(job, ProgressStatus.FAILED)
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    await h.store.add_redrives(scope, [job.state_key], episode="ep-1")
    assert await h.store.release_charged(job.state_key, "ep-1")
    await h.store.apply(
        job.state_key, attempt=5, sends=2, charged=2, trials=0, since_ms=h.clock(), ledger_at_since=0, scope=scope
    )
    h.states.put(h.state(job).model_copy(update={"status": ProgressStatus.PAUSED}))
    assert await h.handler.retry_hold_redrives() == 1, "nothing more is owed"
    state = await h.pause_state(job)
    assert state is not None and state.charged == 2, "the new run's charges are kept"


@pytest.mark.asyncio
async def test_a_pause_whose_verdict_could_not_be_written_keeps_the_run_s_share(fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """At 19 sends a run makes its last failed send, and Redis fails before the verdict is written. Its reservation
    stays, so the next delivery gets no send until the store answers; it never gets back a send that was spent."""
    h, job = _BudgetHarness(fleet), _job()
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), sends=19))
    assert await h.judge.reserve(job, attempt=1, stale_ms=60_000) == 1

    async def down(self, *args, **kwargs):  # noqa: ANN202
        raise PauseStoreUnavailable("redis blinked")

    monkeypatch.setattr(PauseStore, "apply", down)
    assert await h.judge.judge(job, _charged_pause(requests=1), attempt=1) is None
    monkeypatch.undo()
    assert await h.judge.reserve(job, attempt=2, stale_ms=60_000) == 0


@pytest.mark.asyncio
async def test_a_budget_a_commit_creates_starts_at_the_keys_ledger_now(fleet: FleetRedis) -> None:
    """The key was on hold an hour before this job's first failed send: that hour is not subtracted from its wall."""
    from app.services.llm_gateway.gateway import JobTrace

    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    await fleet.client().set(fleet.key(f"sg:hold:ledger:{{{scope}}}"), 3_600_000)
    trace = JobTrace()
    trace.meter.failed = 2
    trace.note_attempt(_PK, _CLS, "P", chargeable=False, trial=False)
    await h.judge.commit(job, attempt=1, trace=trace)
    state = await h.pause_state(job)
    assert state is not None and state.ledger_at_since == 3_600_000


@pytest.mark.asyncio
async def test_a_wake_after_this_episodes_reset_judges_the_new_runs_charges(fleet: FleetRedis) -> None:
    """The job was reset and redriven for this hold episode; its new run then reached the charged cap on its own. Still
    owed (marking it done failed), its next wake fails it at that cap instead of zeroing the count again."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    await h.store.add_redrives(scope, [job.state_key], episode="ep-1")
    await h.store.release_charged(job.state_key, "ep-1")
    await h.store.apply(job.state_key, attempt=3, sends=6, charged=6, trials=0, since_ms=h.clock(), ledger_at_since=0, scope=scope)
    refusal = await h.judge.spent(job, await h.pause_state(job))
    assert refusal is not None and refusal[0] is Verdict.FAIL_CHARGED


@pytest.mark.asyncio
async def test_an_owed_redrive_whose_publish_failed_is_sent_by_the_next_pass(fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pass reset the job's charges for this episode, then the redrive's publish was refused and the job went back
    to FAILED. The job is still owed, so the next pass redrives it, although this episode's reset was already done."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    h.seed(job, ProgressStatus.FAILED)
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    await h.store.add_redrives(scope, [job.state_key], episode="ep-1")
    real_redrive = h.coordinator.redrive
    calls = {"n": 0}

    async def refused_once(*args, **kwargs):  # noqa: ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("broker refused the publish")
        return await real_redrive(*args, **kwargs)

    monkeypatch.setattr(h.coordinator, "redrive", refused_once)
    assert await h.handler.retry_hold_redrives() == 0
    assert h.state(job).status is ProgressStatus.FAILED and await h.store.is_owed(scope, job.state_key)
    assert await h.handler.retry_hold_redrives() == 1
    assert h.state(job).status is not ProgressStatus.FAILED, "redriven by the next pass"
    assert not await h.store.is_owed(scope, job.state_key)


@pytest.mark.asyncio
async def test_a_wake_that_raced_the_reset_judges_the_budget_as_stored_now(fleet: FleetRedis) -> None:
    """The wake read charged=6, then the leader reset this episode before the wake's own reset: the wake gets ALREADY
    and must judge the stored budget (0 charges), not its stale snapshot, so the job runs."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    await h.store.add_redrives(scope, [job.state_key], episode="ep-1")
    stale = await h.pause_state(job)
    await h.store.release_charged(job.state_key, "ep-1")  # the leader, between the wake's read and its reset
    assert await h.judge.spent(job, stale) is None


@pytest.mark.asyncio
async def test_a_job_that_reached_the_charged_cap_without_pausing_counts_toward_the_hold(fleet: FleetRedis) -> None:
    """A run that does not pause (an SDK retry's failed request, then a clean answer or a crash) commits charged sends. When
    they reach the cap, the next delivery counts the job as capped on its key like a PAUSED outcome does: the third distinct
    job holds the key and is redriven with a fresh count, not failed."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    breaker = FleetBreaker(fleet)
    for i in range(2):
        await breaker.register_capped(_PK, _CLS, f"o-{i}", label="ChatX:m@host", reason="charged_cap")
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))

    assert await h.judge.spent(job, await h.pause_state(job)) is None
    state = await h.pause_state(job)
    assert state is not None and state.charged == 0 and state.sends == 6
    assert await breaker.hold(_PK, _CLS) is not None, "the key is held"


@pytest.mark.asyncio
async def test_a_lone_job_at_the_charged_cap_without_pausing_still_fails(fleet: FleetRedis) -> None:
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    refusal = await h.judge.spent(job, await h.pause_state(job))
    assert refusal is not None and refusal[0] is Verdict.FAIL_CHARGED
    assert await FleetBreaker(fleet).hold(_PK, _CLS) is None


@pytest.mark.asyncio
async def test_a_failing_reconcile_scan_does_not_stop_the_owed_redrives(fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scope index is a fast path. A reconcile scan that cannot run (a node down) is logged and the pass goes on with
    the index it has; the pass counter still advances, so the next tick does not retry only the scan."""
    h, job = _BudgetHarness(fleet), _job()
    scope = f"{_PK}:{_CLS}"
    h.seed(job, ProgressStatus.FAILED)
    await h.store.put(job.state_key, PauseState(since_ms=h.clock(), charged=6, sends=6, scope=scope))
    await h.store.add_redrives(scope, [job.state_key], episode="ep-1")

    async def down(self):  # noqa: ANN202
        raise PauseStoreUnavailable("scan failed")

    monkeypatch.setattr(PauseStore, "reconcile_owed", down)
    before = h.handler._redrive_passes
    assert await h.handler.retry_hold_redrives() == 1
    assert h.handler._redrive_passes == before + 1
