import asyncio
import os

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.linear.connector import LinearConnector
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


async def test_run() -> None:
    """Initializes and runs the Linear connector sync process for testing."""
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

        user_email = "test_user@example.com"
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
    logger = create_logger("linear_connector")
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

    # 3. Set Linear credentials in the in-memory config store
    linear_token = os.getenv("LINEAR_API_TOKEN")

    if not linear_token:
        logger.error("LINEAR_API_TOKEN environment variable not set.")
        logger.info("Get your token from: https://linear.app/settings/api")
        return

    config = {
        "auth": {
            "authType": "API_TOKEN",
            "apiToken": linear_token
        }
    }

    connector_id = "linear"
    await key_value_store.create_key(f"/services/connectors/{connector_id}/config", config)

    logger.info(f"DEBUG: Stored config at key: /services/connectors/{connector_id}/config")

    # Try to retrieve it immediately
    stored_config = await key_value_store.get_key(f"/services/connectors/{connector_id}/config")
    logger.info(f"DEBUG: Retrieved config: {stored_config}")

    # 4. Create and run the Linear connector
    try:
        linear_connector = await LinearConnector.create_connector(
            logger, data_store_provider, config_service, connector_id
        )
        if await linear_connector.init():
            logger.info("Linear connector initialized successfully.")
            await linear_connector.run_sync()
        else:
            logger.error("Linear connector initialization failed.")
    except Exception as e:
        logger.error(f"An error occurred during the Linear sync: {e}", exc_info=True)


if __name__ == "__main__":
    # Ensure you have LINEAR_API_TOKEN set
    # Example: export LINEAR_API_TOKEN='lin_api_your_token_here'
    # Or in PowerShell: $env:LINEAR_API_TOKEN = 'lin_api_your_token_here'
    asyncio.run(test_run())

