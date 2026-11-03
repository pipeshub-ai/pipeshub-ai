"""EntitiesStage: a sibling of classify with its own fingerprint, outcomes and kept output."""

from typing import Any

import pytest

from app.models.blocks import BlocksContainer
from app.modules.named_entities.domain.models import (
    ExtractionStats,
    NamedEntityExtraction,
)
from app.modules.pipeline.leases import RecordBusy, RevisionSuperseded
from app.modules.pipeline.models import Priority, StageJob, StageOutcome
from app.modules.pipeline.policy import DEFAULT_POLICY, PipelinePolicy
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.entities import EntitiesStage
from app.services.resource_governor.models import ParseTier


def _job(**overrides: object) -> StageJob:
    fields: dict[str, object] = {
        "stage": "entities", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "text-1", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    }
    fields.update(overrides)
    return StageJob.model_validate(fields)


class _IO:
    def __init__(self) -> None:
        self.bound = ["rec-1"]
        self.blocks: BlocksContainer | None = BlocksContainer(blocks=[], block_groups=[])
        self.config: dict[str, Any] = {"enabled": True, "model": "llm:x", "kinds": ["person"]}
        self.extraction = NamedEntityExtraction(status="COMPLETED")
        self.outcome = "written"
        self.save_error: BaseException | None = None
        self.saved: list[list[str]] = []
        self.saved_blocks: list[BlocksContainer] = []

    @property
    def policy(self) -> PipelinePolicy:
        return DEFAULT_POLICY

    @property
    def deadline(self) -> Deadline:
        return Deadline(60)

    async def current_record_ids(self, job: StageJob) -> list[str]:
        return list(self.bound)

    async def enabled(self) -> bool:
        return bool(self.config["enabled"])

    async def entities_config(self, job: StageJob) -> dict[str, Any]:
        return dict(self.config)

    async def load_blocks(self, job: StageJob) -> BlocksContainer | None:
        return self.blocks

    async def extract(self, job: StageJob, blocks: BlocksContainer) -> NamedEntityExtraction:
        return self.extraction

    async def save(
        self, job: StageJob, record_ids: list[str], extraction: NamedEntityExtraction, blocks: BlocksContainer,
    ) -> str:
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(record_ids)
        self.saved_blocks.append(blocks)
        return self.outcome


def test_it_needs_only_the_indexer_and_text() -> None:
    stage = EntitiesStage()
    assert stage.requires == frozenset({"embed"}) and stage.headline is None
    assert stage.applies(_job().view, DEFAULT_POLICY)
    assert not stage.applies(_job(text_chars=0, has_images=True).view, DEFAULT_POLICY)
    assert not stage.applies(_job().view, PipelinePolicy(named_entities=False))


@pytest.mark.parametrize("change", ["text", "enabled", "model", "kinds"])
@pytest.mark.asyncio
async def test_the_fingerprint_follows_text_flag_model_and_configuration(change: str) -> None:
    stage, io = EntitiesStage(), _IO()
    before = (await stage.fingerprint(_job(), io)).digest()
    job = _job()
    if change == "text":
        job = _job(text_digest="text-2")
    elif change == "enabled":
        io.config["enabled"] = False
    elif change == "model":
        io.config["model"] = "llm:y"
    else:
        io.config["kinds"] = ["person", "organization"]
    assert (await stage.fingerprint(job, io)).digest() != before


@pytest.mark.asyncio
async def test_a_completed_run_keeps_the_extraction_as_output() -> None:
    io = _IO()
    result = await EntitiesStage().run(_job(), io)
    assert result.outcome is StageOutcome.COMPLETED and io.saved == [["rec-1"]]
    assert NamedEntityExtraction.model_validate_json(result.output or "").status == "COMPLETED"


@pytest.mark.asyncio
async def test_the_entities_are_saved_with_the_blocks_they_came_from() -> None:
    # The persist also dates a knowledge-base upload by what its text states.
    io = _IO()
    await EntitiesStage().run(_job(), io)
    assert io.saved_blocks == [io.blocks]


@pytest.mark.asyncio
async def test_a_failed_extraction_is_retried_and_writes_nothing() -> None:
    io = _IO()
    io.extraction = NamedEntityExtraction(status="FAILED", termination_reason="llm_error")
    assert (await EntitiesStage().run(_job(), io)).outcome is StageOutcome.RETRY
    assert io.saved == []


