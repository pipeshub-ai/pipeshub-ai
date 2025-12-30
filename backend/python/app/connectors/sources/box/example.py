"""
Example usage of the Box connector.

This file demonstrates how to initialize and use the Box connector
to sync files, folders, users, and groups from a Box account.
"""

import asyncio
import logging

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.sources.box.connector import BoxConnector


async def example_box_sync() -> None:
    """
    Example function demonstrating how to use the Box connector.
    """
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Initialize required services (these would typically be dependency injected)
    config_service = ConfigurationService()  # Your config service instance
    data_store_provider = DataStoreProvider()  # Your data store provider instance
    data_entities_processor = DataSourceEntitiesProcessor(
        org_id="your-org-id",
        data_store_provider=data_store_provider,
        logger=logger
    )

    # Create Box connector instance
    box_connector = BoxConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service
    )

    try:
        # Initialize the connector
        logger.info("Initializing Box connector...")
        initialized = await box_connector.init()

        if not initialized:
            logger.error("Failed to initialize Box connector")
            return

        # Test connection
        logger.info("Testing Box connection...")
        connection_ok = await box_connector.test_connection_and_access()

        if not connection_ok:
            logger.error("Box connection test failed")
            return

        logger.info("Box connection test successful!")

        # Run full sync
        logger.info("Starting full Box sync...")
        await box_connector.run_sync()
        logger.info("Full Box sync completed!")

        # Optional: Run incremental sync
        # logger.info("Starting incremental Box sync...")
        # await box_connector.run_incremental_sync()
        # logger.info("Incremental Box sync completed!")

    except Exception as e:
        logger.error(f"Error during Box sync: {e}", exc_info=True)
    finally:
        # Cleanup
        await box_connector.cleanup()
        logger.info("Box connector cleanup completed")


async def example_box_sync_with_config() -> None:
    """
    Example showing how to configure Box connector with specific settings.
    """
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Example configuration structure expected by the connector
    box_config = {
        "credentials": {
            "access_token": "your-box-access-token-here",
        },
        "auth": {
            "clientId": "your-box-client-id",
            "clientSecret": "your-box-client-secret",
        }
    }

    logger.info("Example Box configuration:")
    logger.info(f"Config structure: {box_config}")
    logger.info("\nTo use the Box connector:")
    logger.info("1. Create a Box app at https://developer.box.com")
    logger.info("2. Configure OAuth 2.0 authentication")
    logger.info("3. Enable the required scopes in Box Developer Console:")
    logger.info("   - Read and write all files and folders (required)")
    logger.info("   - Manage users (optional, for user sync)")
    logger.info("   - Manage groups (optional, for group sync)")
    logger.info("4. Store credentials in your configuration service")
    logger.info("5. Run the connector with: await box_connector.run_sync()")


async def example_webhook_handling() -> None:
    """
    Example showing how to handle Box webhooks.
    """
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Example webhook payload from Box
    webhook_payload = {
        "type": "webhook_event",
        "trigger": "FILE.UPLOADED",
        "source": {
            "id": "12345",
            "type": "file",
            "name": "example.pdf"
        },
        "created_by": {
            "type": "user",
            "id": "67890",
            "name": "John Doe",
            "login": "john@example.com"
        },
        "created_at": "2025-12-09T10:00:00-08:00"
    }

    logger.info("Example Box webhook payload:")
    logger.info(webhook_payload)
    logger.info("\nWebhook triggers supported:")
    logger.info("- FILE.UPLOADED")
    logger.info("- FILE.DELETED")
    logger.info("- FILE.MOVED")
    logger.info("- FOLDER.CREATED")
    logger.info("- FOLDER.DELETED")
    logger.info("- FOLDER.MOVED")
    logger.info("\nTo handle webhooks:")
    logger.info("1. Configure webhook URL in Box Developer Console")
    logger.info("2. Verify webhook signatures for security")
    logger.info("3. Call box_connector.handle_webhook_notification(webhook_payload)")


if __name__ == "__main__":
    # Run the example
    print("Box Connector Example Usage\n")
    print("=" * 50)
    print("\nChoose an example to run:")
    print("1. Basic sync example")
    print("2. Configuration example")
    print("3. Webhook handling example")
    print("\nFor a full sync, run:")
    print("  asyncio.run(example_box_sync())")
    print("\nFor configuration info:")
    print("  asyncio.run(example_box_sync_with_config())")
    print("\nFor webhook info:")
    print("  asyncio.run(example_webhook_handling())")

    # Uncomment to run:
    # asyncio.run(example_box_sync())
    # asyncio.run(example_box_sync_with_config())
    asyncio.run(example_webhook_handling())
