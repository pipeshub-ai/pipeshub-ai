"""Parsing Service entry point.

Standalone FastAPI microservice (port 8092) that accepts file bytes and
returns a ``BlocksContainer`` JSON.  All parser implementations live in
``app/services/parsing/providers/``.  The :class:`ParserRegistry` maps
(format_key, provider) to the correct :class:`IParser`.
"""
import app.utils.runtime_threads  # noqa: E402 - must precede ML imports

import asyncio
import logging
import os
import signal
import sys
import types
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.markdown.docling_markdown_parser import DoclingMarkdownParser
from app.modules.parsers.markdown.mdx_parser import MDXParser
from app.modules.parsers.blocks.blocks_parser import BlocksParser
from app.modules.parsers.docx.docparser import DocParser
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.pptx.ppt_parser import PPTParser
from app.modules.parsers.yaml.yaml_parser import YAMLParser
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config.configuration_service import ConfigurationService
from app.containers.parsing import ParsingAppContainer, initialize_container
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.xls_parser import XLSParser
from app.modules.parsers.html_parser.html_parser import HTMLParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)
from app.modules.parsers.sql.sql_table_parser import SQLTableParser
from app.modules.parsers.sql.sql_view_parser import SQLViewParser
from app.services.docling.client import DoclingClient
from app.services.parsing.interface import ParserProvider
from app.services.parsing.providers.docling_service_parser import DoclingServiceParser
from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
from app.services.parsing.providers.ocr_parser import OCRParser
from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser
from app.services.parsing.registry import ParserRegistry
from app.api.routes.parsing import router as parsing_router
from app.config.constants.ai_models import OCRProvider
from app.services.parsing.concurrency import (
    ParseTier,
    compute_parse_slots,
    get_memory_limit_bytes,
)

logger = logging.getLogger("parsing_main")

# Headroom on top of the combined heavy+light slot count so a request's own
# sequential to_thread hops (e.g. LibreOffice write, then Excel/CSV parse)
# don't starve for a slot while another request is mid-parse.
PARSE_THREAD_POOL_HEADROOM = 4


