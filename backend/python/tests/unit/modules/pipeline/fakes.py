"""In-memory stand-ins for the pipeline's storage and broker seams.

Every method awaits once before its check-and-set, like a real round trip, so concurrent
callers interleave; the check-and-set itself has no await inside, which is what makes it
atomic on one event loop — the same guarantee a single-statement graph CAS gives.
"""

import asyncio
from collections.abc import Sequence

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.models import HeadlineField, StageState, StageStatePatch


class Clock:
    def __init__(self, now_ms: int = 1_000_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms


class InMemoryStageStateStore:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.states: dict[str, StageState] = {}
        self.fail_cas = False

    def put(self, state: StageState) -> None:
        self.states[state.key] = state

    async def get(self, key: str) -> StageState | None:
        await asyncio.sleep(0)
        return self.states.get(key)

    async def get_many(self, virtual_record_id: str, rev: str) -> dict[str, StageState]:
        await asyncio.sleep(0)
        return {s.stage: s for s in self.states.values() if s.virtual_record_id == virtual_record_id and s.rev == rev}

    async def create_if_absent(self, state: StageState) -> bool:
        await asyncio.sleep(0)
        if state.key in self.states:
            return False
        self.states[state.key] = state
        return True

    async def cas(
        self,
        key: str,
        *,
        expected: ProgressStatus,
        new: ProgressStatus,
        patch: StageStatePatch | None = None,
    ) -> bool:
        await asyncio.sleep(0)
        if self.fail_cas:
            raise ConnectionError("graph unavailable")
        current = self.states.get(key)
        if current is None or current.status is not expected:
            return False
        update: dict[str, object] = {"status": new, "updated_at_ms": self._clock()}
        if patch is not None:
            update.update(patch.model_dump(exclude_unset=True))
        self.states[key] = current.model_copy(update=update)
        return True

    async def stale(
        self, *, statuses: Sequence[ProgressStatus], updated_before_ms: int, limit: int
    ) -> list[StageState]:
        await asyncio.sleep(0)
        matching = [s for s in self.states.values() if s.status in statuses and s.updated_at_ms < updated_before_ms]
        return sorted(matching, key=lambda s: s.updated_at_ms)[:limit]


class RecordingHeadlines:
    def __init__(self) -> None:
        self.writes: list[tuple[tuple[str, ...], HeadlineField, ProgressStatus, str, str | None]] = []
        self.cleared: list[bool] = []
        # Every bound record moved to a newer revision: no write lands.
        self.moved_on = False

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
        await asyncio.sleep(0)
        self.writes.append((tuple(record_ids), field, status, rev, reason))
        self.cleared.append(clear_reason)
        return [] if self.moved_on else list(record_ids)


class RecordingProducer:
    def __init__(self, *, reject_next: int = 0) -> None:
        self.sent: list[tuple[str, str, dict[str, object], str | None]] = []
        self.reject_next = reject_next

    async def send_event(
        self, topic: str, event_type: str, payload: dict[str, object], key: str | None = None
    ) -> bool:
        await asyncio.sleep(0)
        if self.reject_next:
            self.reject_next -= 1
            return False
        self.sent.append((topic, event_type, payload, key))
        return True

    def jobs(self, topic: str | None = None) -> list[dict[str, object]]:
        return [payload for t, _, payload, _ in self.sent if topic is None or t == topic]
