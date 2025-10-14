# ruff: noqa
import asyncio

from app.sources.client.box.box import BoxClient
from app.sources.external.box.box import BoxDataSource
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

    # Build Box client using configuration service (await the async method)
    try:
        box_client = await BoxClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Box client created successfully: {box_client}")
    except Exception as e:
        logger.error(f"Failed to create Box client: {e}")
        print(f"❌ Error creating Box client: {e}")
        return
    
    # Create data source and use it
    box_data_source = BoxDataSource(box_client)
    
    # Test get current user
    try:
        response = await box_data_source.get_current_user()
        print(f"✅ Box get current user response: {response}")
    except Exception as e:
        print(f"❌ Error getting Box get current user: {e}")


if __name__ == "__main__":
    asyncio.run(main())