"""Graph-backed stage state store and headline writer: model <-> document mapping and timestamps."""

from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.models import (
    HeadlineField,
    Priority,
    StageJob,
    StageState,
    StageStatePatch,
)
from app.modules.pipeline.state import GraphHeadlineStatusWriter, GraphStageStateStore
from app.services.resource_governor.models import ParseTier


def _state(status: ProgressStatus = ProgressStatus.QUEUED) -> StageState:
    job = StageJob(
        stage="classify", stage_version=1, org_id="org-1", virtual_record_id="vr-1", rev="rev-a",
        record_ids=("rec-1",), connector_id="conn-1", tier=ParseTier.HEAVY, priority=Priority.BULK, trigger="embed",
        text_digest="t", blocks_digest="b", text_chars=10, has_tables=False, has_images=False,
    )
    return StageState.from_job(job, status, now_ms=5)


def _document(state: StageState) -> dict[str, object]:
    document = state.model_dump(mode="json", by_alias=True)
    document["id"] = document.pop("key")
    return document


@pytest.mark.asyncio
async def test_create_stores_the_key_as_id_in_storage_form() -> None:
    graph = AsyncMock()
    graph.stage_state_create = AsyncMock(return_value=True)
    assert await GraphStageStateStore(graph).create_if_absent(_state())
    document = graph.stage_state_create.await_args.args[0]
    assert document["id"] == "vr-1:rev-a:classify"
    assert "key" not in document
    assert document["recordIds"] == ["rec-1"] and document["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_reads_rebuild_the_model() -> None:
    graph = AsyncMock()
    graph.stage_state_get = AsyncMock(return_value=_document(_state(ProgressStatus.COMPLETED)))
    graph.stage_states_for_revision = AsyncMock(return_value=[_document(_state())])
    store = GraphStageStateStore(graph)
    assert await store.get("vr-1:rev-a:classify") == _state(ProgressStatus.COMPLETED)
    assert list(await store.get_many("vr-1", "rev-a")) == ["classify"]


@pytest.mark.asyncio
async def test_a_missing_state_is_none() -> None:
    graph = AsyncMock()
    graph.stage_state_get = AsyncMock(return_value=None)
    assert await GraphStageStateStore(graph).get("k") is None


@pytest.mark.asyncio
async def test_cas_writes_the_patch_and_bumps_updated_at() -> None:
    graph = AsyncMock()
    graph.stage_state_compare_and_set = AsyncMock(return_value=True)
    store = GraphStageStateStore(graph, clock_ms=lambda: 42)
    assert await store.cas(
        "k", expected=ProgressStatus.QUEUED, new=ProgressStatus.IN_PROGRESS,
        patch=StageStatePatch(worker_id="w1", attempt=1),
    )
    graph.stage_state_compare_and_set.assert_awaited_once_with(
        "k", "QUEUED", "IN_PROGRESS", {"workerId": "w1", "attempt": 1, "updatedAtMs": 42}
    )


@pytest.mark.asyncio
async def test_stale_passes_status_values() -> None:
    graph = AsyncMock()
    graph.stage_states_stale = AsyncMock(return_value=[])
    await GraphStageStateStore(graph).stale(statuses=(ProgressStatus.QUEUED,), updated_before_ms=9, limit=3)
    graph.stage_states_stale.assert_awaited_once_with(["QUEUED"], 9, 3)


@pytest.mark.asyncio
async def test_headline_write_stamps_its_timestamp_and_checks_the_revision() -> None:
    graph = AsyncMock()
    graph.compare_and_set_record_fields = AsyncMock(return_value=["rec-1"])
    writer = GraphHeadlineStatusWriter(graph, clock_ms=lambda: 7)
    assert await writer.set_headline(
        ("rec-1",), HeadlineField.EXTRACTION, ProgressStatus.SKIPPED, rev="rev-a", reason="No LLM configured"
    ) == ["rec-1"]
    graph.compare_and_set_record_fields.assert_awaited_once_with(
        ["rec-1"], "rev-a",
        {"extractionStatus": "SKIPPED", "lastExtractionTimestamp": 7, "reason": "No LLM configured"},
    )


@pytest.mark.asyncio
async def test_parsing_status_has_no_timestamp_and_no_reason_unless_given() -> None:
    graph = AsyncMock()
    graph.compare_and_set_record_fields = AsyncMock(return_value=[])
    await GraphHeadlineStatusWriter(graph).set_headline(
        ("rec-1",), HeadlineField.PARSING, ProgressStatus.COMPLETED, rev="rev-a"
    )
    assert graph.compare_and_set_record_fields.await_args.args[2] == {"parsingStatus": "COMPLETED"}


@pytest.mark.asyncio
async def test_a_completed_stage_can_clear_a_stale_reason() -> None:
    graph = AsyncMock()
    graph.compare_and_set_record_fields = AsyncMock(return_value=["rec-1"])
    await GraphHeadlineStatusWriter(graph, clock_ms=lambda: 1).set_headline(
        ("rec-1",), HeadlineField.EXTRACTION, ProgressStatus.COMPLETED, rev="rev-a", clear_reason=True
    )
    assert graph.compare_and_set_record_fields.await_args.args[2]["reason"] is None


@pytest.mark.asyncio
async def test_stage_summaries_list_a_revisions_states_in_the_order_they_changed() -> None:
    from unittest.mock import AsyncMock, patch

    from app.modules.pipeline.state import GraphStageStateStore, stage_summaries

    def state(stage: str, updated: int) -> StageState:
        return StageState.model_construct(
            stage=stage, status=ProgressStatus.COMPLETED, reason=None, attempt=1,
            started_at_ms=None, finished_at_ms=None, updated_at_ms=updated,
        )

    with patch.object(GraphStageStateStore, "get_many", AsyncMock(return_value={"classify": state("classify", 9), "embed": state("embed", 2)})):
        summaries = await stage_summaries(AsyncMock(), "vr-1", "rev-a")
    assert [s.stage for s in summaries] == ["embed", "classify"]
    assert summaries[1].model_dump(by_alias=True)["updatedAtMs"] == 9
