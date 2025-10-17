# ruff: noqa
import asyncio

from app.sources.client.azure.azure_blob import AzureBlobClient
from app.sources.external.azure.azure_blob import AzureBlobDataSource
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

    # Build Azure Blob client using configuration service (await the async method)
    try:
        azure_client = await AzureBlobClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Azure client created successfully: {azure_client}")
    except Exception as e:
        logger.error(f"Failed to create Azure client: {e}")
        print(f"❌ Error creating Azure client: {e}")
        return
    
    # Create data source and use it
    azure_data_source = AzureBlobDataSource(azure_client)
    
    # Test list account information
    try:
        response = await azure_data_source.get_account_information()
        print(f"✅ Azure list account information response: {response}")
    except Exception as e:
        print(f"❌ Error getting Azure list account information: {e}")
    finally:
        try:
            await azure_client.close_async_client()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())