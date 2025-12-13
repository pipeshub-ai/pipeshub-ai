"""
Nextcloud Connector Quick Test Script
Pre-configured with your local environment settings.
"""

import asyncio
import os
import sys
import time
from typing import Tuple

from arango import ArangoClient
from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.nextcloud.connector import NextcloudConnector
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../../../"))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

NEXTCLOUD_CONFIG = {
    "base_url": "http://localhost:8080",
    "username": "NC_Admin",
    "password": "Admin@2025",
    "token": "jLgDD-rSdNa-jZr8f-D7TMP-Knces",
    "use_token": True,
}

ORG_ID = "68d28814cdabcc98a3e02605"


async def setup_services() -> Tuple[Logger, InMemoryKeyValueStore, ConfigurationService, BaseArangoService, ArangoDataStore]:
    """Initialize all required services"""
    logger = create_logger("nextcloud_connector")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "../../../config/default_config.json")

    key_value_store = InMemoryKeyValueStore(logger, config_path)
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)

    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()

    data_store_provider = ArangoDataStore(logger, arango_service)

    return logger, key_value_store, config_service, arango_service, data_store_provider


async def create_test_users(arango_service: BaseArangoService, logger: Logger) -> None:
    """Create test organization and user in ArangoDB"""
    current_time = int(time.time())

    org = {
        "_key": ORG_ID,
        "accountType": "enterprise",
        "name": "Test Org",
        "isActive": True,
        "createdAtTimestamp": current_time,
        "updatedAtTimestamp": current_time,
    }
    await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)
    logger.info(f"✅ Created test org: {ORG_ID}")

    user_email = "test_user@example.com"
    user = {
        "_key": user_email,
        "email": user_email,
        "userId": user_email,
        "orgId": ORG_ID,
        "isActive": True,
        "createdAtTimestamp": current_time,
        "updatedAtTimestamp": current_time,
    }
    await arango_service.batch_upsert_nodes([user], CollectionNames.USERS.value)

    await arango_service.batch_create_edges([{
        "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
        "_to": f"{CollectionNames.ORGS.value}/{ORG_ID}",
    }], CollectionNames.BELONGS_TO.value)

    logger.info(f"✅ Created test user: {user_email}")


async def setup_nextcloud_config(key_value_store: InMemoryKeyValueStore, logger: Logger) -> None:
    """Setup Nextcloud configuration"""
    if NEXTCLOUD_CONFIG["use_token"] and NEXTCLOUD_CONFIG["token"]:
        logger.info("🔐 Using Bearer Token authentication")
        auth_config = {
            "authType": "BEARER_TOKEN",
            "bearerToken": NEXTCLOUD_CONFIG["token"]
        }
    else:
        logger.info("🔐 Using Basic Auth (username/password)")
        auth_config = {
            "authType": "BASIC_AUTH",
            "username": NEXTCLOUD_CONFIG["username"],
            "password": NEXTCLOUD_CONFIG["password"]
        }

    config = {
        "auth": auth_config,
        "credentials": {
            "baseUrl": NEXTCLOUD_CONFIG["base_url"]
        },
        "sync": {
            "batchSize": 100
        }
    }

    await key_value_store.create_key("/services/connectors/nextcloud/config", config)
    logger.info("✅ Config stored at: /services/connectors/nextcloud/config")


async def test_full_sync() -> None:
    """Run full sync - let the connector framework handle app registration"""
    logger, key_value_store, config_service, arango_service, data_store_provider = await setup_services()
    connector = None

    try:
        logger.info("🔍 FULL SYNC TEST")
        logger.info("=" * 60)

        # Step 1: Create test data (org and user only)
        logger.info("Step 1: Creating test organization and user...")
        await create_test_users(arango_service, logger)

        # Step 2: Setup configuration
        logger.info("\nStep 2: Setting up Nextcloud configuration...")
        await setup_nextcloud_config(key_value_store, logger)

        # Step 3: Create connector (this should handle app registration internally)
        logger.info("\nStep 3: Creating connector instance...")
        logger.info("(The connector will auto-register the app with correct schema)")
        connector = await NextcloudConnector.create_connector(
            logger, data_store_provider, config_service
        )

        # Step 4: Initialize connector
        logger.info("\nStep 4: Initializing connector...")
        if await connector.init():
            logger.info("✅ Connector initialized successfully")

            # Step 5: Test connection
            logger.info("\nStep 5: Testing connection to Nextcloud...")
            if await connector.test_connection_and_access():
                logger.info("✅ Connection test passed")

                # Step 6: Run full sync
                logger.info("\n" + "=" * 60)
                logger.info("Step 6: Starting Full Sync...")
                logger.info("=" * 60 + "\n")

                await connector.run_sync()

                logger.info("\n" + "=" * 60)
                logger.info("🎉 SYNC COMPLETED SUCCESSFULLY!")
                logger.info("=" * 60)
            else:
                logger.error("❌ Connection test failed")
                logger.error("Please check:")
                logger.error("  - Nextcloud is running at http://localhost:8080")
                logger.error("  - Token is valid")
                logger.error("  - User has proper permissions")
        else:
            logger.error("❌ Failed to initialize connector")
            logger.error("Check the logs above for details")

    except Exception as e:
        logger.error(f"\n❌ Full sync error: {e}", exc_info=True)

    finally:
        if connector:
            logger.info("\nCleaning up...")
            await connector.cleanup()


async def test_connection_only() -> None:
    """Quick test to just verify connection to Nextcloud"""
    logger, key_value_store, config_service, arango_service, data_store_provider = await setup_services()
    connector = None

    try:
        logger.info("🔍 CONNECTION TEST ONLY")
        logger.info("=" * 60)

        await create_test_users(arango_service, logger)
        await setup_nextcloud_config(key_value_store, logger)

        connector = await NextcloudConnector.create_connector(
            logger, data_store_provider, config_service
        )

        if await connector.init():
            logger.info("✅ Connector initialized")

            if await connector.test_connection_and_access():
                logger.info("✅ CONNECTION SUCCESSFUL!")
                logger.info("Nextcloud is reachable and authenticated")
            else:
                logger.error("❌ CONNECTION FAILED")
        else:
            logger.error("❌ Failed to initialize connector")

    except Exception as e:
        logger.error(f"❌ Connection test error: {e}", exc_info=True)

    finally:
        if connector:
            await connector.cleanup()


if __name__ == "__main__":
    asyncio.run(test_full_sync())