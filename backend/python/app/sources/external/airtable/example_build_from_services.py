# ruff: noqa
import asyncio

from app.sources.client.airtable.airtable import AirtableClient
from app.sources.external.airtable.airtable import AirtableDataSource
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

    # Build Airtable client using configuration service (await the async method)
    try:
        airtable_client = await AirtableClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Airtable client created successfully: {airtable_client}")
    except Exception as e:
        logger.error(f"Failed to create Airtable client: {e}")
        print(f"❌ Error creating Airtable client: {e}")
        return
    
    # Create data source and use it
    airtable_data_source = AirtableDataSource(airtable_client)
    
    # Test get current user
    try:
        response = await airtable_data_source.get_current_user()
        print(f"✅ Airtable get current user response: {response}")
    except Exception as e:
        print(f"❌ Error getting Airtable get current user: {e}")
    finally:
        # Properly close the client session
        await airtable_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())