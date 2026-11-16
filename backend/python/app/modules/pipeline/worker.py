"""Runs one stage job: claim, skip when current, run within budget, record the outcome, dispatch successors.

The stage consumer uses a ``StageJobHandler`` as its message handler and as its
abandonment sink. Each state write is a CAS on the job's own state and on the run that
claimed it (its ``attempt``), and each record-status write is a CAS on the record's content
revision, so neither a duplicate delivery, a run the sweeper reclaimed and handed to another
worker, nor a job for a superseded revision can overwrite newer work. Outcomes map onto what the consumers
already understand: RETRY is a transient error, PAUSED a requeue without an attempt, and
FAILED a terminal error that dead-letters the job.
"""

import asyncio
import logging
import os
import socket
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import Any

from pydantic import ValidationError

from app.config.constants.arangodb import ProgressStatus
from app.core.org_context import for_org
from app.exceptions.indexing_exceptions import ProcessingError
from app.modules.pipeline.connector_gate import ConnectorBeingDeleted
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.models import (
    FOREIGN_REASON,
    ForeignStatus,
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
    StageStatePatch,
)
from app.modules.pipeline.pause_budget import PauseState, Verdict
from app.modules.pipeline.pause_judge import PauseJudge
from app.modules.pipeline.pause_store import PauseStoreUnavailable
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import (
    Deadline,
    HeadlineStatusWriter,
    Stage,
    StageIO,
    StageStateStore,
)
from app.services.llm_gateway.fleet_hold import RECONCILE_EVERY_TICKS
from app.services.llm_gateway.gateway import JobTrace, llm_job
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
    compute_retry_backoff_seconds,
)
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.utils.indexing_timing import ADMITTED, begin_record, end_record

# Called once a stage's headline is final on the records it updated, e.g. to release the
# records' QUEUED md5 copies, which wait for the enrichment to end. Raising makes the
# delivery retry; the redelivery finds the state settled and calls it again.
HeadlineSettled = Callable[[StageJob, Sequence[str], ProgressStatus], Awaitable[None]]


class StagePaused(RequeueWithoutAttempt):
    """A dependency is paused (its breaker is open): requeue without counting an attempt.

    The run ended cleanly, so it does not count toward the delivery backstop either: a job
    waits out an outage of any length, and its growing delay bounds how often it comes back.
    """

    counts_toward_backstop = False


class StageHeldForDelete(StagePaused):
    """The job's connector is being deleted: hand it back for later, having written nothing.

    Not settled, since a delete can be put back: once it sticks the job finds its records gone
    and is dropped as stale; after a revert it runs. A run held mid-way leaves its claim
    IN_PROGRESS, which the redelivery takes over like a crashed run's.

    Only this message waits: the other connectors' jobs on the stage keep running.
    """

    pauses_consumer = False


# How long a job held for a delete waits before it is delivered again.
HELD_FOR_DELETE_DELAY_S = 60.0


class StageRetry(Exception):
    """A transient failure: requeue with backoff, counting an attempt."""


class StageFailed(ProcessingError):
    """A terminal failure: the consumer dead-letters the job."""


