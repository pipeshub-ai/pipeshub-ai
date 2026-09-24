import asyncio
import logging
from typing import Any

from docling_core.types.doc.document import DoclingDocument
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.services.resource_governor import (
    ParseTier,
    ResourceGovernor,
    acquire_gate_with_backpressure,
    gate_pool,
    parse_cost,
)
from app.utils.logger import create_logger

logger = logging.getLogger(__name__)

PDF_PROCESSING_TIMEOUT_SECONDS = 40 * 60
PDF_PARSING_TIMEOUT_SECONDS = 40 * 60

# Docling only ever handles PDFs here — always the heavy tier — so requests
# share the same HEAVY_PARSE pool the Parsing Service gates, sized by this
# process's own governor (see docling_main.py's lifespan). Mirrors
# api/routes/parsing.py's constants; see admission.py for why the wait is
# split into a short warn threshold and a much longer give-up timeout.
DOCLING_QUEUE_WAIT_WARN_SECONDS = 5.0
# Deliberately much smaller than the Parsing Service's 120s: this wait sits
# in front of Docling's own PDF_PROCESSING_TIMEOUT_SECONDS (2400s) budget,
# and DoclingClient's request timeout only has a fixed margin over that (see
# services/docling/client.py) — a large admission wait here would eat
# directly into that margin instead of being "free" queuing time.
DOCLING_GATE_TIMEOUT_SECONDS = 30.0
DOCLING_BACKPRESSURE_RETRY_AFTER_SECONDS = 5


class ParseResponse(BaseModel):
    success: bool
    parse_result: str | None = None  # JSON-encoded document data
    error: str | None = None


class DoclingService:
    def __init__(self, config_service: object | None = None, logger: logging.Logger | None = None) -> None:
        self.logger = logger or create_logger(__name__)
        self.config_service = config_service
        self.processor = None

    async def initialize(self) -> None:
        """Initialize the service with configuration"""
        try:
            # Allow external wiring to provide config_service. If not provided,
            # skip initialization and let the caller wire it later.
            if not self.config_service:
                raise ValueError("Config service not provided")

            # Initialize DoclingProcessor
            self.processor = DoclingProcessor(
                logger=self.logger,
                config=self.config_service
            )

            self.logger.info("✅ Docling service initialized successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Docling service: {str(e)}")
            raise

    async def parse_pdf_only(
        self,
        record_name: str,
        pdf_binary: bytes,
        page_range: tuple[int, int] | None = None,
    ) -> DoclingDocument:
        """Parse PDF and return DoclingDocument (no block creation, no LLM calls).

        Args:
            page_range: Optional 1-based inclusive (start, end) page range.
        """
        try:
            self.logger.info(f"🚀 Parsing PDF (phase 1): {record_name}")
            if self.processor is None:
                raise RuntimeError("DoclingService not initialized: processor is None")

            doc = await self.processor.parse_document(
                record_name, pdf_binary, page_range=page_range
            )
            self.logger.info(f"✅ Successfully parsed PDF: {record_name}")
            return doc

        except Exception as e:
            self.logger.error(f"❌ Error parsing PDF {record_name}: {str(e)}")
            raise

    async def health_check(self) -> bool:
        """Check if the Docling service is healthy"""
        try:
            # Check if service is properly initialized
            if self.processor is None:
                self.logger.warning("⚠️ DoclingService not initialized: processor is None")
                return False

            # Additional health checks can be added here
            # For now, just check if the processor exists
            return True
        except Exception as e:
            self.logger.error(f"❌ Health check failed: {str(e)}")
            return False


# Global service instance (to be set by the application wiring)
docling_service: DoclingService | None = None

def set_docling_service(service: DoclingService) -> None:
    """Wire an initialized DoclingService instance for the route handlers to use."""
    # Avoid using `global` assignment elsewhere; this function is the single writer
    globals()["docling_service"] = service


# Global resource governor (to be set by app.docling_main's lifespan). This
# module is mounted as a sub-app (see docling_main.py's app.mount("/", ...)),
# so it has its own FastAPI `app.state` distinct from the outer app's —
# the module-level singleton mirrors `docling_service` above for the same
# reason: route handlers here can't reach the outer app's state.
_resource_governor: ResourceGovernor | None = None


def set_resource_governor(governor: ResourceGovernor) -> None:
    """Wire an initialized ResourceGovernor for the route handlers to use."""
    globals()["_resource_governor"] = governor


