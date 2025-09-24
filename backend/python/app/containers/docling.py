from dependency_injector import containers, providers  # type: ignore
from dotenv import load_dotenv  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.containers.container import BaseAppContainer
from app.containers.utils.utils import ContainerUtils
from app.utils.logger import create_logger

load_dotenv(override=True)


class DoclingAppContainer(BaseAppContainer):
    """Dependency injection container for the Docling service."""

    # Override logger with service-specific name
    logger = providers.Singleton(create_logger, "docling_service")
    container_utils = ContainerUtils()

    # Override config_service to use the service-specific logger
    key_value_store = providers.Singleton(Etcd3EncryptedKeyValueStore, logger=logger)
    config_service = providers.Singleton(ConfigurationService, logger=logger, key_value_store=key_value_store)

    # Docling-specific wiring configuration
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.docling_main",
            "app.services.docling.docling_service",
            "app.modules.parsers.pdf.docling",
            "app.utils.converters.docling_doc_to_blocks",
        ]
    )


async def initialize_container(container: DoclingAppContainer) -> bool:
    """Initialize container resources for Docling service"""
    logger = container.logger()
    logger.info("🚀 Initializing Docling service resources")

    try:
        # For Docling service, we mainly need configuration and logging
        # No database connections required for the Docling service itself
        logger.info("✅ Docling service configuration initialized")

        # Skip system health checks for Docling service as it doesn't need databases
        return True

    except Exception:
        logger.exception("❌ Failed to initialize Docling service resources")
        raise
