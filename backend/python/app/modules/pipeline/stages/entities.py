"""Named-entity extraction as a pipeline stage, beside classification.

Dispatched with classify when the record is searchable (both require ``embed``), on its own
topic and permits, so classification never waits on it and neither blocks the other. It
reads the stored record's blocks, extracts the named entities (in process or through the
Extraction service), writes each record's entities and mentions to the graph and the entity
points, and keeps the extraction as its output (``app.modules.named_entities.kept`` reads
it back). It writes neither the stored record nor a record status: ``extractionStatus`` is
classification's.

Its fingerprint is the text plus the extraction model, prompt version, the named-entity
configuration and whether extraction is on, so re-indexing unchanged content costs no
model call, and turning extraction off runs it once more to clear the record's entities.
"""

import hashlib
import json
from typing import Any, ClassVar, Protocol

from app.models.blocks import BlocksContainer
from app.modules.named_entities.domain.models import (
    EXTRACTOR_VERSION,
    NamedEntityExtraction,
)
from app.modules.pipeline.leases import RecordBusy, RevisionSuperseded
from app.modules.pipeline.models import (
    HeadlineField,
    RecordView,
    StageFingerprint,
    StageJob,
    StageOutcome,
    StageResult,
    Workload,
)
from app.modules.pipeline.policy import PipelinePolicy
from app.modules.pipeline.stage import StageIO

# Bump when the extraction prompt, its strategies or the stored section's shape change.
ENTITIES_PROMPT_VERSION = 1


class EntitiesIO(StageIO, Protocol):
    async def current_record_ids(self, job: StageJob) -> list[str]:
        """Bound records still on ``job.rev``; empty when every one moved on."""
        ...

    async def enabled(self) -> bool:
        """Whether named-entity extraction is turned on."""
        ...

    async def entities_config(self, job: StageJob) -> dict[str, Any]:
        """What the output depends on beyond the text: model, configuration, the flag. JSON-serializable."""
        ...

    async def load_blocks(self, job: StageJob) -> BlocksContainer | None:
        """The stored record's blocks, or None when no stored record exists."""
        ...

    async def extract(self, job: StageJob, blocks: BlocksContainer) -> NamedEntityExtraction:
        """Never raises for a model failure: it returns a FAILED extraction."""
        ...

    async def save(self, job: StageJob, record_ids: list[str], extraction: NamedEntityExtraction) -> str:
        """Each record's entities and the entity points; the persist outcome.

        Raises ``RevisionSuperseded`` when the record moved to a newer revision before its
        entities could be written, and ``RecordBusy`` when its lease stayed held."""
        ...


class EntitiesStage:
    name: ClassVar[str] = "entities"
    version: ClassVar[int] = 1
    # A sibling of classify, not its successor: neither reads the other's output.
    requires: ClassVar[frozenset[str]] = frozenset({"embed"})
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = None
    budget_s: ClassVar[float] = 900.0

    def applies(self, view: RecordView, policy: PipelinePolicy) -> bool:
        # Images carry no text the recognizers read. Whether extraction is on is read when the
        # job runs: an install that turns it off must still clear the entities it wrote.
        return policy.named_entities and view.text_chars > 0

    async def fingerprint(self, job: StageJob, io: EntitiesIO) -> StageFingerprint:
        config = json.dumps(
            {"prompt": ENTITIES_PROMPT_VERSION, "extractor": EXTRACTOR_VERSION, **(await io.entities_config(job))}, sort_keys=True, default=str
        )
        return StageFingerprint(
            stage=self.name,
            stage_version=self.version,
            input_digest=job.text_digest,
            config_digest=hashlib.sha256(config.encode("utf-8")).hexdigest()[:32],
        )

    async def run(self, job: StageJob, io: EntitiesIO) -> StageResult:
        record_ids = await io.current_record_ids(job)
        if not record_ids:
            return StageResult(outcome=StageOutcome.STALE)
        blocks = await io.load_blocks(job)
        if blocks is None:
            return StageResult(outcome=StageOutcome.RETRY, reason="the stored record is not readable yet")
        try:
            extraction = await io.extract(job, blocks)
        except RevisionSuperseded:
            return StageResult(outcome=StageOutcome.STALE)
        if extraction.status == "FAILED":
            # The model failed; the record keeps its last good entities meanwhile.
            return StageResult(
                outcome=StageOutcome.RETRY,
                reason=f"Named-entity extraction failed ({extraction.termination_reason})",
            )
        try:
            outcome = await io.save(job, record_ids, extraction)
        except RevisionSuperseded:
            return StageResult(outcome=StageOutcome.STALE)
        except RecordBusy as exc:
            return StageResult(outcome=StageOutcome.RETRY, reason=str(exc))
        if outcome == "disabled":
            return StageResult(
                outcome=StageOutcome.SKIPPED, reason="Named-entity extraction is turned off", record_view=job.view
            )
        if extraction.status == "SKIPPED":
            return StageResult(outcome=StageOutcome.SKIPPED, reason="nothing to extract", record_view=job.view)
        return StageResult(
            outcome=StageOutcome.COMPLETED,
            items=len(record_ids),
            record_view=job.view,
            output=extraction.model_dump_json(),
        )
