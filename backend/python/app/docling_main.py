import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config.constants.http_status_code import HttpStatusCode
from app.containers.docling import DoclingAppContainer, initialize_container
from app.services.docling.docling_service import (
    DoclingService,
    set_docling_service,
)
from app.services.docling.docling_service import (
    app as docling_app,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


# Only for development/debugging
def handle_sigterm(signum, frame) -> None:
    print(f"Received signal {signum}, {frame} shutting down gracefully")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

# Initialize container like other services
container = DoclingAppContainer.init("docling_service")
container_lock = asyncio.Lock()

async def get_initialized_container() -> DoclingAppContainer:
    """Dependency provider for initialized container"""
    if not hasattr(get_initialized_container, "initialized"):
        async with container_lock:
            if not hasattr(get_initialized_container, "initialized"):
                await initialize_container(container)
                container.wire(modules=["app.services.docling.docling_service"])
                get_initialized_container.initialized = True
    return container

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""

    # Initialize container and Docling service
    try:
        app_container = await get_initialized_container()
        app.container = app_container

        # Initialize Docling service with proper dependencies
        config_service = app_container.config_service()
        logger = app_container.logger()
        app.state.docling_service = DoclingService(
            config_service=config_service, logger=logger
        )
        await app.state.docling_service.initialize()
        # Wire the initialized instance into the mounted routes
        set_docling_service(app.state.docling_service)
    except Exception as e:
        logger.error(f"❌ Failed to initialize Docling service: {str(e)}")
        raise

    yield

    # Shutdown
    logger.info("🔄 Shutting down Docling service")


app = FastAPI(
    lifespan=lifespan,
    title="Docling Processing Service",
    description="Microservice for PDF processing using Docling",
    version="1.0.0",
)

# Mount the Docling service routes
app.mount("/", docling_app)

@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint"""
    try:
        # Check if Docling service is healthy
        svc = getattr(app.state, "docling_service", None)
        if not svc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "service": "docling",
                    "error": "DoclingService not initialized",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

        # Check if service has health_check method and call it
        if not hasattr(svc, "health_check"):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "service": "docling",
                    "error": "DoclingService does not have health_check method",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

        # Call the health check method
        is_healthy = await svc.health_check()

        if is_healthy:
            return JSONResponse(
                status_code=HttpStatusCode.SUCCESS.value,
                content={
                    "status": "healthy",
                    "service": "docling",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        else:
            return JSONResponse(
                status_code=HttpStatusCode.UNHEALTHY.value,
                content={
                    "status": "unhealthy",
                    "service": "docling",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "error": str(e),
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )

def run(host: str = "0.0.0.0", port: int = 8081, reload: bool = False) -> None:
    """Run the Docling service"""
    uvicorn.run(
        "app.docling_main:app", host=host, port=port, log_level="info", reload=reload
    )

if __name__ == "__main__":
    run(reload=False)
