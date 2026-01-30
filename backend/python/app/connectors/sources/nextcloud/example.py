import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock

from dotenv import load_dotenv

from app.connectors.sources.nextcloud.connector import NextcloudConnector

# Load environment variables
load_dotenv()

async def test_nextcloud_connector() -> None:
    """Simple test script for Nextcloud connector."""

    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Configuration for config_service mock
    nextcloud_config = {
        "auth": {
            "baseUrl": os.getenv("NEXTCLOUD_INSTANCE_URL", "https://your-nextcloud.com"),
            "username": os.getenv("NEXTCLOUD_USERNAME", "your-username"),
            "password": os.getenv("NEXTCLOUD_PASSWORD", "your-password"),
        }
    }

    print("Initializing Nextcloud connector...")
    print(f"Instance URL: {nextcloud_config['auth']['baseUrl']}")
    print(f"Username: {nextcloud_config['auth']['username']}")

    # Create mock dependencies
    data_entities_processor = AsyncMock()
    data_entities_processor.org_id = "test-org-123"

    # Mock data store provider with transaction support
    mock_transaction = MagicMock()
    mock_transaction.get_record_by_external_id = AsyncMock(return_value=None)
    mock_transaction.get_record_by_path = AsyncMock(return_value=None)
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)

    data_store_provider = MagicMock()
    data_store_provider.transaction = MagicMock(return_value=mock_transaction)

    # Mock config service to return our config
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=nextcloud_config)

    connector_id = "test-nextcloud-connector"

    # Initialize connector with all required dependencies
    connector = NextcloudConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id
    )

    try:
        # Initialize the connector
        print("\nCalling init()...")
        init_result = await connector.init()

        if init_result:
            print("✓ Connector initialized successfully")
            print("✓ Connection to Nextcloud successful!")
            print(f"✓ Base URL: {connector.base_url}")
            print(f"✓ Current User: {connector.current_user_id}")
            print(f"✓ User Email: {connector.current_user_email}")
        else:
            print("✗ Connector initialization failed")

    except Exception as e:
        print(f"\n✗ Error: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("=" * 60)
    print("Nextcloud Connector Test Script")
    print("=" * 60)
    asyncio.run(test_nextcloud_connector())
