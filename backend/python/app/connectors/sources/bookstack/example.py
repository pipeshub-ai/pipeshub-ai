import asyncio
import os

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


async def test_run() -> None:
    """Initializes and runs the BookStack connector sync process for testing."""
    org_id = "68d28814cdabcc98a3e02605"

    # Helper function to set up a test user and organization in ArangoDB
    async def create_test_users(arango_service: BaseArangoService) -> None:
        org = {
            "_key": org_id,
            "accountType": "enterprise",
            "name": "Test Org",
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }
        await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)

        user_email = "test_user@example.com"  # Dummy user for DB record
        user = {
            "_key": user_email,
            "email": user_email,
            "userId": user_email,
            "orgId": org_id,
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }
        await arango_service.batch_upsert_nodes([user], CollectionNames.USERS.value)

        await arango_service.batch_create_edges([{
            "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
            "_to": f"{CollectionNames.ORGS.value}/{org_id}",
        }], CollectionNames.BELONGS_TO.value)

    # 1. Initialize services
    logger = create_logger("bookstack_connector")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "../../../config/default_config.json")
    key_value_store = InMemoryKeyValueStore(logger, config_path)
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)

    # 2. Create test data in the database
    await create_test_users(arango_service)

    # 3. Set BookStack credentials in the in-memory config store
    bookstack_base_url = os.getenv("BOOKSTACK_BASE_URL")
    bookstack_token_id = os.getenv("BOOKSTACK_TOKEN_ID")
    bookstack_token_secret = os.getenv("BOOKSTACK_TOKEN_SECRET")

    if not bookstack_base_url:
        logger.error("BOOKSTACK_BASE_URL environment variable not set.")
        logger.info("Example: export BOOKSTACK_BASE_URL='https://your-bookstack-instance.com'")
        return

    if not bookstack_token_id:
        logger.error("BOOKSTACK_TOKEN_ID environment variable not set.")
        logger.info("Example: export BOOKSTACK_TOKEN_ID='tokenid'")
        logger.info("You can generate a token from BookStack user settings -> API Tokens")
        return

    if not bookstack_token_secret:
        logger.error("BOOKSTACK_TOKEN_SECRET environment variable not set.")
        logger.info("Example: export BOOKSTACK_TOKEN_SECRET='tokensecret'")
        logger.info("You can generate a token from BookStack user settings -> API Tokens")
        return

    config = {
        "auth": {
            "base_url": bookstack_base_url,
            "token_id": bookstack_token_id,
            "token_secret": bookstack_token_secret
        }
    }

    await key_value_store.create_key("/services/connectors/bookstack/config", config)

    # Debug logging
    logger.info("\nDEBUG: Stored config at key: /services/connectors/bookstack/config")

    # Try to retrieve it immediately
    stored_config = await key_value_store.get_key("/services/connectors/bookstack/config")
    logger.info(f"\nDEBUG: Retrieved config: {stored_config}")

    # Also list all keys to see what's actually in the store
    all_keys = await key_value_store.get_all_keys()
    logger.info(f"\nDEBUG: All keys in store: {all_keys}")

    # 4. Create and run the BookStack connector
    bookstack_connector = None
    try:
        bookstack_connector = await BookStackConnector.create_connector(
            logger,
            data_store_provider,
            config_service
        )

        # Initialize the connector
        if await bookstack_connector.init():
            logger.info("BookStack connector initialized successfully.")

            # Test the connection
            if await bookstack_connector.test_connection_and_access():
                logger.info("BookStack connection test passed.")

                # Run the full sync
                logger.info("Starting BookStack sync...")
                await bookstack_connector.run_sync()
                logger.info("BookStack sync completed successfully.")
            else:
                logger.error("BookStack connection test failed. Check your API URL and token.")
        else:
            logger.error("BookStack connector initialization failed.")

    except Exception as e:
        logger.error(f"An error occurred during the BookStack sync: {e}", exc_info=True)
    finally:
        if bookstack_connector:
            bookstack_connector.cleanup()


if __name__ == "__main__":
    # Setup instructions:
    # 1. Get your BookStack instance URL (e.g., https://bookstack.example.com)
    # 2. Generate an API token from BookStack:
    #    - Go to your BookStack user settings
    #    - Navigate to "API Tokens" section
    #    - Create a new token (you'll get a token ID and secret)
    # 3. Set environment variables:
    #    export BOOKSTACK_BASE_URL='https://your-bookstack-instance.com'
    #    export BOOKSTACK_TOKEN_ID='tokenid'
    #    export BOOKSTACK_TOKEN_SECRET='tokensecret'
    # 4. Run this script:
    #    python example.py

    asyncio.run(test_run())