# A delivery that finds its state settled here is a leftover duplicate.
_SETTLED = frozenset({ProgressStatus.SKIPPED, ProgressStatus.FAILED})
_TERMINAL = frozenset({ProgressStatus.COMPLETED, ProgressStatus.SKIPPED, ProgressStatus.FAILED})
SUPERSEDED = "superseded by a newer revision"
# How deliveries ended, per stage, for /health. The keys are fixed at construction, so a
# snapshot taken from another thread never sees a dict change size.
OUTCOME_COUNTERS: tuple[str, ...] = (
    "completed", "unchanged", "skipped", "stale", "retry", "paused", "failed", "duplicate", "error", "abandoned",
    # The job's state is in a status a newer release wrote: that release owns it, so nothing ran.
    "foreign",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class StageJobHandler:
    def __init__(
        self,
        registry: StageRegistry,
        states: StageStateStore,
        headlines: HeadlineStatusWriter,
        coordinator: Coordinator,
        io_for: Callable[[StageJob, Deadline], StageIO],
        *,
        logger: logging.Logger,
        worker_id: str | None = None,
        clock_ms: Callable[[], int] = _now_ms,
        on_headline_settled: HeadlineSettled | None = None,
        pause_judge: PauseJudge | None = None,
    ) -> None:
        super().__init__()
        # The pause budget (``pause_budget``). None runs without one: a paused job then waits without bound.
        self._pause = pause_judge
        self._redrive_passes = 0
        self._registry = registry
        self._states = states
        self._headlines = headlines
        self._coordinator = coordinator
        self._io_for = io_for
        self._logger = logger
        self._worker_id = worker_id or _default_worker_id()
        self._clock_ms = clock_ms
        self._on_headline_settled = on_headline_settled
        self._outcomes = {name: dict.fromkeys(OUTCOME_COUNTERS, 0) for name in registry.names()}

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        job, stage = self._parse(message)
        # One timings line per job, like a record's: the stage's steps time themselves.
        timeline, token = begin_record(job.record_ids[0], f"stage:{job.stage}")
        timeline.mark(ADMITTED)
        outcome = "failed"
        try:
            await self._handle(job, stage)
            outcome = "ok"
        except StagePaused:
            outcome = "paused"
            raise
        except StageRetry:
            outcome = "retry"
            raise
        finally:
            end_record(timeline, token, outcome=outcome, logger=self._logger)
        # The consumer releases the job's permit on INDEXING_COMPLETE; nothing held a parse slot.
        data = PipelineEventData(record_id=job.job_id)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=data)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=data)

    async def on_message_abandoned(self, message: StreamMessage | None, *, reason: str, attempts: int) -> None:
        """``AbandonedMessageSink``: a dead-lettered job leaves its stage FAILED, never stranded. Never raises."""
        if message is None:
            self._logger.error("Discarded an unparseable stage message (%s)", reason)
            return
        try:
            job = StageJob.model_validate(message.payload)
        except ValidationError:
            self._logger.error("Discarded a stage message that is not a job (%s)", reason)
            return
        self._count(job.stage, "abandoned")
        try:
            state = await self._states.get(job.state_key)
            if state is None or isinstance(state.status, ForeignStatus) or state.status in _TERMINAL:
                return
            why = f"Stage job discarded after {attempts} attempt(s): {reason}"
            failed = await self._states.cas(
                job.state_key,
                expected=state.status,
                new=ProgressStatus.FAILED,
                patch=StageStatePatch(reason=why, finished_at_ms=self._clock_ms(), worker_id=None),
                # A newer run claimed since is not this message's to fail.
                expected_attempt=state.attempt,
            )
            if failed and self._registry.has(job.stage):
                headline = self._registry.get(job.stage).headline
                if headline is not None:
                    updated = await self._headlines.set_headline(
                        job.record_ids, headline, ProgressStatus.FAILED, rev=job.rev, reason=why
                    )
                    await self._headline_settled(job, updated, ProgressStatus.FAILED)
        except Exception:
            self._logger.exception("Could not mark abandoned stage job %s FAILED", job.job_id)

    def outcomes(self, stage: str) -> dict[str, int]:
        """How this process's deliveries of ``stage`` ended since it started."""
        return dict(self._outcomes.get(stage, {}))

    def _count(self, stage: str, outcome: str) -> None:
        counters = self._outcomes.get(stage)
        if counters is not None:
            counters[outcome] += 1

    def _parse(self, message: StreamMessage) -> tuple[StageJob, Stage[Any]]:
        try:
            job = StageJob.model_validate(message.payload)
        except ValidationError as exc:
            raise StageFailed(f"invalid stage job: {exc}") from exc
        if not self._registry.has(job.stage):
            raise StageFailed(f"no stage named {job.stage!r} runs here", record_id=job.job_id)
        return job, self._registry.get(job.stage)

    async def _handle(self, job: StageJob, stage: Stage[Any]) -> None:
        try:
            await self._handle_job(job, stage)
        except ConnectorBeingDeleted:
            # Nothing more is written: the delete removes the records and their stage states,
            # and a write here would make its batch lose a write conflict.
            self._logger.info(
                "Stage job %s belongs to connector %s, which is being deleted; holding it for %.0fs",
                job.job_id,
                job.connector_id,
                HELD_FOR_DELETE_DELAY_S,
            )
            self._count(job.stage, "paused")
            raise StageHeldForDelete(
                f"connector {job.connector_id} is being deleted", delay_s=HELD_FOR_DELETE_DELAY_S
            ) from None

    async def _handle_job(self, job: StageJob, stage: Stage[Any]) -> None:
        io = self._io_for(job, Deadline(stage.budget_s))
        connector_deleting = getattr(io, "connector_deleting", None)
        if connector_deleting is not None and await connector_deleting(job):
            # Before the state is claimed, so a held job has written nothing at all.
            raise ConnectorBeingDeleted(job.connector_id)
        state = await self._state_for(job, io)
        if state is None:
            self._logger.info(
                "Stage job %s has no state and none of its records is left on its revision; dropping it",
                job.job_id,
            )
            self._count(job.stage, "stale")
            return
        if isinstance(state.status, ForeignStatus):
            # Handing it back would pause this stage's consumer, and after a rollback no pod would ever take it.
            self._logger.info(
                "Stage job %s: its state is %s, %s; leaving it", job.job_id, state.status.value, FOREIGN_REASON
            )
            self._count(job.stage, "foreign")
            return
        status = state.status
        fingerprint = (await stage.fingerprint(job, io)).digest()

        # A fingerprint is stored only by a run that completed, and cleared when a run
        # starts, so an equal one means this revision's output is already current.
        if not job.force and state.fingerprint == fingerprint and await self._output_in_place(job, stage, io, state):
            await self._complete_unchanged(job, stage, state, status, fingerprint)
            self._count(job.stage, "unchanged")
            return
        if status in _SETTLED and not job.force:
            self._logger.info("Stage job %s is already %s; ignoring this delivery", job.job_id, status.value)
            self._count(job.stage, "duplicate")
            return
        prior, refusal = await self._budget_before_run(job, state)
        run_attempt = state.attempt + 1
        claimed = await self._states.cas(
            job.state_key,
            expected=status,
            new=ProgressStatus.IN_PROGRESS,
            patch=StageStatePatch(
                worker_id=self._worker_id,
                attempt=run_attempt,
                started_at_ms=self._clock_ms(),
                finished_at_ms=None,
                fingerprint=None,
                reason=None,
            ),
            expected_attempt=state.attempt,
        )
        if not claimed:
            raise StageRetry(f"stage job {job.job_id} changed state while it was being claimed")
        allowance = None if refusal is not None else await self._reserve_sends(job, stage, run_attempt)

        trace: JobTrace | None = None
        try:
            if refusal is not None:
                # The budget of this revision is spent: an automatic re-dispatch must not send anything.
                last = f" Last status: {state.reason}" if state.reason else ""
                result = StageResult(outcome=StageOutcome.FAILED, reason=f"{refusal[1]}{last}")
            else:
                with llm_job(job.state_key, allowance=allowance) as trace:
                    async with asyncio.timeout(stage.budget_s):
                        result = await stage.run(job, io)
        except TimeoutError:
            result = StageResult(outcome=StageOutcome.RETRY, reason=f"ran past its {stage.budget_s:.0f}s budget")
        except ConnectorBeingDeleted:
            await self._commit_sends(job, run_attempt, trace)
            raise
        except Exception as exc:
            await self._commit_sends(job, run_attempt, trace)
            # The consumer classifies it (retry or dead-letter); the reason stays for whoever looks next.
            self._count(job.stage, "error")
            if not await self._settle(job, ProgressStatus.QUEUED, attempt=run_attempt, reason=f"{type(exc).__name__}: {exc}"):
                # Another run owns the job now; this delivery has nothing left to report.
                return
            raise
        if result.outcome is not StageOutcome.PAUSED:
            # A PAUSED outcome is committed by its verdict; every other one commits what the run spent here.
            await self._commit_sends(job, run_attempt, trace)
        await self._apply(
            job, stage, result, fingerprint, attempt=run_attempt, was_paused=state.status is ProgressStatus.PAUSED
        )

    async def _budget_before_run(
        self, job: StageJob, state: StageState
    ) -> tuple[PauseState | None, tuple[Verdict, str] | None]:
        """The job's pause budget so far, and the refusal when it is spent; judged before the job is claimed.

        A budget that cannot be read defers the job (``StagePaused``, nothing written), whatever its status: an
        automatic redrive leaves a FAILED job QUEUED with its spent budget, so "not readable" is never "not spent". A
        forced run starts over (the coordinator reset its budget) and goes ahead; its calls pause while Redis is down."""
        if self._pause is None or not self._pause.available:
            return None, None
        try:
            prior = await self._pause.prior(job.state_key)
            refusal = None if job.force else await self._pause.spent(job, prior)
        except PauseStoreUnavailable as exc:
            if job.force:
                return None, None
            self._count(job.stage, "paused")
            raise StagePaused(
                f"the pause budget cannot be read ({exc}); holding stage job {job.job_id}",
                delay_s=compute_retry_backoff_seconds(max(state.attempt, 1)),
            ) from None
        return prior, refusal

    async def _state_for(self, job: StageJob, io: StageIO) -> StageState | None:
        """The job's state, recreated when its claim is missing; None when its records are gone.

        A delete removes the stage states with the records, so a job delivered after it would
        otherwise put back a state that nothing refers to and no later delete removes.
        """
        state = await self._states.get(job.state_key)
        if state is None:
            current_record_ids = getattr(io, "current_record_ids", None)
            if current_record_ids is not None and not await current_record_ids(job):
                return None
            # The claim is missing (a hand-published job, or a state that was cleaned up).
            _ = await self._states.create_if_absent(StageState.from_job(job, ProgressStatus.QUEUED, self._clock_ms()))
            state = await self._states.get(job.state_key)
        if state is None:
            raise StageRetry(f"no state could be created for stage job {job.job_id}")
        return state

    async def _output_in_place(self, job: StageJob, stage: Stage[Any], io: StageIO, state: StageState) -> bool:
        """Whether an unchanged job's output is still where readers find it, restoring a kept one.

        Classification lives in the stored record, which a re-index of unchanged content rewrites
        without it; the output kept with the state goes back without running the stage.
        """
        present = getattr(stage, "output_present", None)
        if present is None or await present(job, io):
            return True
        restore = getattr(stage, "restore_output", None)
        if state.output is None or restore is None:
            return False
        # A forced run claims the state before it saves, and saves under the same record lease, so
        # a restore that re-checks the state under that lease can never land after the run's output.
        return bool(await restore(job, io, state.output, still_current=lambda: self._state_unchanged(job, state)))

    async def _state_unchanged(self, job: StageJob, seen: StageState) -> bool:
        now = await self._states.get(job.state_key)
        return now is not None and (now.status, now.attempt, now.fingerprint, now.output) == (
            seen.status, seen.attempt, seen.fingerprint, seen.output
        )

    async def _complete_unchanged(
        self, job: StageJob, stage: Stage[Any], state: StageState, status: ProgressStatus, fingerprint: str
    ) -> None:
        # Also when it read COMPLETED: a run claimed since must not be reported complete on its behalf.
        # The attempt it read fences that: a forced redrive that claimed (and maybe finished) in the
        # meantime has moved it on, even when the status has come back round to COMPLETED.
        already = status is ProgressStatus.COMPLETED
        if not await self._states.cas(
            job.state_key,
            expected=status,
            new=ProgressStatus.COMPLETED,
            patch=StageStatePatch() if already else StageStatePatch(finished_at_ms=self._clock_ms(), reason=None),
            expected_attempt=state.attempt,
        ):
            raise StageRetry(f"stage job {job.job_id} changed state while completing")
        result = StageResult(
            outcome=StageOutcome.COMPLETED, fingerprint=fingerprint, reason="inputs unchanged", record_view=job.view
        )
        await self._clear_pause(job, state.attempt)
        await self._finish(job, stage, result)

    async def _apply(
        self, job: StageJob, stage: Stage[Any], result: StageResult, fingerprint: str, *, attempt: int,
        was_paused: bool = False,
    ) -> None:
        outcome = result.outcome
        self._count(job.stage, outcome.value)
        # A run that no longer owns its state (the sweeper reclaimed it and another worker
        # claimed it) reports nothing further: the owner settles, writes headlines and dispatches.
        if outcome is StageOutcome.COMPLETED:
            if not await self._settle(
                job, ProgressStatus.COMPLETED, attempt=attempt, fingerprint=fingerprint, output=result.output
            ):
                return
            await self._clear_pause(job, attempt)
            await self._finish(
                job, stage, result.model_copy(update={"fingerprint": fingerprint, "record_view": result.record_view or job.view})
            )
        elif outcome is StageOutcome.SKIPPED:
            if not await self._settle(job, ProgressStatus.SKIPPED, attempt=attempt, reason=result.reason):
                return
            await self._clear_pause(job, attempt)
            await self._finish(job, stage, result.model_copy(update={"record_view": result.record_view or job.view}))
        elif outcome is StageOutcome.STALE:
            # Terminal for this revision so the sweeper never revives it; nothing else is written.
            _ = await self._settle(job, ProgressStatus.SKIPPED, attempt=attempt, reason=result.reason or SUPERSEDED)
        elif outcome is StageOutcome.RETRY:
            if not await self._settle(job, ProgressStatus.QUEUED, attempt=attempt, reason=result.reason):
                return
            raise StageRetry(result.reason or f"stage {job.stage} asked to retry")
        elif outcome is StageOutcome.PAUSED:
            judgement = await self._pause.judge(job, result, attempt=attempt) if self._pause is not None else None
            if judgement is not None and judgement.decision.verdict.is_fail:
                # The budget is spent: a poison document, a lone job at the absolute cap, or the wall.
                reason = judgement.decision.reason or f"stage {job.stage} failed"
                await self._fail(job, stage, reason, attempt=attempt)
                return
            if not await self._settle(job, ProgressStatus.PAUSED, attempt=attempt, reason=result.reason):
                return
            if judgement is not None:
                await self._redrive_capped(judgement.scope, judgement.redrive, judgement.episode)
            # Backs off like a retry (15 s, 1 min, 4 min, then every 5 min) so a long outage costs
            # little, and never returns before the dependency said it might be back.
            delay = max(result.retry_after_s or 0.0, compute_retry_backoff_seconds(attempt))
            if judgement is not None and judgement.decision.verdict is Verdict.REDRIVE:
                delay += judgement.jitter_s
            elif self._pause is not None:
                delay += await self._pause.hold_jitter(result)
            # One WARNING per episode: a job that was already PAUSED and pauses again repeats what its first pause said.
            raise StagePaused(
                result.reason or f"stage {job.stage} is paused",
                delay_s=delay,
                log_level=logging.DEBUG if was_paused else logging.WARNING,
            )
        else:
            reason = result.reason or f"stage {job.stage} failed"
            await self._fail(job, stage, reason, attempt=attempt)

    async def _fail(self, job: StageJob, stage: Stage[Any], reason: str, *, attempt: int) -> None:
        if not await self._settle(job, ProgressStatus.FAILED, attempt=attempt, reason=reason):
            return
        if stage.headline is not None:
            updated = await self._headlines.set_headline(
                job.record_ids, stage.headline, ProgressStatus.FAILED, rev=job.rev, reason=reason
            )
            try:
                await self._headline_settled(job, updated, ProgressStatus.FAILED)
            except Exception:
                self._logger.exception("Stage job %s failed and its follow-up could not run", job.job_id)
        raise StageFailed(reason, record_id=job.job_id)

    async def _reserve_sends(self, job: StageJob, stage: Stage[Any], attempt: int) -> int | None:
        """Reserve the failed sends this run may make (what the absolute cap leaves after the committed sends and any
        twin run's reservation); None without a pause budget. When the store cannot be written the job is handed back
        (QUEUED, ``StagePaused``) before it sends anything; a forced run goes ahead unreserved, its calls pause on their
        own while Redis is down."""
        if self._pause is None or not self._pause.available:
            return None
        try:
            return await self._pause.reserve(job, attempt=attempt, stale_ms=int(2 * stage.budget_s * 1000))
        except PauseStoreUnavailable as exc:
            if job.force:
                return None
            await self._settle(job, ProgressStatus.QUEUED, attempt=attempt, reason="the pause budget cannot be read")
            self._count(job.stage, "paused")
            raise StagePaused(
                f"the pause budget cannot be written ({exc}); holding stage job {job.job_id}",
                delay_s=compute_retry_backoff_seconds(max(attempt, 1)),
            ) from None

    async def _commit_sends(self, job: StageJob, attempt: int, trace: JobTrace | None) -> None:
        """Commit what a run that did not pause spent (a RETRY, a failure, a timeout, a completion) and release its
        reservation, so the next run's allowance counts it. A commit that fails leaves the reservation to go stale,
        and a stale reservation counts as fully spent."""
        if self._pause is None or not self._pause.available:
            return
        await self._pause.commit(job, attempt=attempt, trace=trace)

    async def _clear_pause(self, job: StageJob, attempt: int) -> None:
        """A COMPLETED or SKIPPED revision starts its pause budget over, unless a later attempt has written it since."""
        if self._pause is not None:
            await self._pause.clear(job.state_key, attempt=attempt)

    async def _redrive_capped(self, scope: str, state_keys: Sequence[str], episode: str = "") -> None:
        """Jobs the charged cap failed in the window of a key hold: the key was the problem, not the document. Each is
        owed a redrive until it is done (``_redrive_one``); what this pass cannot do, the recovery leader retries."""
        if self._pause is None:
            return
        for key in state_keys:
            if await self._redrive_one(key, episode):
                try:
                    await self._pause.redrive_done(scope, key)
                except PauseStoreUnavailable:
                    self._logger.debug("could not mark the hold redrive of %s done; the next pass finds it done", key)

    async def retry_hold_redrives(self) -> int:
        """The recovery leader's pass over the redrives provider holds still owe. Returns how many were completed."""
        if self._pause is None or not self._pause.available:
            return 0
        if self._redrive_passes % RECONCILE_EVERY_TICKS == 0:
            # The scope index is a fast path; the owed sets themselves are the record (``PauseStore.reconcile_owed``).
            try:
                await self._pause.reconcile_owed()
            except PauseStoreUnavailable:
                self._logger.warning("could not reconcile the owed redrive index; using the index as it is", exc_info=True)
        self._redrive_passes += 1
        done = 0
        for scope, owed in (await self._pause.pending_redrives()).items():
            for key, episode in owed:
                if await self._redrive_one(key, episode):
                    await self._pause.redrive_done(scope, key)
                    done += 1
        return done

    async def _redrive_one(self, key: str, episode: str = "") -> bool:
        """Redrive one job a hold owes: a fresh charge count first, then a redrive that is not forced, so its pause budget
        (sends, wall) carries on. True when nothing more is owed: done, or the job no longer needs it. A job still running
        may yet settle FAILED by its charged cap, and a reset or redrive that fails is tried again, so those stay owed."""
        if self._pause is None:
            return True
        try:
            other = await self._states.get(key)
            if other is None or not other.org_id:
                return True
            if other.status is ProgressStatus.IN_PROGRESS:
                return False
            if other.status in (ProgressStatus.QUEUED, ProgressStatus.PAUSED):
                # It will run again: it must not meet its old charge count at the pre-run check. Once per episode,
                # so a pass repeated after its redrive went out leaves the charges a new run has made.
                await self._pause.release_charged(key, episode)
                return True
            if other.status is not ProgressStatus.FAILED:
                return True
            # Still owed means the redrive has not gone out (a refused publish, or a crash after the reset), so it is
            # sent even when this episode's reset was already done. A run that has since reached its cap on its own is
            # refused by the pre-run check before it sends, so a redrive here never buys it more sends.
            await self._pause.release_charged(key, episode)
            with for_org(other.org_id, job="stage_hold_redrive"):
                await self._coordinator.redrive(other.to_job().view, other.stage, priority=other.priority, force=False)
            return True
        except Exception:
            self._logger.warning("could not redrive stage job %s after a provider hold; it is retried", key, exc_info=True)
            return False

    async def _finish(self, job: StageJob, stage: Stage[Any], result: StageResult) -> None:
        if stage.headline is not None:
            status = ProgressStatus.COMPLETED if result.outcome is StageOutcome.COMPLETED else ProgressStatus.SKIPPED
            updated = await self._headlines.set_headline(
                job.record_ids,
                stage.headline,
                status,
                rev=job.rev,
                reason=result.reason if status is ProgressStatus.SKIPPED else None,
                clear_reason=status is ProgressStatus.COMPLETED,
            )
            if not updated:
                self._logger.info(
                    "Stage job %s finished, but no bound record is still on revision %s; not dispatching successors",
                    job.job_id,
                    job.rev,
                )
                return
            await self._headline_settled(job, updated, status)
        _ = await self._coordinator.on_stage_done(job, result)

    async def _headline_settled(self, job: StageJob, record_ids: Sequence[str], status: ProgressStatus) -> None:
        if self._on_headline_settled is not None and record_ids:
            await self._on_headline_settled(job, record_ids, status)

    async def _settle(
        self,
        job: StageJob,
        status: ProgressStatus,
        *,
        attempt: int,
        fingerprint: str | None = None,
        reason: str | None = None,
        output: str | None = None,
    ) -> bool:
        """Move this run's state (claimed as ``attempt``) out of IN_PROGRESS; whether it still owned it.

        If the sweeper reclaimed it meanwhile (it took this worker for dead) and nobody claimed it
        since, settle from wherever it is unless that is terminal. Once another run has claimed it,
        the state is that run's: this one writes nothing."""
        terminal = status in _TERMINAL
        patch = (
            StageStatePatch(
                fingerprint=fingerprint, output=output, reason=reason, finished_at_ms=self._clock_ms(), worker_id=None
            )
            if terminal
            else StageStatePatch(reason=reason, worker_id=None)
        )
        if await self._states.cas(
            job.state_key, expected=ProgressStatus.IN_PROGRESS, new=status, patch=patch, expected_attempt=attempt
        ):
            return True
        current = await self._states.get(job.state_key)
        if (
            current is not None
            and not isinstance(current.status, ForeignStatus)
            and current.status not in _TERMINAL
            and current.attempt == attempt
            and await self._states.cas(
                job.state_key, expected=current.status, new=status, patch=patch, expected_attempt=attempt
            )
        ):
            return True
        self._logger.warning(
            "Stage job %s (attempt %d) could not record %s; its state is now %s (attempt %s)",
            job.job_id,
            attempt,
            status.value,
            current.status.value if current else "missing",
            current.attempt if current else "-",
        )
        self._count(job.stage, "duplicate")
        return False
