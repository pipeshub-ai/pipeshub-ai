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

logger = logging.getLogger("parsing_main")


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
    app_logger.info("✅ Parsing Service started — %d formats registered", len(app.state.parser_registry.list_all_formats()))

    yield

    app_logger.info("🔄 Parsing Service shutting down")
    try:
        config_service.close()
    except Exception:
        pass


app = FastAPI(
    title="PipesHub Parsing Service",
    description="Parses documents into BlocksContainer format",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(parsing_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    registry: ParserRegistry = app.state.parser_registry
    return JSONResponse(
        content={
            "status": "ok",
            "formats": list(registry.list_all_formats().keys()),
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PARSING_SERVICE_PORT", "8092"))
    uvicorn.run(
        "app.parsing_main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
