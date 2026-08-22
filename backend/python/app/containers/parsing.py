"""DI Container for the Parsing Service.

Wires together:
- Config / logger / key-value store (from BaseAppContainer)
- DoclingClient (for PDF via external Docling service)
- DoclingProcessor (local in-process Docling)
- PdfPlumberOpenCVProcessor
- OCRHandler
- Native parsers (CSV, Excel, Image, HTML, Markdown, SQL)
- ParserRegistry
"""
from __future__ import annotations

from dependency_injector import containers, providers  # type: ignore
from dotenv import load_dotenv  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.containers.container import BaseAppContainer
from app.utils.logger import create_logger

load_dotenv(override=True)


class ParsingAppContainer(BaseAppContainer):
    """DI container for the standalone Parsing Service."""

    logger = providers.Singleton(create_logger, "parsing_service")
    key_value_store = providers.Singleton(EncryptedKeyValueStore, logger=logger)
    config_service = providers.Singleton(
        ConfigurationService, logger=logger, key_value_store=key_value_store
    )

    # Wiring configuration — modules that use @inject / Provide[]
    wiring_config = containers.WiringConfiguration(modules=["app.parsing_main"])


async def initialize_container(container: ParsingAppContainer) -> bool:
    logger = container.logger()
    logger.info("🚀 Initializing Parsing Service resources")
    return True
