"""
Confluence Cloud Connector Example

Simple script to test the Confluence connector without running the full app.
Requires environment variables:
- TEST_USER_EMAIL: Email of test user
- CONFLUENCE_CLIENT_ID: Atlassian OAuth Client ID
- CONFLUENCE_CLIENT_SECRET: Atlassian OAuth Client Secret
- CONFLUENCE_ACCESS_TOKEN: OAuth Access Token (get this from OAuth flow first)
"""

import asyncio
import os

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


async def test_run() -> None:
    """Run Confluence connector test sync"""
    user_email = os.getenv("TEST_USER_EMAIL")
    org_id = "org_1"

    async def create_test_users(
        user_email: str, arango_service: BaseArangoService
    ) -> None:
        """Create test organization and user in ArangoDB"""
        org = {
            "_key": org_id,
            "accountType": "enterprise",
            "name": "Test Org",
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }

        await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)

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

        await arango_service.batch_create_edges(
            [
                {
                    "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
                    "_to": f"{CollectionNames.ORGS.value}/{org_id}",
                    "entityType": "ORGANIZATION",
                    "createdAtTimestamp": 1718745600,
                    "updatedAtTimestamp": 1718745600,
                }
            ],
            CollectionNames.BELONGS_TO.value,
        )

    # Setup logging and services
    logger = create_logger("confluence_connector")
    logger.info("🚀 Starting Confluence Connector Test...")

    # Initialize configuration services
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)

    # Connect to ArangoDB
    arango_client = ArangoClient()
    arango_service = BaseArangoService(
        logger, arango_client, config_service, kafka_service
    )

    try:
        await arango_service.connect()
        logger.info("✅ Connected to ArangoDB")
    except Exception as e:
        logger.error(f"❌ Failed to connect to ArangoDB: {e}")
        return

    # Setup data store
    data_store_provider = ArangoDataStore(logger, arango_service)

    # Create test user if provided
    if user_email:
        logger.info(f"Creating test user: {user_email}")
        await create_test_users(user_email, arango_service)
    else:
        logger.warning("⚠️ No TEST_USER_EMAIL provided, skipping user creation")

    # Configure Confluence connector with OAuth credentials
    config = {
        "auth": {
            "authType": "OAUTH",
            "clientId": os.getenv("CONFLUENCE_CLIENT_ID"),
            "clientSecret": os.getenv("CONFLUENCE_CLIENT_SECRET"),
        },
        "credentials": {
            "access_token": os.getenv("CONFLUENCE_ACCESS_TOKEN"),
        }
    }

    # Validate required config
    if not config["auth"]["clientId"]:
        logger.error("❌ CONFLUENCE_CLIENT_ID not set")
        return
    if not config["auth"]["clientSecret"]:
        logger.error("❌ CONFLUENCE_CLIENT_SECRET not set")
        return
    if not config["credentials"]["access_token"]:
        logger.error("❌ CONFLUENCE_ACCESS_TOKEN not set")
        return

    logger.info("✅ Configuration validated")

    # Store config in key-value store
    await key_value_store.create_key("/services/connectors/confluence/config", config)

    # Create and initialize connector
    logger.info("🔧 Creating Confluence connector...")
    connector: BaseConnector = await ConfluenceConnector.create_connector(
        logger, data_store_provider, config_service
    )

    logger.info("🔧 Initializing Confluence connector...")
    init_success = await connector.init()

    if not init_success:
        logger.error("❌ Connector initialization failed")
        return

    # Run sync
    logger.info("🔄 Running Confluence sync...")
    await connector.run_sync()

    logger.info("✅ Confluence sync completed successfully!")


if __name__ == "__main__":
    asyncio.run(test_run())

