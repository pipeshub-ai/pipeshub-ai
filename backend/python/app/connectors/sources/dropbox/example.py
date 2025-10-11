import asyncio
import os

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


async def test_run() -> None:
    """Initializes and runs the Dropbox connector sync process for testing."""
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

        user_email = "test_user@example.com" # Dummy user for DB record
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
    logger = create_logger("dropbox_connector")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "../../../config/default_config.json")
    key_value_store = InMemoryKeyValueStore(logger, config_path)
    # key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)


    # 2. Create test data in the database
    await create_test_users(arango_service)

    # 3. Set Dropbox credentials in the in-memory config store
    dropbox_token = os.getenv("DROPBOX_TOKEN")
    dropbox_team_token = os.getenv("DROPBOX_TEAM_TOKEN")

    if not dropbox_token:
        logger.error("DROPBOX_TOKEN environment variable not set.")
        return

    config = {
        "credentials": {
            "access_token": dropbox_team_token,
            "isTeam": True  # Set to True if using a team token
        }
    }


    await key_value_store.create_key("/services/connectors/dropbox/config", config)

    #### logs
    logger.info("\nDEBUG: Stored config at key: /services/connectors/dropbox/config")

    # Try to retrieve it immediately
    stored_config = await key_value_store.get_key("/services/connectors/dropbox/config")
    logger.info(f"\nDEBUG: Retrieved config: {stored_config}")

    # Also list all keys to see what's actually in the store
    all_keys = await key_value_store.get_all_keys()
    logger.info(f"\nDEBUG: All keys in store: {all_keys}")

    # 4. Create and run the Dropbox connector
    try:
        dropbox_connector = await DropboxConnector.create_connector(logger, data_store_provider, config_service)
        if await dropbox_connector.init():
            logger.info("Dropbox connector initialized successfully.")
            await dropbox_connector.run_sync()
        else:
            logger.error("Dropbox connector initialization failed.")
    except Exception as e:
        logger.error(f"An error occurred during the Dropbox sync: {e}", exc_info=True)


if __name__ == "__main__":
    # Ensure you have a .env file or export DROPBOX_TOKEN
    # Example: export DROPBOX_TOKEN='your-token-here'
    asyncio.run(test_run())