def handle_sigterm(signum: int, frame: types.FrameType | None) -> None:
    logger.info("Received signal %s; shutting down gracefully", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

container = ParsingAppContainer.init("parsing_service")
container_lock = asyncio.Lock()


async def _get_initialized_container() -> ParsingAppContainer:
    if not hasattr(_get_initialized_container, "initialized"):
        async with container_lock:
            if not hasattr(_get_initialized_container, "initialized"):
                await initialize_container(container)
                setattr(_get_initialized_container, "initialized", True)
    return container


def _build_registry(config_service: ConfigurationService, app_logger: logging.Logger) -> ParserRegistry:
    """Build and configure the :class:`ParserRegistry` with all providers."""

    registry = ParserRegistry()

    # ----------------------------------------------------------------
    # Shared parser instances
    # ----------------------------------------------------------------
    docling_client = DoclingClient(
        service_url=os.getenv("DOCLING_SERVICE_URL", "http://localhost:8081")
    )
    docling_processor = DoclingProcessor(logger=app_logger, config=config_service)
    
    image_parser = ImageParser(app_logger)
    default_html_parser = SelectolaxHtmlParser()
    docling_html_parser = DoclingHtmlParser()
    csv_parser = CSVParser(config_service=config_service)
    tsv_parser = CSVParser(config_service=config_service, delimiter="\t")
    excel_parser = ExcelParser(app_logger, config_service)
    xls_parser = XLSParser(excel_parser)
    sql_table_parser = SQLTableParser()
    sql_view_parser = SQLViewParser()

    default_md_parser = MarkdownItParser()
    docling_md_parser = DoclingMarkdownParser(app_logger, config_service)

    default_mdx_parser = MDXParser(default_md_parser)
    docling_mdx_parser = MDXParser(docling_md_parser)

    # Build provider instances
    docling_svc_parser = DoclingServiceParser(docling_client)
    local_docling_parser = LocalDoclingParser(docling_processor)
    doc_parser = DocParser(local_docling_parser)
    ppt_parser = PPTParser(local_docling_parser)
    pdfplumber_parser = PdfPlumberParser(PDFPlumberOpenCVProcessor(app_logger, config_service))

    from app.modules.parsers.pdf.ocr_handler import OCRHandler  # noqa: PLC0415
    ocr_handler = OCRHandler(app_logger, OCRProvider.VLM_OCR.value, config=config_service)
    default_ocr_parser = OCRParser(ocr_handler, docling_md_parser)
    docling_ocr_parser = OCRParser(ocr_handler, docling_md_parser)
    
    registry.register("blocks", ParserProvider.DEFAULT, BlocksParser(app_logger, config_service))

    # ----------------------------------------------------------------
    # JSON / YAML — deterministic, schema-aware natural-language chunking
    # ----------------------------------------------------------------
    json_parser = JSONParser()
    registry.register("json", ParserProvider.DEFAULT, json_parser)
    registry.register("yaml", ParserProvider.DEFAULT, YAMLParser(json_parser))
    # ----------------------------------------------------------------
    # PDF
    # ----------------------------------------------------------------
    registry.register("pdf", ParserProvider.DOCLING, SmartPDFParser(docling_svc_parser, docling_ocr_parser))
    registry.register("pdf", ParserProvider.DEFAULT, SmartPDFParser(docling_svc_parser, default_ocr_parser))

    # ----------------------------------------------------------------
    # DOCX / DOC — local Docling handles these in-process
    # ----------------------------------------------------------------
    registry.register("docx", ParserProvider.DOCLING, local_docling_parser)
    registry.register("docx", ParserProvider.DEFAULT, local_docling_parser)
    registry.register("doc", ParserProvider.DOCLING, doc_parser)
    registry.register("doc", ParserProvider.DEFAULT, doc_parser)


    # ----------------------------------------------------------------
    # PPTX / PPT
    # ----------------------------------------------------------------
    registry.register("pptx", ParserProvider.DOCLING, local_docling_parser)
    registry.register("pptx", ParserProvider.DEFAULT, local_docling_parser)
    registry.register("ppt", ParserProvider.DOCLING, ppt_parser)
    registry.register("ppt", ParserProvider.DEFAULT, ppt_parser)
    
    # ----------------------------------------------------------------
    # TXT
    # ----------------------------------------------------------------
    registry.register("txt", ParserProvider.DEFAULT, default_md_parser)
    registry.register("txt", ParserProvider.DOCLING, docling_md_parser)
    # ----------------------------------------------------------------
    # MDX
    # ----------------------------------------------------------------
    registry.register("mdx", ParserProvider.DEFAULT, default_mdx_parser)
    registry.register("mdx", ParserProvider.DOCLING, docling_mdx_parser)

    registry.register("md", ParserProvider.DEFAULT, default_md_parser)
    registry.register("md", ParserProvider.DOCLING, docling_md_parser)

    # ----------------------------------------------------------------
    # HTML
    # ----------------------------------------------------------------
    registry.register("html", ParserProvider.DEFAULT, default_html_parser)
    registry.register("html", ParserProvider.DOCLING, docling_html_parser)

    # ----------------------------------------------------------------
    # CSV / TSV
    # ----------------------------------------------------------------
    registry.register("csv", ParserProvider.DEFAULT, csv_parser)
    registry.register("tsv", ParserProvider.DEFAULT, tsv_parser)

    # ----------------------------------------------------------------
    # Excel
    # ----------------------------------------------------------------
    registry.register("xlsx", ParserProvider.DEFAULT, excel_parser)
    registry.register("xls", ParserProvider.DEFAULT, xls_parser)

    # ----------------------------------------------------------------
    # Images
    # ----------------------------------------------------------------
    for fmt in ("png", "jpg", "jpeg", "webp", "svg", "heic", "heif"):
        registry.register(fmt, ParserProvider.DEFAULT, image_parser)

    # ----------------------------------------------------------------
    # SQL tables / views
    # ----------------------------------------------------------------
    registry.register("sql_table", ParserProvider.DEFAULT, sql_table_parser)
    registry.register("sql_view", ParserProvider.DEFAULT, sql_view_parser)

    return registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app_container = await _get_initialized_container()
    app.container = app_container  # type: ignore[attr-defined]

    config_service = app_container.config_service()
    app_logger = app_container.logger()

    app.state.parser_registry = _build_registry(config_service, app_logger)

    # ------------------------------------------------------------------
    # Concurrency gates: two isolated pools so a heavy PDF/OCR parse can
    # never queue behind (or starve) a fast markdown/CSV/JSON parse. Sizes
    # are auto-derived from CPU + memory limits (cgroup-aware, falling back
    # to psutil for native macOS/Windows/Linux runs) — see
    # app/services/parsing/concurrency.py. MAX_CONCURRENT_PARSING remains
    # the one operator override, pinning the heavy pool directly.
    # ------------------------------------------------------------------
    cpu_count = os.cpu_count() or 1
    mem_limit_bytes = get_memory_limit_bytes()
    heavy_slots, light_slots = compute_parse_slots(
        cpu_count=cpu_count,
        mem_limit_bytes=mem_limit_bytes,
        override=os.getenv("MAX_CONCURRENT_PARSING"),
    )

    app.state.parse_gates = {
        ParseTier.HEAVY: asyncio.Semaphore(heavy_slots),
        ParseTier.LIGHT: asyncio.Semaphore(light_slots),
    }
    app.state.parse_gate_slots = {
        ParseTier.HEAVY: heavy_slots,
        ParseTier.LIGHT: light_slots,
    }

    total_slots = heavy_slots + light_slots
    thread_pool_size = total_slots + PARSE_THREAD_POOL_HEADROOM
    executor = ThreadPoolExecutor(
        max_workers=thread_pool_size,
        thread_name_prefix="parsing-worker",
    )
    asyncio.get_running_loop().set_default_executor(executor)
    app.state.parse_executor = executor

    mem_limit_gib = f"{mem_limit_bytes / (1024**3):.1f}GiB" if mem_limit_bytes else "unknown"
    app_logger.info(
        "✅ Parsing Service started — %d formats registered | "
        "heavy_slots=%d light_slots=%d (cpu_count=%d, mem_limit=%s, "
        "MAX_CONCURRENT_PARSING=%s) | thread_pool_size=%d | "
        "LOCAL_DOCLING_PARSE_WORKERS=%s | PDF_RASTER_WORKERS=%s | PARSING_UVICORN_WORKERS=%s",
        len(app.state.parser_registry.list_all_formats()),
        heavy_slots,
        light_slots,
        cpu_count,
        mem_limit_gib,
        os.getenv("MAX_CONCURRENT_PARSING", "auto"),
        thread_pool_size,
        os.getenv("LOCAL_DOCLING_PARSE_WORKERS", "1"),
        os.getenv("PDF_RASTER_WORKERS", "auto"),
        os.getenv("PARSING_UVICORN_WORKERS", "1"),
    )

    yield

    app_logger.info("🔄 Parsing Service shutting down")
    executor.shutdown(wait=False, cancel_futures=True)
    try:
        config_service.close()
    except Exception:
        pass


from app.api.middlewares.request_context import RequestContextMiddleware
from app.utils.request_context import set_service_suffix

set_service_suffix("-ps")

app = FastAPI(
    title="PipesHub Parsing Service",
    description="Parses documents into BlocksContainer format",
    version="2.0.0",
    lifespan=lifespan,
)

# Trace context — outermost, so log lines correlate with the caller's request.
app.add_middleware(RequestContextMiddleware)

app.include_router(parsing_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint. ``status`` uses the "healthy"/"unhealthy" values
    that every other service (indexing, docling, query) reports, so the
    Node.js aggregator (health.routes.ts) and Docker healthcheck can treat
    parsing the same way as the rest of the fleet instead of special-casing
    an "ok" status that only this service used to return.
    """
    registry: ParserRegistry = app.state.parser_registry
    return JSONResponse(
        content={
            "status": "healthy",
            "formats": list(registry.list_all_formats().keys()),
        }
    )


def run(host: str = "0.0.0.0", port: int | None = None, workers: int | None = None, *, reload: bool = False) -> None:
    """Run the Parsing Service.

    ``PARSING_UVICORN_WORKERS`` (default 1) scales the service across
    multiple processes for CPU headroom beyond the in-process concurrency
    gates (auto-sized heavy/light pools — see
    ``app.services.parsing.concurrency.compute_parse_slots``). Effective
    service-wide capacity is ``PARSING_UVICORN_WORKERS x (heavy_slots +
    light_slots)``.
    """
    port = port or int(os.getenv("PARSING_SERVICE_PORT", "8092"))
    workers = workers or max(1, int(os.getenv("PARSING_UVICORN_WORKERS", "1")))
    if reload and workers > 1:
        logger.warning(
            "PARSING_UVICORN_WORKERS>1 is not compatible with reload=True; falling back to 1 worker."
        )
        workers = 1
    # Uvicorn's own startup banner only goes to the console, not our log
    # files; log it ourselves so it shows up wherever parsing_main's logger
    # is configured to write (see app/utils/logger.py).
    logger.info(
        "🚀 Parsing Service listening on %s:%d (workers=%d, reload=%s)",
        host, port, workers, reload,
    )
    uvicorn.run(
        "app.parsing_main:app",
        host=host,
        port=port,
        log_level="info",
        reload=reload,
        workers=workers,
    )


if __name__ == "__main__":
    run()
