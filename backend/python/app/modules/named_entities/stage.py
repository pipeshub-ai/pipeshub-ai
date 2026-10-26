"""Indexing stage. A failure here must not make the record unsearchable.

These functions can raise; ``SinkOrchestrator.extract_named_entities`` and
``persist_named_entities`` are the only callers and turn any exception into a
FAILED extraction or a logged warning. Call them through the orchestrator.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.modules.entity_resolution.models import ResolutionMode
from app.modules.named_entities.domain.config import load_named_entity_config
from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
    indexing_llm,
)
from app.modules.named_entities.graph_writer import (
    NamedEntityGraphWriter,
    PersistRetrySupersededError,
)
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.resolution import NamedEntityResolver
from app.telemetry.modules.named_entity_metrics import observe_stage, record_run

_MODEL_GAVE_UP = frozenset({"no_progress", "budget_turns", "budget_tokens", "timeout"})

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
    from app.models.entities import Record
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.modules.transformers.transformer import TransformContext
    from app.services.extraction.client import ExtractionClient
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)


def _reference_ms(record: Record) -> int | None:
    return getattr(record, "source_updated_at", None) or getattr(record, "source_created_at", None)


# A re-extraction reads up to this many single-call windows (about 200k
# characters at the default window size). One that still stops short is not
# queued again, so a record too long for it settles instead of looping.
REEXTRACT_MAX_WINDOWS = 50


def needs_reextract(extraction: NamedEntityExtraction) -> bool:
    """A written extraction that left text unread: the call budget ran out, or
    the agent stopped with blocks it had not read. Failed calls are not a reason:
    a re-extraction would fail the same way, and the last good entities stay."""
    stats = extraction.stats
    if extraction.status != "PARTIAL" or stats.windows_run >= REEXTRACT_MAX_WINDOWS:
        return False
    if stats.windows_total > stats.windows_run:
        return True
    return extraction.strategy == "agent" and extraction.termination_reason != "llm_error"


async def extract_named_entities(
    ctx: TransformContext,
    *,
    config_service: ConfigurationService,
    client: ExtractionClient | None = None,
    enabled: bool,
    reextract: bool = False,
) -> NamedEntityExtraction:
    """``reextract``: the completion of a record's earlier extraction, run locally
    in single calls with ``REEXTRACT_MAX_WINDOWS`` windows."""
    record = ctx.record
    if not enabled:
        return NamedEntityExtraction(status="SKIPPED", termination_reason="empty", strategy="deterministic")
    blocks = getattr(getattr(record, "block_containers", None), "blocks", None) or []
    if not blocks:
        return NamedEntityExtraction(status="SKIPPED", termination_reason="empty", strategy="deterministic")
    config = await load_named_entity_config(config_service)
    budgets = config.budgets
    if reextract:
        budgets = budgets.model_copy(update={"max_llm_windows": REEXTRACT_MAX_WINDOWS})
        client = None
    if client is not None:
        return await client.extract_entities(
            block_container=record.block_containers,
            org_id=record.org_id,
            record_name=record.record_name,
            record_type=getattr(record.record_type, "value", str(record.record_type)),
            reference_time_ms=_reference_ms(record),
            tz=config.tz,
            enabled_kinds=[kind.value for kind in config.enabled()],
            budgets=budgets.model_dump(),
        )
    llm = transport = None
    provider = model = "indexing"
    try:
        llm, transport, provider, model = await indexing_llm(
            config_service, org_id=record.org_id, shard_key=record.record_name or "",
        )
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
        budgets=budgets,
        llm=llm,
        transport=transport,
        provider_name=provider,
        model_name=model,
        # The agent reads in turns and can stop short again; single calls read it all.
        supports_tools=False if reextract else None,
    )
    return await NamedEntityExtractor().extract(request)


def _model_gave_nothing(extraction: NamedEntityExtraction) -> bool:
    """The model failed or gave up without accepting an entity. A model that
    finished, or answered the single call, with nothing is a real empty answer."""
    if extraction.termination_reason == "llm_error":
        return True
    return extraction.stats.accepted == 0 and extraction.termination_reason in _MODEL_GAVE_UP


async def persist_named_entities(
    ctx: TransformContext,
    extraction: NamedEntityExtraction,
    *,
    graph_provider: IGraphDBProvider,
    graph_data_store: GraphDataStore,
    entity_vector_store: EntityVectorStore | None = None,
    enabled: bool,
    llm: BaseChatModel | None = None,
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


def log_run(record: Record, extraction: NamedEntityExtraction, *, outcome: str, seconds: float) -> None:
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


def normalization_context(record: Record, tz: str) -> NormalizationContext:
    return NormalizationContext(reference_time_ms=_reference_ms(record), tz=tz or "UTC")
