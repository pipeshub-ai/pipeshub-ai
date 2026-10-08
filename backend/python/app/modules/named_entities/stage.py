"""Indexing stage. A failure here must not make the record unsearchable.

These functions can raise; ``SinkOrchestrator.extract_named_entities`` and
``persist_named_entities`` are the only callers and turn any exception into a
FAILED extraction or a logged warning. Call them through the orchestrator.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from app.modules.entity_resolution.models import ResolutionMode
from app.modules.named_entities.domain.config import load_named_entity_config
from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
    indexing_llm,
)
from app.modules.named_entities.graph_writer import NamedEntityGraphWriter, PersistRetrySupersededError
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.resolution import NamedEntityResolver
from app.telemetry.modules.named_entity_metrics import observe_stage, record_run

_MODEL_GAVE_UP = frozenset({"no_progress", "budget_turns", "budget_tokens", "timeout"})

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


def _reference_ms(record) -> int | None:
    return getattr(record, "source_updated_at", None) or getattr(record, "source_created_at", None)


async def extract_named_entities(ctx, *, config_service, client=None, enabled: bool) -> NamedEntityExtraction:
    record = ctx.record
    if not enabled:
        return NamedEntityExtraction(status="SKIPPED", termination_reason="empty", strategy="deterministic")
    blocks = getattr(getattr(record, "block_containers", None), "blocks", None) or []
    if not blocks:
        return NamedEntityExtraction(status="SKIPPED", termination_reason="empty", strategy="deterministic")
    config = await load_named_entity_config(config_service)
    if client is not None:
        return await client.extract_entities(
            block_container=record.block_containers,
            org_id=record.org_id,
            record_name=record.record_name,
            record_type=getattr(record.record_type, "value", str(record.record_type)),
            reference_time_ms=_reference_ms(record),
            tz=config.tz,
            enabled_kinds=[kind.value for kind in config.enabled()],
            budgets=config.budgets.model_dump(),
        )
    llm = transport = None
    provider = model = "indexing"
    try:
        llm, transport, provider, model = await indexing_llm(config_service)
    except Exception:
        logger.warning("Named-entity model unavailable; keeping deterministic spans")
    request = NamedEntityRequest(
        blocks=blocks,
        org_id=record.org_id,
        record_name=record.record_name or "",
        record_type=getattr(record.record_type, "value", "") or "",
        reference_time_ms=_reference_ms(record),
        tz=config.tz,
        enabled=config.enabled(),
        budgets=config.budgets,
        llm=llm,
        transport=transport,
        provider_name=provider,
        model_name=model,
    )
    return await NamedEntityExtractor().extract(request)


@contextmanager
def extraction_blob_holder(record: object, extraction: NamedEntityExtraction | None) -> Iterator[bool]:
    """While the blob is written, carry the extraction even if classification gave
    no semantic metadata: the blob is the copy the graph and vectors are rebuilt
    from. The holder is removed again because no metadata means a failed
    classification to the graph writer. Yields whether a holder was set."""
    holds = (
        isinstance(extraction, NamedEntityExtraction)
        and getattr(record, "semantic_metadata", None) is None
        and (bool(extraction.entities) or extraction.status != "SKIPPED")
    )
    if not holds:
        yield False
        return
    from app.models.blocks import SemanticMetadata

    record.semantic_metadata = SemanticMetadata(named_entities=extraction.model_dump(mode="json"))
    try:
        yield True
    finally:
        record.semantic_metadata = None


def attach_named_entities(record, extraction: NamedEntityExtraction) -> None:
    metadata = getattr(record, "semantic_metadata", None)
    if metadata is None:
        return
    metadata.named_entities = extraction.model_dump(mode="json")


def _model_gave_nothing(extraction: NamedEntityExtraction) -> bool:
    """The model failed or gave up without accepting an entity. A model that
    finished, or answered the single call, with nothing is a real empty answer."""
    if extraction.termination_reason == "llm_error":
        return True
    return extraction.stats.accepted == 0 and extraction.termination_reason in _MODEL_GAVE_UP


async def persist_named_entities(
    ctx,
    extraction: NamedEntityExtraction,
    *,
    graph_provider,
    graph_data_store,
    entity_vector_store=None,
    enabled: bool,
    llm=None,
    retry_due: int | None = None,
) -> str:
    """Write the record's entities. Returns ``written``, ``kept`` (the last good
    entities stay), ``disabled``, ``superseded`` (a retry found a newer write
    already in place) or ``failed``: a failed write is the caller's to retry.
    ``retry_due`` marks a retry's write: it lands only while that retry is pending."""
    record = ctx.record
    writer = NamedEntityGraphWriter(graph_provider, graph_data_store, logger)
    if not enabled:
        # The flag is off by default; skip the write for records that never had entities.
        if retry_due is not None or await graph_provider.get_named_entities_for_record(record.id):
            try:
                await writer.clear_for_record(record.id, retry_due=retry_due)
            except PersistRetrySupersededError:
                return "superseded"
        return "disabled"
    started = time.perf_counter()
    outcome = "written"
    result = "written"
    try:
        if extraction.status == "FAILED" or (
            _model_gave_nothing(extraction)
            and await graph_provider.get_named_entities_for_record(record.id)
        ):
            # A transient failure must not erase the entities from the last good run.
            # When the model stopped without accepting anything only the deterministic
            # kinds are left, and writing them would drop every semantic edge the record had.
            outcome = "failed"
            return "kept"
        resolver = NamedEntityResolver(graph_provider, llm=llm, mode=ResolutionMode.SHADOW)
        resolved = await resolver.resolve(record.org_id, extraction.entities)
        embeddable = await writer.write(record.org_id, record.id, resolved, retry_due=retry_due)
        if entity_vector_store and embeddable:
            await entity_vector_store.upsert_entities_batch(embeddable)
    except PersistRetrySupersededError:
        outcome = result = "superseded"
    except Exception as exc:
        outcome = result = "failed"
        logger.warning(
            "named_entities persist failed record=%s entities=%d error=%s",
            getattr(record, "id", ""),
            len(extraction.entities),
            type(exc).__name__,
        )
    finally:
        elapsed = time.perf_counter() - started
        observe_stage(extraction.strategy, elapsed)
        record_run(extraction, outcome=outcome)
        log_run(record, extraction, outcome=outcome, seconds=elapsed)
    return result


