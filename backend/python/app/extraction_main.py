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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import app.utils.runtime_threads  # must precede all ML library imports
from app.api.middlewares.body_limit import BodyLimitMiddleware
from app.api.routes.extraction import (
    CLASSIFY_PATH,
    MAX_CLASSIFY_REQUEST_BYTES,
)
from app.api.routes.extraction import router as extraction_router
from app.api.validation_errors import input_free_422
from app.containers.extraction import ExtractionAppContainer, initialize_container
from app.edition import check_runtime_settings
from app.edition_services import configure_app
from app.modules.transformers.document_extraction import DocumentExtraction
from app.services.llm_gateway.fleet_redis import bind_fleet_redis, bind_service_fleet
from app.services.llm_gateway.gateway import get_llm_gateway
from app.services.llm_gateway.wire import hook_status as wire_hook_status
from app.utils.env_utils import env_int
from app.utils.logger import install_log_redaction
from app.utils.process_hardening import mark_process_non_dumpable

logger = logging.getLogger("extraction_main")


def handle_sigterm(signum: int, frame: types.FrameType | None) -> None:
    logger.info("Received signal %s; shutting down gracefully", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

# Stop at load on a store setting this edition does not run.
check_runtime_settings()

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
    mark_process_non_dumpable()
    install_log_redaction()
    app_container = await _get_initialized_container()
    app.container = app_container  # type: ignore[attr-defined]

    config_service = app_container.config_service()
    app_logger = app_container.logger()

    # DocumentExtraction is constructed with a no-op graph provider because
    # the extraction service API accepts pre-fetched departments directly.
    app.state.entity_extraction_semaphore = asyncio.Semaphore(
        env_int("MAX_CONCURRENT_ENTITY_EXTRACTIONS", 8, lo=1)
    )
    app.state.document_extraction = DocumentExtraction(
        logger=app_logger,
        graph_provider=_NoOpGraphProvider(),
        config_service=config_service,
    )

    # The model providers' circuit breaker is the fleet's: shared with the indexing service through Redis, so every
    # process and pod sees the same outage.
    fleet_registry = await bind_service_fleet(config_service, app_logger)

    app_logger.info("✅ Extraction Service started")

    yield

    app_logger.info("🔄 Extraction Service shutting down")
    bind_fleet_redis(None)
    await fleet_registry.aclose()
    try:
        config_service.close()
    except Exception:
        pass


app = FastAPI(
    title="PipesHub Extraction Service",
    description="LLM-based document classification and metadata extraction",
    version="2.0.0",
    lifespan=lifespan,
    exception_handlers={RequestValidationError: input_free_422},
)
configure_app(app, "extraction")
# Refuse before the block container is parsed into memory (see the cap in the route module).
app.add_middleware(BodyLimitMiddleware, limits={CLASSIFY_PATH: MAX_CLASSIFY_REQUEST_BYTES})

app.include_router(extraction_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    content: dict[str, object] = {"status": "healthy", "service": "extraction"}
    try:
        # Classification calls the model through this process's gateway: its cap and breakers.
        content["llm_gateway"] = get_llm_gateway().stats()
        content["llm_wire_hooks"] = wire_hook_status()
    except Exception:
        # Logged, not returned: exception text does not belong in an unauthenticated response.
        logger.exception("Could not read the LLM gateway's stats")
        content["llm_gateway"] = {"error": "unavailable"}
    return JSONResponse(content=content)


if __name__ == "__main__":
    port = int(os.getenv("EXTRACTION_SERVICE_PORT", "8093"))
    uvicorn.run(
        "app.extraction_main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
