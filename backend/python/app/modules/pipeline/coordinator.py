"""Decides what runs next. A library the stage worker and the ingress path call, not a service.

Every dispatch is claimed in the state store before it is published: a unique insert for
a new ``(revision, stage)``, or a single-statement CAS from a terminal status to QUEUED.
So concurrent upstream completions dispatch a stage exactly once, and re-running an
upstream re-evaluates its successors, whose fingerprint check decides whether any work
happens. A claim whose publish fails is released so the caller's retry claims it again;
if even the release fails, the state stays QUEUED and the sweeper publishes it.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.models import (
    Priority,
    RecordView,
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
    StageStatePatch,
)
from app.modules.pipeline.policy import PipelinePolicy
from app.modules.pipeline.publisher import PublisherNotBoundError
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import HeadlineStatusWriter, Stage, StageStateStore

STAGE_JOB_EVENT = "stageJob"
# A published job still QUEUED after this long may have been lost by the broker (a trimmed stream).
REPUBLISH_BACKSTOP_MS = 6 * 3600 * 1000

_DONE = frozenset({ProgressStatus.COMPLETED, ProgressStatus.SKIPPED})
# A state in one of these may be claimed (moved to QUEUED) by a later dispatch or a redrive.
_CLAIMABLE = frozenset(
    {
        ProgressStatus.NOT_STARTED,
        ProgressStatus.COMPLETED,
        ProgressStatus.SKIPPED,
        ProgressStatus.FAILED,
        ProgressStatus.PAUSED,
    }
)


class EventPublisher(Protocol):
    async def send_event(
        self, topic: str, event_type: str, payload: dict[str, Any], key: str | None = None
    ) -> bool: ...


class StagePublishError(Exception):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"broker did not accept stage job {job_id}")
        self.job_id = job_id


@dataclass(frozen=True)
class SweepReport:
    scanned: int
    republished: int
    reclaimed: int


def _now_ms() -> int:
    return int(time.time() * 1000)


class Coordinator:
    def __init__(
        self,
        registry: StageRegistry,
        states: StageStateStore,
        headlines: HeadlineStatusWriter,
        producer: EventPublisher,
        *,
        policy_for: Callable[[str], PipelinePolicy],
        logger: logging.Logger,
        clock_ms: Callable[[], int] = _now_ms,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._states = states
        self._headlines = headlines
        self._producer = producer
        self._policy_for = policy_for
        self._logger = logger
        self._clock_ms = clock_ms

    async def on_external_done(
        self,
        stage: str,
        view: RecordView,
        *,
        priority: Priority,
        trigger: str,
        force: bool = False,
        keep_claim_on_publish_failure: bool = False,
    ) -> list[str]:
        """A prerequisite produced outside the stage runtime finished for this revision.

        ``keep_claim_on_publish_failure`` is for callers that cannot cheaply retry (the
        record is already searchable): a job the broker refused stays QUEUED for the
        sweeper instead of failing the caller.
        """
        if not self._registry.is_external(stage):
            raise ValueError(f"{stage!r} is not an external prerequisite")
        job = self._job_for(stage, 1, view, priority=priority, trigger=trigger, force=force)
        state = StageState.from_job(job, ProgressStatus.COMPLETED, self._clock_ms())
        if not await self._states.create_if_absent(state):
            existing = await self._states.get(state.key)
            if existing is not None and existing.status not in _DONE:
                _ = await self._states.cas(
                    state.key,
                    expected=existing.status,
                    new=ProgressStatus.COMPLETED,
                    patch=StageStatePatch.dispatch(job),
                )
        return await self._dispatch_after(
            stage, view, priority=priority, force=force, keep_claim=keep_claim_on_publish_failure
        )

    async def on_stage_done(self, job: StageJob, result: StageResult) -> list[str]:
        """Called by the worker after the stage's own state is terminal and before it ACKs."""
        if result.outcome not in (StageOutcome.COMPLETED, StageOutcome.SKIPPED):
            return []
        if result.record_view is None:
            raise ValueError(f"stage {job.stage!r} finished without a record view; successors cannot be evaluated")
        return await self._dispatch_after(job.stage, result.record_view, priority=job.priority, force=job.force)

    async def redrive(self, view: RecordView, stage: str, *, priority: Priority, force: bool) -> str | None:
        """Re-dispatch one stage for one revision; None when it is already queued or running."""
        target = self._registry.get(stage)
        job = self._job_for(target.name, target.version, view, priority=priority, trigger="redrive", force=force)
        claimed_from = await self._claim(job)
        if claimed_from is None:
            return None
        await self._publish(job, release_to=claimed_from)
        if target.headline is not None:
            _ = await self._headlines.set_headline(view.record_ids, target.headline, ProgressStatus.QUEUED, rev=view.rev)
        return job.job_id

    async def redrive_revision(
        self, virtual_record_id: str, rev: str, stages: Sequence[str], *, priority: Priority, force: bool
    ) -> list[str] | None:
        """Re-dispatch ``stages`` for a revision the runtime has seen, from what its external
        prerequisite recorded. None when it has not seen the revision: the caller reindexes
        instead, which dispatches every stage."""
        states = await self._states.get_many(virtual_record_id, rev)
        origin = next(
            (s for s in states.values() if self._registry.is_external(s.stage) and s.status in _DONE), None
        )
        if origin is None:
            return None
        view = origin.to_job().view
        dispatched: list[str] = []
        for stage in stages:
            if not self._registry.has(stage):
                self._logger.warning("Cannot re-run unknown stage %r for %s", stage, virtual_record_id)
                continue
            job_id = await self.redrive(view, stage, priority=priority, force=force)
            if job_id is not None:
                dispatched.append(job_id)
        return dispatched

    async def sweep(
        self,
        *,
        queued_older_than_ms: int,
        limit: int,
        republish_after_ms: int = REPUBLISH_BACKSTOP_MS,
        in_progress_older_than_ms: int | None = None,
        is_job_active: Callable[[str], Awaitable[bool]] | None = None,
    ) -> SweepReport:
        """Re-publish claims that never reached the broker or paused jobs whose message was lost,
        and reclaim jobs whose worker is gone.

        Callers run this under the cluster-wide recovery lease, so one sweeper acts at a time.
        """
        now = self._clock_ms()
        scanned = republished = reclaimed = 0
        queued = await self._states.stale(
            statuses=(ProgressStatus.QUEUED,), updated_before_ms=now - queued_older_than_ms, limit=limit
        )
        for state in queued:
            scanned += 1
            if not self._registry.has(state.stage):
                continue
            if state.published_at_ms is not None and now - state.published_at_ms < republish_after_ms:
                # On the broker, waiting its turn behind the backlog.
                continue
            job = state.to_job()
            if await self._send(job):
                await self._mark_published(job)
                republished += 1
        # A paused job rewrites its state every time it comes back (at least every few minutes),
        # so one untouched for the republish backstop has lost its message.
        paused = await self._states.stale(
            statuses=(ProgressStatus.PAUSED,), updated_before_ms=now - republish_after_ms, limit=limit
        )
        for state in paused:
            scanned += 1
            if not self._registry.has(state.stage):
                continue
            job = state.to_job()
            if await self._states.cas(state.key, expected=ProgressStatus.PAUSED, new=ProgressStatus.QUEUED) and (
                await self._send(job)
            ):
                await self._mark_published(job)
                republished += 1
        if in_progress_older_than_ms is not None and is_job_active is not None:
            running = await self._states.stale(
                statuses=(ProgressStatus.IN_PROGRESS,),
                updated_before_ms=now - in_progress_older_than_ms,
                limit=limit,
            )
            for state in running:
                scanned += 1
                job = state.to_job()
                if not self._registry.has(state.stage) or await is_job_active(job.job_id):
                    continue
                reclaimed_state = await self._states.cas(
                    state.key,
                    expected=ProgressStatus.IN_PROGRESS,
                    new=ProgressStatus.QUEUED,
                    patch=StageStatePatch(worker_id=None, reason="re-dispatched: worker lost"),
                )
                if reclaimed_state and await self._send(job):
                    await self._mark_published(job)
                    reclaimed += 1
        return SweepReport(scanned=scanned, republished=republished, reclaimed=reclaimed)

    async def _dispatch_after(
        self, upstream: str, view: RecordView, *, priority: Priority, force: bool, keep_claim: bool = False
    ) -> list[str]:
        policy = self._policy_for(view.org_id)
        published: list[str] = []
        frontier = [upstream]
        evaluated: set[str] = set()
        while frontier:
            done = frontier.pop(0)
            for stage in self._registry.successors(done):
                if stage.name in evaluated:
                    continue
                states = await self._states.get_many(view.virtual_record_id, view.rev)
                if not all(req in states and states[req].status in _DONE for req in stage.requires):
                    # The last prerequisite to finish dispatches it.
                    continue
                evaluated.add(stage.name)
                if not stage.applies(view, policy):
                    await self._skip(stage, view, priority=priority, trigger=done)
                    frontier.append(stage.name)
                    continue
                job = self._job_for(stage.name, stage.version, view, priority=priority, trigger=done, force=force)
                claimed_from = await self._claim(job)
                if claimed_from is not None:
                    await self._publish(job, release_to=None if keep_claim else claimed_from)
                    published.append(job.job_id)
                    if stage.headline is not None:
                        _ = await self._headlines.set_headline(
                            view.record_ids, stage.headline, ProgressStatus.QUEUED, rev=view.rev
                        )
        return published

    async def _skip(self, stage: Stage[Any], view: RecordView, *, priority: Priority, trigger: str) -> None:
        job = self._job_for(stage.name, stage.version, view, priority=priority, trigger=trigger, force=False)
        reason = "not applicable to this record"
        # A stage that already produced output for this revision keeps it.
        if await self._states.create_if_absent(
            StageState.from_job(job, ProgressStatus.SKIPPED, self._clock_ms(), reason=reason)
        ) and stage.headline is not None:
            _ = await self._headlines.set_headline(
                view.record_ids, stage.headline, ProgressStatus.SKIPPED, rev=view.rev, reason=reason
            )

    async def _claim(self, job: StageJob) -> ProgressStatus | None:
        """Claim ``job``'s state for dispatch; returns the status it was claimed from, or None."""
        if await self._states.create_if_absent(StageState.from_job(job, ProgressStatus.QUEUED, self._clock_ms())):
            return ProgressStatus.NOT_STARTED
        existing = await self._states.get(job.state_key)
        if existing is None or existing.status not in _CLAIMABLE:
            return None
        if await self._states.cas(
            job.state_key,
            expected=existing.status,
            new=ProgressStatus.QUEUED,
            patch=StageStatePatch.dispatch(job),
        ):
            return existing.status
        return None

    async def _publish(self, job: StageJob, *, release_to: ProgressStatus | None) -> None:
        """Publish a claimed job. If the broker refuses it, release the claim to ``release_to``
        and raise for the caller's retry, or with ``release_to=None`` keep it for the sweeper."""
        if await self._send(job):
            await self._mark_published(job)
            return
        if release_to is None:
            self._logger.warning("Stage job %s is claimed but not published; the sweeper will publish it", job.job_id)
            return
        released = False
        try:
            released = await self._states.cas(job.state_key, expected=ProgressStatus.QUEUED, new=release_to)
        except Exception:  # The sweeper covers an unreleased claim.
            self._logger.exception("Could not release the claim on stage job %s", job.job_id)
        if not released:
            self._logger.warning("Stage job %s stays QUEUED unpublished; the sweeper will publish it", job.job_id)
        raise StagePublishError(job.job_id)

    async def _mark_published(self, job: StageJob) -> None:
        # Lets the sweeper tell a job waiting on the broker from a claim that never reached it.
        # A worker may already have moved the state on, which is fine.
        _ = await self._states.cas(
            job.state_key,
            expected=ProgressStatus.QUEUED,
            new=ProgressStatus.QUEUED,
            patch=StageStatePatch(published_at_ms=self._clock_ms()),
        )

    async def _send(self, job: StageJob) -> bool:
        try:
            return await self._producer.send_event(
                self._registry.topic_for(job.stage),
                STAGE_JOB_EVENT,
                job.model_dump(mode="json", by_alias=True),
                key=job.connector_id,
            )
        except PublisherNotBoundError:  # No stage topics yet; the indexing service logs why.
            return False
        except Exception:  # Every broker failure means "not published".
            self._logger.exception("Publishing stage job %s failed", job.job_id)
            return False

    @staticmethod
    def _job_for(
        stage: str, version: int, view: RecordView, *, priority: Priority, trigger: str, force: bool
    ) -> StageJob:
        return StageJob.for_view(
            view, stage=stage, stage_version=version, priority=priority, trigger=trigger, force=force
        )
