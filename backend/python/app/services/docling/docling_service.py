import asyncio
import base64
import pickle
from typing import Optional

import uvicorn
from docling.datamodel.document import ConversionResult
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config.constants.http_status_code import HttpStatusCode
from app.models.blocks import BlocksContainer
from app.modules.parsers.pdf.docling import DoclingProcessor
from app.utils.logger import create_logger

PDF_PROCESSING_TIMEOUT_SECONDS = 40 * 60
PDF_PARSING_TIMEOUT_SECONDS = 40 * 60  # 10 minutes for parsing only
# ConfigService will be injected via dependency injection


class ProcessRequest(BaseModel):
    record_name: str
    pdf_binary: str  # base64 encoded PDF binary data
    org_id: Optional[str] = None


class ProcessResponse(BaseModel):
    success: bool
    block_containers: Optional[dict] = None
    error: Optional[str] = None


class ParseRequest(BaseModel):
    record_name: str
    pdf_binary: str  # base64 encoded PDF binary data


class ParseResponse(BaseModel):
    success: bool
    parse_result: Optional[str] = None  # base64 encoded pickled ConversionResult
    error: Optional[str] = None


class CreateBlocksRequest(BaseModel):
    parse_result: str  # base64 encoded pickled ConversionResult
    page_number: Optional[int] = None


class CreateBlocksResponse(BaseModel):
    success: bool
    block_containers: Optional[dict] = None
    error: Optional[str] = None


class DoclingService:
    def __init__(self, config_service=None, logger=None) -> None:
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

    async def process_pdf(self, record_name: str, pdf_binary: bytes) -> BlocksContainer:
        """Process PDF using DoclingProcessor (legacy method - does both parse and create blocks)"""
        try:
            self.logger.info(f"🚀 Processing PDF: {record_name}")
            if self.processor is None:
                raise RuntimeError("DoclingService not initialized: processor is None")
            result = await self.processor.load_document(record_name, pdf_binary)

            if result is False:
                raise ValueError("DoclingProcessor returned False - processing failed")

            self.logger.info(f"✅ Successfully processed PDF: {record_name}")
            return result

        except Exception as e:
            self.logger.error(f"❌ Error processing PDF {record_name}: {str(e)}")
            raise

    async def parse_pdf_only(self, record_name: str, pdf_binary: bytes) -> ConversionResult:
        """Parse PDF and return ConversionResult (no block creation, no LLM calls).
        
        This is phase 1 of two-phase processing.
        """
        try:
            self.logger.info(f"🚀 Parsing PDF (phase 1): {record_name}")
            if self.processor is None:
                raise RuntimeError("DoclingService not initialized: processor is None")
            
            conv_res = await self.processor.parse_document(record_name, pdf_binary)
            self.logger.info(f"✅ Successfully parsed PDF: {record_name}")
            return conv_res

        except Exception as e:
            self.logger.error(f"❌ Error parsing PDF {record_name}: {str(e)}")
            raise

    async def create_blocks_from_parse_result(
        self, conv_res: ConversionResult, page_number: int = None
    ) -> BlocksContainer:
        """Create blocks from ConversionResult (involves LLM calls for tables).
        
        This is phase 2 of two-phase processing.
        """
        try:
            self.logger.info("🚀 Creating blocks from parse result (phase 2)")
            if self.processor is None:
                raise RuntimeError("DoclingService not initialized: processor is None")
            
            block_containers = await self.processor.create_blocks(conv_res, page_number=page_number)
            
            if block_containers is False:
                raise ValueError("DoclingProcessor returned False - block creation failed")
            
            self.logger.info("✅ Successfully created blocks from parse result")
            return block_containers

        except Exception as e:
            self.logger.error(f"❌ Error creating blocks: {str(e)}")
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
docling_service: Optional[DoclingService] = None

def set_docling_service(service: DoclingService) -> None:
    """Wire an initialized DoclingService instance for the route handlers to use."""
    # Avoid using `global` assignment elsewhere; this function is the single writer
    globals()["docling_service"] = service