@pytest.mark.parametrize("outcome", ["written", "kept"])
@pytest.mark.asyncio
async def test_a_model_failure_with_deterministic_hits_saves_them_and_is_retried(outcome: str) -> None:
    # "written": a first index keeps its amounts and dates meanwhile; "kept": an edited record
    # keeps its last good names. Either way the fingerprint is not stored, so the names come later.
    io = _IO()
    io.outcome = outcome
    io.extraction = NamedEntityExtraction(status="PARTIAL", termination_reason="llm_error", strategy="deterministic")
    result = await EntitiesStage().run(_job(), io)
    assert result.outcome is StageOutcome.RETRY and result.output is None
    assert io.saved == [["rec-1"]]


@pytest.mark.asyncio
async def test_a_single_call_with_a_failed_window_is_retried() -> None:
    io = _IO()
    io.extraction = NamedEntityExtraction(
        status="PARTIAL", termination_reason="single_call", strategy="single_call",
        stats=ExtractionStats(windows_total=3, windows_run=3, windows_failed=1),
    )
    assert (await EntitiesStage().run(_job(), io)).outcome is StageOutcome.RETRY


@pytest.mark.parametrize(("status", "saved"), [("PARTIAL", [["rec-1"]]), ("FAILED", [])])
@pytest.mark.asyncio
async def test_a_provider_outage_pauses_instead_of_spending_attempts(status: str, saved: list) -> None:
    # FAILED: nothing deterministic was found either, so nothing is written meanwhile.
    io = _IO()
    io.extraction = NamedEntityExtraction(
        status=status, termination_reason="llm_error", strategy="deterministic",
        stats=ExtractionStats(windows_total=2, windows_run=2, windows_failed=2, windows_unavailable=2),
    )
    result = await EntitiesStage().run(_job(), io)
    assert result.outcome is StageOutcome.PAUSED and io.saved == saved


@pytest.mark.asyncio
async def test_an_outage_mixed_with_other_failures_still_counts_toward_the_attempt_limit() -> None:
    io = _IO()
    io.extraction = NamedEntityExtraction(
        status="PARTIAL", termination_reason="llm_error", strategy="deterministic",
        stats=ExtractionStats(windows_total=3, windows_run=3, windows_failed=3, windows_unavailable=1),
    )
    assert (await EntitiesStage().run(_job(), io)).outcome is StageOutcome.RETRY


@pytest.mark.parametrize("reason", ["timeout", "no_progress", "budget_turns"])
@pytest.mark.asyncio
async def test_an_agent_that_gave_up_settles_rather_than_repeating_itself(reason: str) -> None:
    io = _IO()
    io.extraction = NamedEntityExtraction(status="PARTIAL", termination_reason=reason, strategy="agent")
    assert (await EntitiesStage().run(_job(), io)).outcome is StageOutcome.COMPLETED


@pytest.mark.asyncio
async def test_a_model_failure_while_turned_off_is_skipped_not_retried() -> None:
    io = _IO()
    io.outcome = "disabled"
    io.extraction = NamedEntityExtraction(status="PARTIAL", termination_reason="llm_error")
    assert (await EntitiesStage().run(_job(), io)).outcome is StageOutcome.SKIPPED


@pytest.mark.asyncio
async def test_turned_off_clears_through_the_persist_and_is_skipped() -> None:
    io = _IO()
    io.extraction = NamedEntityExtraction(status="SKIPPED", termination_reason="empty")
    io.outcome = "disabled"
    result = await EntitiesStage().run(_job(), io)
    assert result.outcome is StageOutcome.SKIPPED and io.saved == [["rec-1"]]


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(RevisionSuperseded("rec-1"), StageOutcome.STALE), (RecordBusy("busy"), StageOutcome.RETRY)],
)
@pytest.mark.asyncio
async def test_save_failures_map_to_outcomes(error: BaseException, outcome: StageOutcome) -> None:
    io = _IO()
    io.save_error = error
    assert (await EntitiesStage().run(_job(), io)).outcome is outcome


@pytest.mark.asyncio
async def test_a_superseded_revision_is_stale() -> None:
    io = _IO()
    io.bound = []
    assert (await EntitiesStage().run(_job(), io)).outcome is StageOutcome.STALE


@pytest.mark.asyncio
async def test_the_extraction_is_kept_as_the_stage_output_only() -> None:
    """Nothing to put back into the stored record: the stage state holds the extraction, so the
    worker never asks whether a re-index left it in place."""
    stage = EntitiesStage()
    assert not hasattr(stage, "output_present") and not hasattr(stage, "restore_output")
    result = await stage.run(_job(), _IO())
    assert NamedEntityExtraction.model_validate_json(result.output or "").status == "COMPLETED"
