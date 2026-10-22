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
from app.exceptions.indexing_exceptions import ProcessingError
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.models import (
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
    StageStatePatch,
)
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import (
    Deadline,
    HeadlineStatusWriter,
    Stage,
    StageIO,
    StageStateStore,
)
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
    ) -> None:
        super().__init__()
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
            if state is None or state.status in _TERMINAL:
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
        io = self._io_for(job, Deadline(stage.budget_s))
        state = await self._state_for(job, io)
        if state is None:
            self._logger.info(
                "Stage job %s has no state and none of its records is left on its revision; dropping it",
                job.job_id,
            )
            self._count(job.stage, "stale")
            return
        fingerprint = (await stage.fingerprint(job, io)).digest()

        # A fingerprint is stored only by a run that completed, and cleared when a run
        # starts, so an equal one means this revision's output is already current.
        if not job.force and state.fingerprint == fingerprint and await self._output_in_place(job, stage, io, state):
            await self._complete_unchanged(job, stage, state, fingerprint)
            self._count(job.stage, "unchanged")
            return
        if state.status in _SETTLED and not job.force:
            self._logger.info("Stage job %s is already %s; ignoring this delivery", job.job_id, state.status.value)
            self._count(job.stage, "duplicate")
            return
        run_attempt = state.attempt + 1
        claimed = await self._states.cas(
            job.state_key,
            expected=state.status,
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

        try:
            async with asyncio.timeout(stage.budget_s):
                result = await stage.run(job, io)
        except TimeoutError:
            result = StageResult(outcome=StageOutcome.RETRY, reason=f"ran past its {stage.budget_s:.0f}s budget")
        except Exception as exc:
            # The consumer classifies it (retry or dead-letter); the reason stays for whoever looks next.
            self._count(job.stage, "error")
            if not await self._settle(job, ProgressStatus.QUEUED, attempt=run_attempt, reason=f"{type(exc).__name__}: {exc}"):
                # Another run owns the job now; this delivery has nothing left to report.
                return
            raise
        await self._apply(job, stage, result, fingerprint, attempt=run_attempt)

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

    async def _complete_unchanged(self, job: StageJob, stage: Stage[Any], state: StageState, fingerprint: str) -> None:
        # Also when it read COMPLETED: a run claimed since must not be reported complete on its behalf.
        # The attempt it read fences that: a forced redrive that claimed (and maybe finished) in the
        # meantime has moved it on, even when the status has come back round to COMPLETED.
        already = state.status is ProgressStatus.COMPLETED
        if not await self._states.cas(
            job.state_key,
            expected=state.status,
            new=ProgressStatus.COMPLETED,
            patch=StageStatePatch() if already else StageStatePatch(finished_at_ms=self._clock_ms(), reason=None),
            expected_attempt=state.attempt,
        ):
            raise StageRetry(f"stage job {job.job_id} changed state while completing")
        result = StageResult(
            outcome=StageOutcome.COMPLETED, fingerprint=fingerprint, reason="inputs unchanged", record_view=job.view
        )
        await self._finish(job, stage, result)

    async def _apply(
        self, job: StageJob, stage: Stage[Any], result: StageResult, fingerprint: str, *, attempt: int
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
            await self._finish(
                job, stage, result.model_copy(update={"fingerprint": fingerprint, "record_view": result.record_view or job.view})
            )
        elif outcome is StageOutcome.SKIPPED:
            if not await self._settle(job, ProgressStatus.SKIPPED, attempt=attempt, reason=result.reason):
                return
            await self._finish(job, stage, result.model_copy(update={"record_view": result.record_view or job.view}))
        elif outcome is StageOutcome.STALE:
            # Terminal for this revision so the sweeper never revives it; nothing else is written.
            _ = await self._settle(job, ProgressStatus.SKIPPED, attempt=attempt, reason=result.reason or SUPERSEDED)
        elif outcome is StageOutcome.RETRY:
            if not await self._settle(job, ProgressStatus.QUEUED, attempt=attempt, reason=result.reason):
                return
            raise StageRetry(result.reason or f"stage {job.stage} asked to retry")
        elif outcome is StageOutcome.PAUSED:
            if not await self._settle(job, ProgressStatus.PAUSED, attempt=attempt, reason=result.reason):
                return
            # Backs off like a retry (15 s, 1 min, 4 min, then every 5 min) so a long outage costs
            # little, and never returns before the dependency said it might be back.
            delay = max(result.retry_after_s or 0.0, compute_retry_backoff_seconds(attempt))
            raise StagePaused(result.reason or f"stage {job.stage} is paused", delay_s=delay)
        else:
            reason = result.reason or f"stage {job.stage} failed"
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