# FastAPI app
app = FastAPI(
    title="Docling Processing Service",
    description="Microservice for PDF processing using Docling",
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
async def health_check() -> dict:
    """Health check endpoint"""
    return {"status": "healthy", "service": "docling"}


@app.post("/process-pdf", response_model=ProcessResponse)
async def process_pdf_endpoint(request: ProcessRequest) -> ProcessResponse:
    """Process PDF document using Docling"""
    try:
        # Decode base64 PDF binary data
        try:
            pdf_binary = base64.b64decode(request.pdf_binary)
        except Exception as e:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Invalid base64 PDF data: {str(e)}"
            )

        # Ensure service is wired
        if docling_service is None:
            raise HTTPException(status_code=500, detail="Docling service not available")

        # Process the PDF with 40 minute timeout
        block_containers = await asyncio.wait_for(
            docling_service.process_pdf(
                request.record_name,
                pdf_binary
            ),
            timeout=PDF_PROCESSING_TIMEOUT_SECONDS  # 40 minutes in seconds
        )

        # Convert BlocksContainer to dict for JSON serialization
        # We'll need to implement a proper serialization method
        block_containers_dict = serialize_blocks_container(block_containers)


        return ProcessResponse(
            success=True,
            block_containers=block_containers_dict
        )

    except asyncio.TimeoutError:
        return ProcessResponse(
            success=False,
            error=f"Processing timed out after {PDF_PROCESSING_TIMEOUT_SECONDS} seconds"
        )
    except HTTPException:
        raise
    except Exception as e:
        return ProcessResponse(
            success=False,
            error=f"Processing failed: {str(e)}"
        )

def serialize_blocks_container(blocks_container: BlocksContainer) -> dict:
    """Serialize BlocksContainer to dictionary for JSON response"""
    try:
        # Convert to dict using the model's dict method.
        # If this fails, it indicates an issue with the Pydantic model definitions that should be fixed.
        return blocks_container.dict()
    except Exception as e:
        # Re-raise the exception to make the serialization issue visible and easier to debug.
        # A logger should be used here to capture the error details.
        raise TypeError(f"Failed to serialize BlocksContainer: {e}") from e


def serialize_conversion_result(conv_res: ConversionResult) -> str:
    """Serialize ConversionResult to base64-encoded pickle string"""
    try:
        pickled = pickle.dumps(conv_res)
        return base64.b64encode(pickled).decode('utf-8')
    except Exception as e:
        raise TypeError(f"Failed to serialize ConversionResult: {e}") from e


def deserialize_conversion_result(serialized: str) -> ConversionResult:
    """Deserialize base64-encoded pickle string to ConversionResult"""
    try:
        pickled = base64.b64decode(serialized)
        return pickle.loads(pickled)
    except Exception as e:
        raise TypeError(f"Failed to deserialize ConversionResult: {e}") from e


@app.post("/parse-pdf", response_model=ParseResponse)
async def parse_pdf_endpoint(request: ParseRequest) -> ParseResponse:
    """Parse PDF document (phase 1 - no block creation, no LLM calls)"""
    try:
        # Decode base64 PDF binary data
        try:
            pdf_binary = base64.b64decode(request.pdf_binary)
        except Exception as e:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Invalid base64 PDF data: {str(e)}"
            )

        # Ensure service is wired
        if docling_service is None:
            raise HTTPException(status_code=500, detail="Docling service not available")

        # Parse the PDF with timeout
        conv_res = await asyncio.wait_for(
            docling_service.parse_pdf_only(
                request.record_name,
                pdf_binary
            ),
            timeout=PDF_PARSING_TIMEOUT_SECONDS
        )

        # Serialize ConversionResult to base64-encoded pickle
        serialized_result = await asyncio.to_thread(serialize_conversion_result, conv_res)

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


@app.post("/create-blocks", response_model=CreateBlocksResponse)
async def create_blocks_endpoint(request: CreateBlocksRequest) -> CreateBlocksResponse:
    """Create blocks from parse result (phase 2 - involves LLM calls)"""
    try:
        # Deserialize the ConversionResult
        try:
            conv_res = await asyncio.to_thread(deserialize_conversion_result, request.parse_result)
        except Exception as e:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Invalid parse_result data: {str(e)}"
            )

        # Ensure service is wired
        if docling_service is None:
            raise HTTPException(status_code=500, detail="Docling service not available")

        # Create blocks with timeout
        block_containers = await asyncio.wait_for(
            docling_service.create_blocks_from_parse_result(
                conv_res,
                page_number=request.page_number
            ),
            timeout=PDF_PROCESSING_TIMEOUT_SECONDS
        )

        # Serialize BlocksContainer to dict
        block_containers_dict = serialize_blocks_container(block_containers)

        return CreateBlocksResponse(
            success=True,
            block_containers=block_containers_dict
        )

    except asyncio.TimeoutError:
        return CreateBlocksResponse(
            success=False,
            error=f"Block creation timed out after {PDF_PROCESSING_TIMEOUT_SECONDS} seconds"
        )
    except HTTPException:
        raise
    except Exception as e:
        return CreateBlocksResponse(
            success=False,
            error=f"Block creation failed: {str(e)}"
        )


def run(host: str = "0.0.0.0", port: int = 8081, reload: bool = False) -> None:
    """Run the Docling service"""
    uvicorn.run(
        "app.services.docling.docling_service:app",
        host=host,
        port=port,
        log_level="info",
        reload=reload
    )


if __name__ == "__main__":
    run(reload=False)
