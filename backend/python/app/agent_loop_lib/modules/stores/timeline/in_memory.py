from __future__ import annotations

from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry, TimelineStore


class InMemoryTimelineStore(TimelineStore):
    def __init__(self) -> None:
        self._entries: list[TimelineEntry] = []

    async def append(self, entry: TimelineEntry) -> None:
        self._entries.append(entry)

    async def get_by_trace(self, trace_id: str) -> list[TimelineEntry]:
        return sorted(
            [e for e in self._entries if e.trace_id == trace_id],
            key=lambda e: e.sequence_id,
        )

    async def get_by_run(self, run_id: str) -> list[TimelineEntry]:
        return sorted(
            [e for e in self._entries if e.run_id == run_id],
            key=lambda e: e.sequence_id,
        )

    async def clear(self, trace_id: str) -> None:
        self._entries = [e for e in self._entries if e.trace_id != trace_id]
