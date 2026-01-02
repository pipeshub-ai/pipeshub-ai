"""
Test file for YourConnector.
Run this to test your connector implementation before integrating with the full system.
"""

import asyncio
import os
import sys
from pathlib import Path

from arango import ArangoClient

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.github.connector import GithubConnector
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


async def test_connector():
    """Test connector initialization and basic operations."""

    # Setup
    logger = create_logger("test_github")
    base_dir = os.path.dirname(os.path.abspath(__file__))

    key_value_store = InMemoryKeyValueStore(logger)
    config_service = ConfigurationService(logger, key_value_store)
    config_path = os.path.join(base_dir, "../../../config/default_config.json")
    key_value_store = InMemoryKeyValueStore(logger, config_path)
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)


    # try:
    #     logger.info("=" * 50)
    #     logger.info("Testing GithubConnector")
    #     logger.info("=" * 50)
    #     # Test 1: Create connector instance
    #     try:
    #         logger.info("\n1. Creating connector instance...")
    #         connector = await GithubConnector.create_connector(
    #             logger,
    #             data_store_provider, #- set to None for basic testing
    #             config_service
    #         )
    #         logger.info("✅ Connector instance created")

    #         # Test 2: Initialize connector
    #         logger.info("\n2. Initializing connector...")
    #         init_result = await connector.init()
    #         if init_result:
    #             logger.info("✅ Connector initialized successfully")
    #         else:
    #             logger.error("❌ Connector initialization failed")
    #             return
    #     except Exception as e:
    #         logger.error(f"An error occurred during the BookStack sync: {e}", exc_info=True)
    #     finally:
    #         if connector:
    #             connector.cleanup()

    #     # Test 3: Test connection need to make it
    #     # logger.info("\n3. Testing connection...")
    #     # connection_ok = connector.test_connection_and_access()
    #     # if connection_ok:
    #     #     logger.info("✅ Connection test passed")
    #     # else:
    #     #     logger.error("❌ Connection test failed")
    #     #     return

    #     # Test 4: Fetch sample data (without saving to database)
    #     # logger.info("\n4. Fetching sample data from API...")
    #     # TODO: Implement test data fetching
    #     # users = await connector._get_all_users_external()
    #     # logger.info(f"✅ Fetched {len(users)} users")

    #     # Test 5: Test sync (dry run - comment out database operations)
    #     # logger.info("\n5. Running test sync (dry run)...")
    #     await connector.run_sync()
    #     # logger.info("✅ Sync test completed")

    #     logger.info("\n" + "=" * 50)
    #     logger.info("All tests passed! ✅")
    #     logger.info("=" * 50)

    # except Exception as e:
    #     logger.error(f"\n❌ Test failed: {e}", exc_info=True)
    # finally:
    #     # Cleanup
    #     connector.cleanup()
    TOKEN = os.getenv("GITHUB_PAT")
    config={
        "auth":{
            "token_id":TOKEN
        }
    }
    logger.info(f"config : {config}")
    await key_value_store.create_key("/services/connectors/github/config",config)
    try:
        logger.info("=" * 50)
        logger.info("Testing GithubConnector")
        logger.info("=" * 50)
        logger.info("\n1. Creating connector instance...")
        connector = await GithubConnector.create_connector(
                logger,
                data_store_provider, #- set to None for basic testing
                config_service
            )
        logger.info("✅ Connector instance created")

            # Test 2: Initialize connector
        logger.info("\n2. Initializing connector...")
        init_result = await connector.init()
        if init_result:
                logger.info("✅ Connector initialized successfully")
        else:
                logger.error("❌ Connector initialization failed")
                return

        await connector.run_sync()
        logger.info("\n" + "=" * 40)
        logger.info("All tests passed! ✅")
        logger.info("=" * 40)
    except Exception as e:
        logger.error(f"An error occurred during the github sync: {e}", exc_info=True)
    finally:
        if connector:
            connector.cleanup()


if __name__ == "__main__":
    asyncio.run(test_connector())
