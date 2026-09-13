"""ClassifyStage: fingerprint inputs, outcome mapping for every extractor failure, and saving."""

from collections.abc import Awaitable, Callable

import pytest

from app.models.blocks import BlocksContainer, SemanticMetadata
from app.modules.pipeline.models import Priority, StageJob, StageOutcome
from app.modules.pipeline.policy import DEFAULT_POLICY, PipelinePolicy
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.classify import ClassifyStage
from app.modules.transformers.document_extraction import ExtractionLLMError
from app.services.base_client import (
    ServiceBackpressureError,
    ServiceCallError,
    ServiceUnavailableError,
)
from app.services.extraction.client import ExtractionClientError
from app.services.resource_governor.models import ParseTier
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError


def _job(**overrides: object) -> StageJob:
    fields: dict[str, object] = {
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1", "rec-2"), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "text-1", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    }
    fields.update(overrides)
    return StageJob.model_validate(fields)


class _IO:
    def __init__(self) -> None:
        self.bound = ["rec-1"]
        self.blocks: BlocksContainer | None = BlocksContainer(blocks=[], block_groups=[])
        self.depts = ["Engineering", "Legal"]
        self.model = "llm:gpt-x"
        self.outcome: SemanticMetadata | BaseException | None = SemanticMetadata(summary="s", categories=["Security"])
        self.saved: list[tuple[list[str], SemanticMetadata]] = []
        self.save_error: BaseException | None = None
        self.has_metadata = True
        self.restored: list[SemanticMetadata] = []
        self.fence: Callable[[], Awaitable[bool]] | None = None

    @property
    def policy(self) -> PipelinePolicy:
        return DEFAULT_POLICY

    @property
    def deadline(self) -> Deadline:
        return Deadline(60)

    async def current_record_ids(self, job: StageJob) -> list[str]:
        return list(self.bound)

    async def departments(self, org_id: str) -> list[str]:
        return list(self.depts)

    async def model_identity(self, org_id: str) -> str:
        return self.model

    async def load_blocks(self, job: StageJob) -> BlocksContainer | None:
        return self.blocks

    async def classify(self, blocks: BlocksContainer, org_id: str, departments: list[str]) -> SemanticMetadata | None:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def stored_metadata_present(self, job: StageJob) -> bool:
        return self.has_metadata

    async def restore_stored_metadata(
        self, job: StageJob, metadata: SemanticMetadata, *, still_current: Callable[[], Awaitable[bool]] | None = None
    ) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.fence = still_current
        self.restored.append(metadata)

    async def save(self, job: StageJob, record_ids: list[str], metadata: SemanticMetadata) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved.append((record_ids, metadata))


class TestFingerprint:
    @pytest.mark.asyncio
    async def test_is_stable_for_the_same_inputs_in_any_department_order(self) -> None:
        io_a, io_b = _IO(), _IO()
        io_b.depts = ["Legal", "Engineering"]
        stage = ClassifyStage()
        assert (await stage.fingerprint(_job(), io_a)).digest() == (await stage.fingerprint(_job(), io_b)).digest()

    @pytest.mark.parametrize("change", ["text", "model", "departments"])
    @pytest.mark.asyncio
    async def test_changes_with_text_model_or_departments(self, change: str) -> None:
        stage, io = ClassifyStage(), _IO()
        before = (await stage.fingerprint(_job(), io)).digest()
        job = _job()
        if change == "text":
            job = _job(text_digest="text-2")
        elif change == "model":
            io.model = "llm:gpt-y"
        else:
            io.depts = ["Engineering"]
        assert (await stage.fingerprint(job, io)).digest() != before


def test_applies_to_text_or_images_when_classification_is_on() -> None:
    stage = ClassifyStage()
    assert stage.applies(_job().view, DEFAULT_POLICY)
    assert stage.applies(_job(text_chars=0, has_images=True).view, DEFAULT_POLICY)
    assert not stage.applies(_job(text_chars=0).view, DEFAULT_POLICY)
    assert not stage.applies(_job().view, PipelinePolicy(classification=False))


@pytest.mark.asyncio
async def test_saves_for_the_records_still_on_this_revision() -> None:
    io = _IO()
    result = await ClassifyStage().run(_job(), io)
    assert result.outcome is StageOutcome.COMPLETED and result.items == 1
    assert io.saved[0][0] == ["rec-1"]


@pytest.mark.asyncio
async def test_a_superseded_revision_is_stale_and_saves_nothing() -> None:
    io = _IO()
    io.bound = []
    assert (await ClassifyStage().run(_job(), io)).outcome is StageOutcome.STALE
    assert io.saved == []


@pytest.mark.asyncio
async def test_an_unreadable_stored_record_is_retried() -> None:
    io = _IO()
    io.blocks = None
    assert (await ClassifyStage().run(_job(), io)).outcome is StageOutcome.RETRY


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (LLMNotConfiguredError("No LLM is configured"), StageOutcome.SKIPPED),
        (ServiceUnavailableError("breaker open"), StageOutcome.PAUSED),
        (ExtractionLLMError("no output"), StageOutcome.RETRY),
        (ExtractionClientError(message="500"), StageOutcome.RETRY),
        (ServiceCallError("422 invalid", status_code=422), StageOutcome.FAILED),
        (LLMUnavailableError("provider refused the connection"), StageOutcome.PAUSED),
        (ServiceBackpressureError("slow down", retry_after=5.0), StageOutcome.PAUSED),
        (ServiceCallError("503 after retries", status_code=503), StageOutcome.RETRY),
        (ServiceCallError("429 after retries", status_code=429), StageOutcome.RETRY),
        (ServiceCallError("no status", status_code=None), StageOutcome.RETRY),
    ],
)
@pytest.mark.asyncio
async def test_extractor_failures_map_to_outcomes(error: BaseException, outcome: StageOutcome) -> None:
    io = _IO()
    io.outcome = error
    result = await ClassifyStage().run(_job(), io)
    assert result.outcome is outcome
    assert io.saved == []


