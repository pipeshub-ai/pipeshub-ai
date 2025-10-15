# ruff: noqa
import asyncio
import os

from app.sources.client.linear.linear import LinearClient
from app.sources.external.linear.linear import LinearDataSource
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main() -> None:
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build Linear client using configuration service (await the async method)
    try:
        linear_client = await LinearClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Linear client created successfully: {linear_client}")
    except Exception as e:
        logger.error(f"Failed to create Linear client: {e}")
        print(f"❌ Error creating Linear client: {e}")
        return
    
    # Create data source and use it
    linear_data_source = LinearDataSource(linear_client)
    
    # Test get all teams
    try:
        response = await linear_data_source.get_all_teams()
        print(f"✅ Linear get all teams response: {response}")
    except Exception as e:
        print(f"❌ Error getting Linear get all teams: {e}")
    finally:
        # Properly close the client session
        await linear_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())