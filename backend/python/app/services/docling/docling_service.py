import base64
import sys
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config.constants.http_status_code import HttpStatusCode

# Add the app directory to Python path for imports
sys.path.append('/app')

from app.models.blocks import BlocksContainer
from app.modules.parsers.pdf.docling import DoclingProcessor
from app.utils.logger import create_logger

# ConfigService will be injected via dependency injection


class ProcessRequest(BaseModel):
    record_name: str
    pdf_binary: str  # base64 encoded PDF binary data
    org_id: Optional[str] = None


class ProcessResponse(BaseModel):
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
        """Process PDF using DoclingProcessor"""
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

        # Process the PDF
        block_containers = await docling_service.process_pdf(
            request.record_name,
            pdf_binary
        )

        # Convert BlocksContainer to dict for JSON serialization
        # We'll need to implement a proper serialization method
        block_containers_dict = await serialize_blocks_container(block_containers)

        return ProcessResponse(
            success=True,
            block_containers=block_containers_dict
        )

    except HTTPException:
        raise
    except Exception as e:
        return ProcessResponse(
            success=False,
            error=f"Processing failed: {str(e)}"
        )


async def serialize_blocks_container(blocks_container: BlocksContainer) -> dict:
    """Serialize BlocksContainer to dictionary for JSON response"""
    try:
        # Convert to dict using the model's dict method
        result = blocks_container.dict()
        return result
    except Exception:
        # Fallback serialization if dict() method doesn't work
        return {
            "blocks": [
                {
                    "id": block.id,
                    "index": block.index,
                    "type": block.type.value if hasattr(block.type, 'value') else str(block.type),
                    "format": block.format.value if hasattr(block.format, 'value') else str(block.format),
                    "data": block.data,
                    "parent_index": block.parent_index,
                    "source_id": block.source_id,
                    "citation_metadata": {
                        "page_number": block.citation_metadata.page_number if block.citation_metadata else None,
                        "bounding_boxes": [
                            {"x": bbox.x, "y": bbox.y} for bbox in (block.citation_metadata.bounding_boxes or [])
                        ] if block.citation_metadata else []
                    } if block.citation_metadata else None,
                    "image_metadata": {
                        "captions": block.image_metadata.captions if block.image_metadata else [],
                        "footnotes": block.image_metadata.footnotes if block.image_metadata else []
                    } if block.image_metadata else None
                } for block in blocks_container.blocks
            ],
            "block_groups": [
                {
                    "index": group.index,
                    "name": group.name,
                    "type": group.type.value if hasattr(group.type, 'value') else str(group.type),
                    "parent_index": group.parent_index,
                    "description": group.description,
                    "source_group_id": group.source_group_id,
                    "format": group.format.value if hasattr(group.format, 'value') else str(group.format),
                    "data": group.data,
                    "children": [
                        {
                            "block_index": child.block_index
                        } for child in group.children
                    ],
                    "table_metadata": {
                        "num_of_rows": group.table_metadata.num_of_rows,
                        "num_of_cols": group.table_metadata.num_of_cols,
                        "captions": group.table_metadata.captions,
                        "footnotes": group.table_metadata.footnotes
                    } if group.table_metadata else None,
                    "citation_metadata": {
                        "page_number": group.citation_metadata.page_number if group.citation_metadata else None,
                        "bounding_boxes": [
                            {"x": bbox.x, "y": bbox.y} for bbox in (group.citation_metadata.bounding_boxes or [])
                        ] if group.citation_metadata else []
                    } if group.citation_metadata else None
                } for group in blocks_container.block_groups
            ]
        }


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
