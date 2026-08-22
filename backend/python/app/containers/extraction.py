"""DI Container for the Extraction Service.

The extraction service wraps the existing DocumentExtraction logic.  It does
**not** need a graph provider connection because the orchestrator (indexing
service) pre-fetches departments and passes them in the request body.

A minimal graph provider stub is injected to satisfy the DocumentExtraction
constructor — department lookups are bypassed at the API layer by calling
the new ``classify()`` method instead of ``extract_metadata()``.
"""
from __future__ import annotations

from dependency_injector import containers, providers  # type: ignore
from dotenv import load_dotenv  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.containers.container import BaseAppContainer
from app.utils.logger import create_logger

load_dotenv(override=True)


class ExtractionAppContainer(BaseAppContainer):
    """DI container for the standalone Extraction Service."""

    logger = providers.Singleton(create_logger, "extraction_service")
    key_value_store = providers.Singleton(EncryptedKeyValueStore, logger=logger)
    config_service = providers.Singleton(
        ConfigurationService, logger=logger, key_value_store=key_value_store
    )

    wiring_config = containers.WiringConfiguration(modules=["app.extraction_main"])


async def initialize_container(container: ExtractionAppContainer) -> bool:
    logger = container.logger()
    logger.info("🚀 Initializing Extraction Service resources")
    return True
