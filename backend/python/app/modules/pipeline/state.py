"""Stage states and headline statuses in the graph, through ``IGraphDBProvider``."""

import time
from collections.abc import Callable, Sequence
from typing import Any

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.models import (
    HeadlineField,
    StageState,
    StageStatePatch,
    StageStateSummary,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# The record timestamp that moves with each headline field.
_HEADLINE_TIMESTAMP: dict[HeadlineField, str | None] = {
    HeadlineField.PARSING: None,
    HeadlineField.INDEXING: "lastIndexTimestamp",
    HeadlineField.EXTRACTION: "lastExtractionTimestamp",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_document(state: StageState) -> dict[str, Any]:
    document = state.model_dump(mode="json", by_alias=True)
    document["id"] = document.pop("key")
    return document


def _from_document(document: dict[str, Any]) -> StageState:
    data = dict(document)
    data["key"] = data.pop("id")
    return StageState.model_validate(data)


class GraphStageStateStore:
    """``StageStateStore`` backed by the graph's ``stageStates`` collection."""

    def __init__(self, graph: IGraphDBProvider, *, clock_ms: Callable[[], int] = _now_ms) -> None:
        super().__init__()
        self._graph = graph
        self._clock_ms = clock_ms

    async def get(self, key: str) -> StageState | None:
        document = await self._graph.stage_state_get(key)
        return _from_document(document) if document is not None else None

    async def get_many(self, virtual_record_id: str, rev: str) -> dict[str, StageState]:
        documents = await self._graph.stage_states_for_revision(virtual_record_id, rev)
        return {state.stage: state for state in map(_from_document, documents)}

    async def create_if_absent(self, state: StageState) -> bool:
        return await self._graph.stage_state_create(_to_document(state))

    async def cas(
        self,
        key: str,
        *,
        expected: ProgressStatus,
        new: ProgressStatus,
        patch: StageStatePatch | None = None,
    ) -> bool:
        fields = patch.fields() if patch is not None else {}
        fields["updatedAtMs"] = self._clock_ms()
        return await self._graph.stage_state_compare_and_set(key, expected.value, new.value, fields)

    async def stale(
        self, *, statuses: Sequence[ProgressStatus], updated_before_ms: int, limit: int
    ) -> list[StageState]:
        documents = await self._graph.stage_states_stale(
            [status.value for status in statuses], updated_before_ms, limit
        )
        return [_from_document(document) for document in documents]


class GraphHeadlineStatusWriter:
    """``HeadlineStatusWriter`` that writes record statuses only on the current content revision."""

    def __init__(self, graph: IGraphDBProvider, *, clock_ms: Callable[[], int] = _now_ms) -> None:
        super().__init__()
        self._graph = graph
        self._clock_ms = clock_ms

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
        fields: dict[str, Any] = {field.value: status.value}
        timestamp_field = _HEADLINE_TIMESTAMP[field]
        if timestamp_field is not None:
            fields[timestamp_field] = self._clock_ms()
        if reason is not None:
            fields["reason"] = reason
        elif clear_reason:
            fields["reason"] = None
        return await self._graph.compare_and_set_record_fields(list(record_ids), rev, fields)


async def stage_summaries(graph: IGraphDBProvider, virtual_record_id: str, rev: str) -> list[StageStateSummary]:
    """Every stage's state for one revision, in the order they last changed."""
    states = await GraphStageStateStore(graph).get_many(virtual_record_id, rev)
    return [StageStateSummary.of(s) for s in sorted(states.values(), key=lambda s: (s.updated_at_ms, s.stage))]