async def _acquire_docling_gate(pdf_binary: bytes, message_id: str) -> tuple[bool, int]:
    """Acquire a HEAVY_PARSE permit sized to *pdf_binary*, if a governor has
    been wired. Returns ``(admitted, cost)`` — ``cost`` is 0 whenever no
    permit was actually taken (no governor configured, standalone/test runs
    that skip governor wiring, or the gate denied admission) so callers can
    release unconditionally without an extra guard: ``AdmissionGate.release``
    subtracts ``cost`` straight from ``in_use``, and a denied acquire never
    incremented it, so releasing a non-zero cost here would under-count
    in-flight work and let in more concurrent parses than the limit allows."""
    if _resource_governor is None:
        return True, 0
    cost = parse_cost(ParseTier.HEAVY, len(pdf_binary))
    gate = _resource_governor.gate(gate_pool(ParseTier.HEAVY))
    admitted = await acquire_gate_with_backpressure(
        gate, cost, ParseTier.HEAVY, message_id,
        logger=logger,
        log_prefix="docling",
        queue_wait_warn_seconds=DOCLING_QUEUE_WAIT_WARN_SECONDS,
        gate_timeout_seconds=DOCLING_GATE_TIMEOUT_SECONDS,
    )
    return admitted, (cost if admitted else 0)


def _release_docling_gate(cost: int) -> None:
    if _resource_governor is None or cost == 0:
        return
    _resource_governor.gate(gate_pool(ParseTier.HEAVY)).release(cost)


def _backpressure_response() -> JSONResponse:
    """Build the 429 backpressure response shared by the conversion routes.

    A raw ``JSONResponse`` (FastAPI allows any handler to return a
    ``Response`` directly, bypassing its declared ``response_model``) since
    none of ``ProcessResponse``/``ParseResponse`` model a structured error
    code — ``DoclingClient._post_with_retry`` only needs the status code and
    a parseable ``Retry-After`` header to treat this as backpressure rather
    than failure, independent of the body shape.
    """
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(DOCLING_BACKPRESSURE_RETRY_AFTER_SECONDS)},
        content={
            "success": False,
            "error": "PARSE_BACKPRESSURE: Docling service is at capacity; retry later.",
        },
    )

# FastAPI app
app = FastAPI(
    title="Docling Processing Service",
    description="Microservice for PDF parsing using Docling",
    version="1.0.0"
)


@app.on_event("startup")
async def startup_event() -> None:
    """Initialize the service on startup when running this module standalone.
    When mounted by an external app (e.g., app.docling_main), the external app
    should wire and initialize the service via set_docling_service().
    """
    if docling_service is None:
        # If not wired by external app yet, skip initialization quietly
        return
    if getattr(docling_service, "processor", None) is None:
        await docling_service.initialize()


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint"""
    return {"status": "healthy", "service": "docling"}


def serialize_docling_doc(doc: DoclingDocument) -> str:
    """Serialize DoclingDocument to JSON string."""
    try:
        return doc.model_dump_json()
    except Exception as e:
        raise TypeError(f"Failed to serialize DoclingDocument: {e}") from e


@app.post("/parse-pdf", response_model=ParseResponse)
async def parse_pdf_endpoint(
    file: UploadFile = File(...),
    record_name: str = Form(...),
    start_page: int | None = Form(default=None),
    end_page: int | None = Form(default=None),
) -> ParseResponse | JSONResponse:
    """Parse PDF document (phase 1 - no block creation, no LLM calls)"""
    cost = 0
    try:
        pdf_binary = await file.read()
        admitted, cost = await _acquire_docling_gate(pdf_binary, record_name)
        if not admitted:
            return _backpressure_response()

        if docling_service is None:
            raise HTTPException(status_code=500, detail="Docling service not available")

        page_range = None
        if start_page is not None and end_page is not None:
            page_range = (start_page, end_page)

        doc = await asyncio.wait_for(
            docling_service.parse_pdf_only(
                record_name, pdf_binary, page_range=page_range
            ),
            timeout=PDF_PARSING_TIMEOUT_SECONDS
        )

        serialized_result = await asyncio.to_thread(serialize_docling_doc, doc)

        return ParseResponse(
            success=True,
            parse_result=serialized_result
        )

    except asyncio.TimeoutError:
        return ParseResponse(
            success=False,
            error=f"Parsing timed out after {PDF_PARSING_TIMEOUT_SECONDS} seconds"
        )
    except HTTPException:
        raise
    except Exception as e:
        return ParseResponse(
            success=False,
            error=f"Parsing failed: {str(e)}"
        )
    finally:
        _release_docling_gate(cost)
