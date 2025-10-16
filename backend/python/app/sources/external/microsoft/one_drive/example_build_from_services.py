# ruff: noqa
import asyncio
import os
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient
from app.sources.external.microsoft.one_drive.one_drive import OneDriveDataSource
from app.config.configuration_service import ConfigurationService
import logging
from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.one_drive.one_drive import OneDriveDataSource, OneDriveResponse
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main():
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build Microsoft Graph client using configuration service (await the async method)
    try:
        ms_graph_client = await MSGraphClient.build_from_services(
            service_name="onedrive",
            logger=logger,
            config_service=config_service,
            mode=GraphMode.APP,
        )
        print(f"Microsoft Graph client created successfully: {ms_graph_client}")
    except Exception as e:
        logger.error(f"Failed to create Microsoft Graph client: {e}")
        print(f"❌ Error creating Microsoft Graph client: {e}")
        return
    
    # Create data source and use it
    one_drive_data_source = OneDriveDataSource(ms_graph_client)
    
    # Test getting drives
    try:
        response = await one_drive_data_source.drives_drive_list_drive()
        print(f"✅ Get drives response: {response.to_dict()}")
    except Exception as e:
        print(f"❌ Error getting drives: {e}")


if __name__ == "__main__":
    asyncio.run(main())