def log_run(record, extraction: NamedEntityExtraction, *, outcome: str, seconds: float) -> None:
    """One line per record. Counts only: no surfaces, names or values."""
    by_kind: dict[str, int] = {}
    for entity in extraction.entities:
        by_kind[entity.kind.value] = by_kind.get(entity.kind.value, 0) + 1
    stats = extraction.stats
    logger.info(
        "named_entities record=%s outcome=%s status=%s strategy=%s reason=%s entities=%d "
        "kinds=%s deterministic=%d accepted=%d rejected=%d rejected_reasons=%s dropped=%d turns=%d tool_calls=%d "
        "extract_ms=%d persist_seconds=%.2f",
        getattr(record, "id", ""),
        outcome,
        extraction.status,
        extraction.strategy,
        extraction.termination_reason,
        len(extraction.entities),
        ",".join(f"{kind}:{n}" for kind, n in sorted(by_kind.items())),
        stats.deterministic,
        stats.accepted,
        stats.rejected,
        ",".join(f"{reason}:{n}" for reason, n in sorted(stats.rejected_reasons.items())) or "-",
        stats.dropped_cap,
        stats.turns,
        stats.tool_calls,
        stats.extract_ms,
        seconds,
    )


def normalization_context(record, tz: str) -> NormalizationContext:
    return NormalizationContext(reference_time_ms=_reference_ms(record), tz=tz or "UTC")
