import asyncio
import logging
from typing import Any

from docling_core.types.doc.document import DoclingDocument
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.utils.logger import create_logger

PDF_PARSING_TIMEOUT_SECONDS = 40 * 60


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
) -> ParseResponse:
    """Parse PDF document. This is the only processing Docling performs - no
    block construction and no LLM calls, keeping the service stateless."""
    try:
        pdf_binary = await file.read()

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
