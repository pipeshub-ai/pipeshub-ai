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
import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.service import TokenScopes, config_node_constants
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.modules.transformers.document_extraction import (
    DEFAULT_CONTEXT_LENGTH,
    _classifies_as_code,
    classify_request_blocks,
)
from app.services.base_client import (
    BaseServiceClient,
    ServiceUnavailableError,
    parse_retry_after,
)
from app.services.llm_gateway import remote_usage
from app.services.llm_gateway.gateway import current_job_trace
from app.services.messaging.backpressure import get_default_backpressure_coordinator
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError

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

        The service has no record of its own, so *record_name* and *record_type*
        travel with the request: the type selects the code prompt (``CODE_FILE``)
        over the document prompt, and both give the document prompt its file
        context.
        """
        if _classifies_as_code(record_type):
            # The code prompt reads the file-summary block, each block's
            # code_metadata and the block groups (a class's docstring and its
            # INHERITS edges), all of which the trimmed document request drops.
            # The service still cuts the rendered file to its token budget.
            container = block_container.model_dump(mode="json")
        else:
            context_length_ceiling = await self._context_length_ceiling()
            container = {
                "blocks": classify_request_blocks(
                    block_container.blocks, context_length_ceiling
                ),
            }
        payload = {
            "block_container": container,
            "org_id": org_id,
            "departments": departments or [],
            "record_name": record_name,
            "record_type": record_type,
        }
        response = await self._post_for_job("/api/v1/extract/classify", payload, operation="classify", org_id=org_id)

        body = response.json()
        _note_remote_usage(body)

        if not body.get("success"):
            error_msg = body.get("error") or "Classification failed"
            if body.get("error_code") == LLMNotConfiguredError.code:
                raise LLMNotConfiguredError(error_msg)
            if body.get("error_code") == LLMUnavailableError.code:
                raise _unavailable_error(error_msg, body, response)
            raise ExtractionClientError(message=error_msg)

        classification_dict = body.get("classification")
        if classification_dict is None:
            return None

        return SemanticMetadata(**classification_dict)

    async def _post_for_job(self, path: str, payload: dict, *, operation: str, org_id: str) -> object:
        """POST a request that makes model calls for the caller's stage job. It carries the job, the sends the job has
        left and a request id kept across transport retries (``remote_usage``). When the reply is lost after the
        request may have been received, or the service says it already had it, the job is charged what the service
        recorded for it, or the whole allowance it was given when nothing is recorded; then the error goes on."""
        fields = _job_fields()
        try:
            # Never sent twice once it may have been received: the service would refuse the copy (it claims the
            # request id), and the job is charged for the first from what the service recorded.
            response = await self._post_json(
                path, {**payload, **fields}, operation=operation, org_id=org_id, retry_after_delivery=False
            )
        except ServiceUnavailableError as exc:
            if exc.delivered:
                await _charge_lost_reply(fields)
            raise
        if response.status_code >= HTTP_SERVER_ERROR:
            body = _json_or_none(response)
            if not (isinstance(body, dict) and "success" in body):
                # A 5xx that is not the service's own answer (a proxy's 502/503/504, no response body of the service's)
                # says nothing of what was spent: charge what the service recorded, or the whole allowance, and pause
                # the job like any lost reply. The service's own 5xx carries its usage and goes to the stage's retry.
                await _charge_lost_reply(fields)
                raise ServiceUnavailableError(
                    f"{self.service_name} {operation}: status {response.status_code} with no account of the work",
                    status_code=response.status_code,
                    service_name=self.service_name,
                    delivered=True,
                )
        if response.status_code == HTTP_CONFLICT:
            body = response.json()
            if isinstance(body, dict) and body.get("error_code") == REPLAYED:
                # A duplicate of a request already given (a redelivery, not a lost reply): charged what the first
                # spent, and retried on the stage's own retry path, which counts the attempt.
                await _charge_lost_reply(fields, body.get("usage"))
                raise ExtractionClientError(
                    message=f"{self.service_name} {operation}: the request was already delivered"
                )
        return response

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
        response = await self._post_for_job(
            "/api/v1/extract/entities", payload, operation="extract_entities", org_id=org_id
        )
        body = response.json()
        _note_remote_usage(body)
        if not body.get("success"):
            error_msg = body.get("error") or "Entity extraction failed"
            if body.get("error_code") == LLMUnavailableError.code:
                raise _unavailable_error(error_msg, body, response)
            raise ExtractionClientError(message=error_msg)
        # The service can be a newer build than this one.
        extraction = read_stored_extraction(body.get("extraction"))
        if extraction is None:
            return NamedEntityExtraction(status="FAILED", termination_reason="llm_error")
        return extraction


HTTP_CONFLICT = 409
HTTP_SERVER_ERROR = 500


def _json_or_none(response: object) -> object:
    try:
        return response.json()  # type: ignore[attr-defined]
    except ValueError:
        return None
REPLAYED = "REPLAYED"


def _job_fields() -> dict[str, object]:
    """The caller's stage job, the failed sends it has left and a name for this request, so the service admits and
    bounds its calls as that job and never spends twice for one request."""
    trace = current_job_trace()
    if trace is None:
        return {}
    from app.services.llm_gateway.gateway import current_job_key

    fields: dict[str, object] = {}
    if job_key := current_job_key():
        fields["job_key"] = job_key
        fields["request_id"] = uuid.uuid4().hex
    if (remaining := trace.meter.remaining()) is not None:
        fields["send_allowance"] = remaining
    return fields


async def _charge_lost_reply(fields: dict[str, object], usage: object = None) -> None:
    """Charge the job for a request whose reply it never got: what the service recorded, else everything it allowed."""
    trace = current_job_trace()
    if trace is None:
        return
    job_key, request_id = str(fields.get("job_key") or ""), str(fields.get("request_id") or "")
    if not isinstance(usage, dict) and job_key and request_id:
        usage = await remote_usage.read(job_key, request_id)
    if isinstance(usage, dict):
        trace.note_remote({**usage, "unavailable": max(1, int(usage.get("unavailable") or 0))})
        return
    allowance = fields.get("send_allowance")
    trace.note_remote({"failed": allowance if isinstance(allowance, int) and allowance > 0 else 1, "unavailable": 1})


def _note_remote_usage(body: object) -> None:
    """Add what the service spent for the caller's job to the job's own trace. A service too old to report ``usage``
    reports a provider outage in the 424 body alone, which is read the same way (a missing ``sent`` counts as a send)."""
    trace = current_job_trace()
    if trace is None or not isinstance(body, dict):
        return
    outage = body.get("error_code") == LLMUnavailableError.code
    usage = body.get("usage")
    if isinstance(usage, dict):
        unavailable = usage.get("unavailable")
        # A 424 is the provider being unavailable, whatever raised it behind the service.
        if outage and not (isinstance(unavailable, int) and unavailable > 0):
            usage = {**usage, "unavailable": 1}
        trace.note_remote(usage)
        return
    if not outage:
        return
    sent = body.get("sent") is not False
    requests = body.get("requests")
    trace.note_remote({
        "failed": requests if isinstance(requests, int) else (1 if sent else 0),
        "charged": charged if isinstance(charged := body.get("charged"), int) and charged >= 0
        else (1 if body.get("chargeable") and not body.get("trial") else 0),
        "trials": 1 if sent and body.get("trial") else 0,
        "unavailable": 1,
        "pk": body.get("pk"), "size_class": body.get("size_class"), "provider": body.get("provider"),
    })


def _unavailable_error(message: str, body: dict, response: object) -> LLMUnavailableError:
    """The service's provider outage (its 424), rebuilt for the caller's stage to pause on."""
    sent = body.get("sent")
    return LLMUnavailableError(
        message,
        # Set while the provider's circuit is open behind that service.
        retry_after=parse_retry_after(getattr(response, "headers", {}).get("Retry-After")),
        # A body that does not say counts as a send.
        sent=True if sent is None else bool(sent),
        trial=bool(body.get("trial")),
        chargeable=bool(body.get("chargeable")),
        charged=body.get("charged") if isinstance(body.get("charged"), int) else None,
        pk=body.get("pk"),
        size_class=body.get("size_class"),
        requests=body.get("requests"),
        provider=body.get("provider"),
    )
