"""The stage SDK: what a stage implements, and the only I/O it may touch."""

import time
from collections.abc import Callable, Sequence
from typing import ClassVar, Protocol, TypeVar

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.models import (
    HeadlineField,
    RecordView,
    StageFingerprint,
    StageJob,
    StageResult,
    StageState,
    StageStatePatch,
    Workload,
)
from app.modules.pipeline.policy import PipelinePolicy


class StageBudgetExceeded(Exception):
    """The stage ran past its time budget."""


class Deadline:
    """Remaining time budget for one stage run."""

    def __init__(self, budget_s: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._clock = clock
        self._expires_at = clock() + budget_s

    def remaining(self) -> float:
        return max(0.0, self._expires_at - self._clock())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise StageBudgetExceeded("stage budget exhausted")


class StageStateStore(Protocol):
    async def get(self, key: str) -> StageState | None: ...

    async def get_many(self, virtual_record_id: str, rev: str) -> dict[str, StageState]:
        """States of one revision, keyed by stage name."""
        ...

    async def create_if_absent(self, state: StageState) -> bool:
        """Insert on a unique key; False when the key already exists."""
        ...

    async def cas(
        self,
        key: str,
        *,
        expected: ProgressStatus,
        new: ProgressStatus,
        patch: StageStatePatch | None = None,
    ) -> bool:
        """Move ``key`` from ``expected`` to ``new`` in one statement; False when it was not ``expected``.

        Every successful transition, including ``expected == new``, also sets ``updatedAtMs`` to now.
        """
        ...

    async def stale(
        self, *, statuses: Sequence[ProgressStatus], updated_before_ms: int, limit: int
    ) -> list[StageState]:
        """States in ``statuses`` untouched since ``updated_before_ms``, oldest first."""
        ...


class HeadlineStatusWriter(Protocol):
    """Record-node status writes. Held by the worker host, never by a stage."""

    async def set_headline(
        self,
        record_ids: Sequence[str],
        field: HeadlineField,
        status: ProgressStatus,
        *,
        rev: str,
        reason: str | None = None,
        clear_reason: bool = False,
    ) -> list[str]:
        """Write ``field`` on records whose ``contentRev`` is ``rev``; returns the ids updated.

        ``reason`` is written when given; ``clear_reason`` blanks a stale one instead.
        """
        ...


class StageIO(Protocol):
    """Everything a stage may read or write. Concrete implementations are built by the DI container."""

    @property
    def policy(self) -> PipelinePolicy: ...

    @property
    def deadline(self) -> Deadline: ...


IOT_contra = TypeVar("IOT_contra", bound=StageIO, contravariant=True)


class Stage(Protocol[IOT_contra]):
    """A unit of pipeline work. ``IOT_contra`` is the IO protocol it needs beyond ``StageIO``."""

    name: ClassVar[str]
    version: ClassVar[int]
    requires: ClassVar[frozenset[str]]
    workload: ClassVar[Workload]
    # The record status this stage owns, if any.
    headline: ClassVar[HeadlineField | None]
    budget_s: ClassVar[float]

    def applies(self, view: RecordView, policy: PipelinePolicy) -> bool: ...

    async def fingerprint(self, job: StageJob, io: IOT_contra) -> StageFingerprint: ...

    async def run(self, job: StageJob, io: IOT_contra) -> StageResult: ...
