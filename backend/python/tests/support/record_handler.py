"""A ``RecordEventHandler`` over mock dependencies, for the unit tests and the messaging integration tests.

Shared from here rather than from a test module: the integration suites import only helpers that the
integration matrix rules list (``scripts/pr_changes.py``), and ``tests/support/`` is one of them.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.services.messaging.kafka.handlers.record import RecordEventHandler

__all__ = ["make_record_handler"]


def make_record_handler(
    logger: Any = None,  # noqa: ANN401
    config_service: Any = None,  # noqa: ANN401
    event_processor: Any = None,  # noqa: ANN401
    producer: Any = None,  # noqa: ANN401
) -> RecordEventHandler:
    """Create a RecordEventHandler with mock dependencies."""
    logger = logger or MagicMock()
    config_service = config_service or AsyncMock()
    event_processor = event_processor or MagicMock()
    producer = producer if producer is not None else AsyncMock()

    # Ensure event_processor has the nested objects the handler expects
    if not hasattr(event_processor, "graph_provider") or event_processor.graph_provider is None:
        event_processor.graph_provider = AsyncMock()
    elif not isinstance(event_processor.graph_provider, AsyncMock):
        event_processor.graph_provider = AsyncMock()
    if not hasattr(event_processor, "processor") or event_processor.processor is None:
        event_processor.processor = MagicMock()
        event_processor.processor.indexing_pipeline = AsyncMock()
    # Mirrors EventProcessor's real default (None) — a bare MagicMock would
    # auto-vivify `.sink_orchestrator.entity_vector_store` as a truthy
    # MagicMock, which the handler would then try to `await` a method call
    # on. Tests that need entity-vector-store behaviour set this explicitly
    # afterwards via `handler.event_processor.sink_orchestrator = ...`.
    event_processor.sink_orchestrator = None
    # Same reason: no stage runtime unless a test binds one (an auto-vivified
    # MagicMock would be awaited). Tests that need it set it afterwards.
    if "stage_ingress" not in vars(event_processor) and isinstance(event_processor, MagicMock):
        event_processor.stage_ingress = None

    return RecordEventHandler(
        logger=logger,
        config_service=config_service,
        event_processor=event_processor,
        producer=producer,
    )
