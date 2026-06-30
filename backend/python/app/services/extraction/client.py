"""HTTP client for the Extraction Service.

Usage::

    client = ExtractionClient(service_url="http://localhost:8093")
    classification = await client.classify(
        block_container=container,
        org_id="org-123",
        departments=["Engineering", "Finance"],
    )
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.models.blocks import BlocksContainer, SemanticMetadata
from app.services.base_client import BaseServiceClient, ServiceCallError


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
    ) -> None:
        super().__init__(
            service_url=service_url or os.getenv("EXTRACTION_SERVICE_URL", "http://localhost:8093"),
            service_name="ExtractionService",
            read_timeout=read_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    async def classify(
        self,
        block_container: BlocksContainer,
        org_id: str,
        departments: list[str] | None = None,
    ) -> SemanticMetadata | None:
        """Call ``POST /api/v1/extract/classify`` and return SemanticMetadata.

        Returns ``None`` when the LLM produced no classification (e.g. empty
        document).  Raises :class:`ExtractionClientError` on explicit failures.
        Raises :class:`ServiceCallError` on connection problems.
        """
        payload = {
            "block_container": block_container.model_dump(mode="json"),
            "org_id": org_id,
            "departments": departments or [],
        }

        response = await self._post_json(
            "/api/v1/extract/classify",
            payload,
            operation="classify",
        )

        body = response.json()

        if not body.get("success"):
            error_msg = body.get("error") or "Classification failed"
            raise ExtractionClientError(message=error_msg)

        classification_dict = body.get("classification")
        if classification_dict is None:
            return None

        return SemanticMetadata(**classification_dict)
