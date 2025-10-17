# ruff: noqa
import asyncio
import os

from app.sources.client.linkedin.linkedin import LinkedInClient
from app.sources.external.linkedin.linkedin import LinkedInDataSource
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
        linkedin_client = await LinkedInClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"LinkedIn client created successfully: {linkedin_client}")
    except Exception as e:
        logger.error(f"Failed to create LinkedIn client: {e}")
        print(f"❌ Error creating LinkedIn client: {e}")
        return
    
    # Create data source and use it
    linkedin_data_source = LinkedInDataSource(linkedin_client)
    
    # Test get profile
    try:
        response = await linkedin_data_source.get_profile()
        print(f"✅ LinkedIn get profile response: {response}")
    except Exception as e:
        print(f"❌ Error getting LinkedIn get profile: {e}")


if __name__ == "__main__":
    asyncio.run(main())