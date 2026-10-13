"""HTTP client for the Extraction Service.

Usage::

    client = ExtractionClient(service_url="http://localhost:8093")
    classification = await client.classify(
        block_container=container,
        org_id="org-123",
        departments=["Engineering", "Finance"],
        record_name="Q3 Board Deck.pdf",
        record_type="FILE",
    )
"""
from __future__ import annotations

import logging
import math
import os
from typing import TYPE_CHECKING, Any

from app.config.constants.service import TokenScopes, config_node_constants
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.modules.transformers.document_extraction import (
    DEFAULT_CONTEXT_LENGTH,
    classify_request_blocks,
)
from app.services.base_client import BaseServiceClient, ServiceCallError
from app.services.messaging.backpressure import get_default_backpressure_coordinator

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.messaging.backpressure import BackpressureCoordinator

logger = logging.getLogger(__name__)


class ExtractionClientError(Exception):
    """Raised when an extraction request fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}


class ExtractionClient(BaseServiceClient):
    """Async HTTP client for the Extraction Service (port 8093)."""

    def __init__(
        self,
        service_url: str | None = None,
        read_timeout: float = 600.0,  # 10 min — LLM calls can be slow
        max_retries: int = 2,
        retry_delay: float = 2.0,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
        config_service: "ConfigurationService | None" = None,
    ) -> None:
        super().__init__(
            service_url=service_url or os.getenv("EXTRACTION_SERVICE_URL", "http://localhost:8093"),
            service_name="ExtractionService",
            read_timeout=read_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            backpressure_coordinator=backpressure_coordinator or get_default_backpressure_coordinator(),
            config_service=config_service,
            service_scope=TokenScopes.DOCUMENT_CLASSIFY,
        )

    async def classify(
        self,
        block_container: BlocksContainer,
        org_id: str,
        departments: list[str] | None = None,
        record_name: str = "",
        record_type: str = "",
    ) -> SemanticMetadata | None:
        """Call ``POST /api/v1/extract/classify`` and return SemanticMetadata.

        Returns ``None`` when the LLM produced no classification (e.g. empty
        document).  Raises :class:`ExtractionClientError` on explicit failures.
        Raises :class:`ServiceCallError` on connection problems.
        """
        context_length_ceiling = await self._context_length_ceiling()
        payload = {
            "block_container": {
                "blocks": classify_request_blocks(
                    block_container.blocks, context_length_ceiling
                ),
            },
            "org_id": org_id,
            "departments": departments or [],
            "record_name": record_name,
            "record_type": record_type,
        }

        response = await self._post_json(
            "/api/v1/extract/classify",
            payload,
            operation="classify",
            org_id=org_id,
        )

        body = response.json()

        if not body.get("success"):
            error_msg = body.get("error") or "Classification failed"
            raise ExtractionClientError(message=error_msg)

        classification_dict = body.get("classification")
        if classification_dict is None:
            return None

        return SemanticMetadata(**classification_dict)

    async def _context_length_ceiling(self) -> int | float | None:
        """The largest context length the service could classify with.

        The service picks its model per call (``get_llm_for_role(...,
        "indexing")``) and reads ``contextLength`` off that model's entry,
        defaulting to DEFAULT_CONTEXT_LENGTH. Every entry it could pick is in
        the AI-models config, so their maximum bounds its budget whichever it
        picks. ``None`` (send every block) when that cannot be read.

        Read uncached, as the service reads it (``app/utils/llm.py``): a cached
        copy could hold a context length an admin has since raised, and the
        tail trimmed here would then be one the service still reads.
        """
        if self._config_service is None:
            return None
        try:
            ai_models = await self._config_service.get_config(
                config_node_constants.AI_MODELS.value, use_cache=False
            )
        except Exception:  # trimming is an optimisation; send every block
            return None
        if not isinstance(ai_models, dict):
            return None
        ceiling: int | float | None = None
        for entries in ai_models.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                value = entry.get("contextLength") or DEFAULT_CONTEXT_LENGTH
                # The service would fail on a non-number; send it everything.
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    return None
                ceiling = value if ceiling is None else max(ceiling, value)
        return ceiling

    async def extract_entities(
        self,
        block_container: BlocksContainer,
        org_id: str,
        record_name: str = "",
        record_type: str = "",
        reference_time_ms: int | None = None,
        tz: str = "UTC",
        enabled_kinds: list[str] | None = None,
        budgets: dict | None = None,
    ):
        """Call ``POST /api/v1/extract/entities``. Read timeout covers the agent wall clock."""
        from app.modules.named_entities.domain.models import (
            NamedEntityExtraction,
            read_stored_extraction,
        )

        payload = {
            "block_container": block_container.model_dump(mode="json"),
            "org_id": org_id,
            "record_name": record_name,
            "record_type": record_type,
            "reference_time_ms": reference_time_ms,
            "tz": tz,
            "enabled_kinds": enabled_kinds or [],
            "budgets": budgets,
        }
        response = await self._post_json(
            "/api/v1/extract/entities",
            payload,
            operation="extract_entities",
            org_id=org_id,
        )
        body = response.json()
        if not body.get("success"):
            raise ExtractionClientError(message=body.get("error") or "Entity extraction failed")
        # The service can be a newer build than this one.
        extraction = read_stored_extraction(body.get("extraction"))
        if extraction is None:
            return NamedEntityExtraction(status="FAILED", termination_reason="llm_error")
        return extraction
