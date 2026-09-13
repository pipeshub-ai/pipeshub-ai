"""Classification as a pipeline stage: runs after the record is searchable, on its own permits.

It reads the stored record's blocks, asks the configured extractor (in process or the
Extraction service) for departments, categories, topics, languages and a summary, and
writes them to the stored record, the summary vector and the record's taxonomy edges.
Its fingerprint is the classification input plus the model, prompt and the org's
department list, so re-indexing unchanged content costs no LLM call.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import ClassVar, Protocol

from app.models.blocks import BlocksContainer, SemanticMetadata
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
from app.modules.transformers.document_extraction import ExtractionLLMError
from app.services.base_client import (
    TRANSIENT_STATUS_CODES,
    ServiceBackpressureError,
    ServiceCallError,
    ServiceUnavailableError,
)
from app.services.extraction.client import ExtractionClientError
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError

# Bump when the classification prompt or its output mapping changes.
CLASSIFY_PROMPT_VERSION = 1


class ClassifyIO(StageIO, Protocol):
    async def current_record_ids(self, job: StageJob) -> list[str]:
        """Bound records still on ``job.rev``; empty when every one moved on."""
        ...

    async def departments(self, org_id: str) -> list[str]: ...

    async def model_identity(self, org_id: str) -> str:
        """Which model classifies for this org, without building it."""
        ...

    async def load_blocks(self, job: StageJob) -> BlocksContainer | None:
        """The stored record's blocks, or None when no stored record exists."""
        ...

    async def classify(self, blocks: BlocksContainer, org_id: str, departments: list[str]) -> SemanticMetadata | None:
        """None when the document has nothing to classify."""
        ...

    async def stored_metadata_present(self, job: StageJob) -> bool:
        """Whether the stored record still carries a classification (a re-index rewrites it)."""
        ...

    async def restore_stored_metadata(
        self, job: StageJob, metadata: SemanticMetadata, *, still_current: Callable[[], Awaitable[bool]] | None = None
    ) -> None:
        """Put a kept classification back into the stored record; raises like ``save``, and
        ``RevisionSuperseded`` when ``still_current`` fails under the record lease."""
        ...

    async def save(self, job: StageJob, record_ids: list[str], metadata: SemanticMetadata) -> None:
        """Stored record, summary vector, and each record's taxonomy edges.

        Raises ``RevisionSuperseded`` when the record moved to a newer revision before the
        stored record could be written, and ``RecordBusy`` when its lease stayed held.
        """
        ...


class ClassifyStage:
    name: ClassVar[str] = "classify"
    version: ClassVar[int] = 1
    # "embed" is produced by the legacy indexer until it becomes a stage.
    requires: ClassVar[frozenset[str]] = frozenset({"embed"})
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = HeadlineField.EXTRACTION
    budget_s: ClassVar[float] = 900.0

    def applies(self, view: RecordView, policy: PipelinePolicy) -> bool:
        return policy.classification and (view.text_chars > 0 or view.has_images)

    async def fingerprint(self, job: StageJob, io: ClassifyIO) -> StageFingerprint:
        config = json.dumps(
            {
                "model": await io.model_identity(job.org_id),
                "prompt": CLASSIFY_PROMPT_VERSION,
                "departments": sorted(await io.departments(job.org_id)),
            },
            sort_keys=True,
        )
        return StageFingerprint(
            stage=self.name,
            stage_version=self.version,
            input_digest=job.text_digest,
            config_digest=hashlib.sha256(config.encode("utf-8")).hexdigest()[:32],
        )

    async def output_present(self, job: StageJob, io: ClassifyIO) -> bool:
        """Whether this revision's classification is still in the stored record."""
        return await io.stored_metadata_present(job)

    async def restore_output(
        self, job: StageJob, io: ClassifyIO, output: str, *, still_current: Callable[[], Awaitable[bool]] | None = None
    ) -> bool:
        """Put a kept classification back without a model call; False when it cannot be, or when
        ``still_current`` (checked under the record lease) says the kept output was superseded."""
        try:
            await io.restore_stored_metadata(
                job, SemanticMetadata.model_validate_json(output), still_current=still_current
            )
        except (RevisionSuperseded, RecordBusy, ValueError):
            return False
        return True

    async def run(self, job: StageJob, io: ClassifyIO) -> StageResult:
        record_ids = await io.current_record_ids(job)
        if not record_ids:
            return StageResult(outcome=StageOutcome.STALE)
        blocks = await io.load_blocks(job)
        if blocks is None:
            return StageResult(outcome=StageOutcome.RETRY, reason="the stored record is not readable yet")
        try:
            metadata = await io.classify(blocks, job.org_id, await io.departments(job.org_id))
        except LLMNotConfiguredError as exc:
            return StageResult(outcome=StageOutcome.SKIPPED, reason=f"Extraction skipped: {exc}", record_view=job.view)
        except LLMUnavailableError as exc:
            # The model provider is down, not the document: wait without burning attempts.
            return StageResult(
                outcome=StageOutcome.PAUSED,
                reason=f"Model provider unavailable: {exc}",
                retry_after_s=exc.retry_after,
            )
        except (ServiceUnavailableError, ServiceBackpressureError) as exc:
            # The Extraction service is down, its breaker is open, or it asked callers to slow down.
            return StageResult(outcome=StageOutcome.PAUSED, reason=f"Extraction service unavailable: {exc}")
        except (ExtractionLLMError, ExtractionClientError) as exc:
            return StageResult(outcome=StageOutcome.RETRY, reason=f"Classification failed: {exc}")
        except ServiceCallError as exc:
            if exc.status_code is None or exc.status_code in TRANSIENT_STATUS_CODES:
                return StageResult(outcome=StageOutcome.RETRY, reason=f"Extraction service failed: {exc}")
            return StageResult(outcome=StageOutcome.FAILED, reason=f"Extraction service rejected the document: {exc}")
        if metadata is None:
            return StageResult(outcome=StageOutcome.SKIPPED, reason="nothing to classify", record_view=job.view)
        try:
            await io.save(job, record_ids, metadata)
        except RevisionSuperseded:
            return StageResult(outcome=StageOutcome.STALE)
        except RecordBusy as exc:
            return StageResult(outcome=StageOutcome.RETRY, reason=str(exc))
        return StageResult(
            outcome=StageOutcome.COMPLETED,
            items=len(record_ids),
            record_view=job.view,
            output=metadata.model_dump_json(),
        )