@pytest.mark.asyncio
async def test_nothing_to_classify_is_skipped() -> None:
    io = _IO()
    io.outcome = None
    result = await ClassifyStage().run(_job(), io)
    assert result.outcome is StageOutcome.SKIPPED and result.record_view is not None


@pytest.mark.asyncio
async def test_a_provider_outage_carries_how_long_to_wait() -> None:
    io = _IO()
    io.outcome = LLMUnavailableError("down", retry_after=25.0)
    result = await ClassifyStage().run(_job(), io)
    assert result.outcome is StageOutcome.PAUSED and result.retry_after_s == 25.0


@pytest.mark.asyncio
async def test_a_completed_run_keeps_its_classification_as_output() -> None:
    result = await ClassifyStage().run(_job(), _IO())
    assert result.outcome is StageOutcome.COMPLETED
    assert result.output is not None and SemanticMetadata.model_validate_json(result.output).categories == ["Security"]


@pytest.mark.asyncio
async def test_a_record_reindexed_meanwhile_ends_the_job_stale() -> None:
    from app.modules.pipeline.leases import RevisionSuperseded

    io = _IO()
    io.save_error = RevisionSuperseded("rec-1")
    assert (await ClassifyStage().run(_job(), io)).outcome is StageOutcome.STALE


@pytest.mark.asyncio
async def test_a_record_that_stays_busy_is_retried() -> None:
    from app.modules.pipeline.leases import RecordBusy

    io = _IO()
    io.save_error = RecordBusy("record rec-1 stayed busy")
    result = await ClassifyStage().run(_job(), io)
    assert result.outcome is StageOutcome.RETRY and "busy" in (result.reason or "")


@pytest.mark.asyncio
async def test_a_kept_classification_goes_back_without_a_model_call() -> None:
    io = _IO()
    io.has_metadata = False
    stage = ClassifyStage()
    assert not await stage.output_present(_job(), io)
    kept = SemanticMetadata(summary="s", categories=["Security"]).model_dump_json()

    async def unchanged() -> bool:
        return True

    assert await stage.restore_output(_job(), io, kept, still_current=unchanged)
    assert io.restored[0].categories == ["Security"] and io.fence is unchanged
    assert not await stage.restore_output(_job(), io, "not json")
