"""
Test file for Gitlab Connector.
Run this to test your connector implementation before integrating with the full system.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from arango import ArangoClient

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


def _get_env(
    logger, primary: str, *fallbacks: str, allow_fallback: bool = True
) -> Optional[str]:
    """Fetch env var from primary name, otherwise try fallbacks."""
    possible_names = (primary, *(fallbacks if allow_fallback else []))
    for name in possible_names:
        value = os.getenv(name)
        if value:
            value = value.strip()
        if value:
            if name != primary:
                logger.warning(
                    "Using fallback env var %s for %s. "
                    "Consider defining %s to keep environments explicit.",
                    name,
                    primary,
                    primary,
                )
            return value
    return None


async def test_run() -> None:
    logger = create_logger("gitlab_connector")
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # key_value_store = InMemoryKeyValueStore(logger)
    # config_service = ConfigurationService(logger, key_value_store)
    # config_path = os.path.join(base_dir, "../../../config/default_config.json")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)
    config = {
        "auth":{
            "authType":"OAUTH",
            "clientId":os.getenv("GITLAB_CLIENT_ID"),
            "clientSecret":os.getenv("GITLAB_CLIENT_SECRET"),
        },
        "credentials":{
            "access_token": os.getenv("GITLAB_ACCESS_TOKEN")
        }
    }
    await key_value_store.create_key("/services/connectors/gitlab/config", config)
    # Debug logging
    logger.info("\nDEBUG: Stored config at key: /services/connectors/gitlab/config")

    # Try to retrieve it immediately
    stored_config = await key_value_store.get_key("/services/connectors/gitlab/config")
    logger.info(f"\nDEBUG: Retrieved config: {stored_config}")
    gitlab_connector = None
    try:
        gitlab_connector =await  GitLabConnector.create_connector(
            logger, 
            data_store_provider,
            config_service,
            "gitlab"
        )
        await gitlab_connector.init()
        await gitlab_connector.test_connection_and_access()
        await gitlab_connector.run_sync()
    except Exception as e:
        logger.error(f"Error running Gitlab connector: {e}")
    finally:
        if gitlab_connector:
            gitlab_connector.cleanup()

if __name__ == "__main__":
    asyncio.run(test_run())