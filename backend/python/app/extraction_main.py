"""Extraction Service entry point.

Standalone FastAPI microservice (port 8093) that accepts a BlocksContainer
JSON and returns LLM-generated classification metadata (SemanticMetadata).

The service wraps the existing :class:`DocumentExtraction` logic.  It does
**not** need a graph connection because the indexing orchestrator pre-fetches
departments and passes them in the request body.
"""
import asyncio
import logging
import os
import signal
import sys
import types
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.containers.extraction import ExtractionAppContainer, initialize_container
from app.modules.transformers.document_extraction import DocumentExtraction
from app.api.routes.extraction import router as extraction_router

logger = logging.getLogger("extraction_main")


def handle_sigterm(signum: int, frame: types.FrameType | None) -> None:
    logger.info("Received signal %s; shutting down gracefully", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

container = ExtractionAppContainer.init("extraction_service")
container_lock = asyncio.Lock()


async def _get_initialized_container() -> ExtractionAppContainer:
    if not hasattr(_get_initialized_container, "initialized"):
        async with container_lock:
            if not hasattr(_get_initialized_container, "initialized"):
                await initialize_container(container)
                setattr(_get_initialized_container, "initialized", True)
    return container


class _NoOpGraphProvider:
    """Placeholder graph provider — the extraction service never calls graph methods."""

    async def get_departments(self, org_id: str) -> list[str]:
        return []

    async def get_document(self, doc_id: str, collection: str) -> dict | None:
        return None

    async def batch_upsert_nodes(self, docs: list, collection: str) -> bool:
        return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app_container = await _get_initialized_container()
    app.container = app_container  # type: ignore[attr-defined]

    config_service = app_container.config_service()
    app_logger = app_container.logger()

    # DocumentExtraction is constructed with a no-op graph provider because
    # the extraction service API accepts pre-fetched departments directly.
    app.state.document_extraction = DocumentExtraction(
        logger=app_logger,
        graph_provider=_NoOpGraphProvider(),
        config_service=config_service,
    )

    app_logger.info("✅ Extraction Service started")

    yield

    app_logger.info("🔄 Extraction Service shutting down")
    try:
        config_service.close()
    except Exception:
        pass


app = FastAPI(
    title="PipesHub Extraction Service",
    description="LLM-based document classification and metadata extraction",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(extraction_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("EXTRACTION_SERVICE_PORT", "8093"))
    uvicorn.run(
        "app.extraction_main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
