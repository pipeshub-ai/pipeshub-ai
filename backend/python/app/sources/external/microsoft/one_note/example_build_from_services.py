# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.one_note.one_note import OneNoteDataSource, OneNoteResponse
from app.config.configuration_service import ConfigurationService
import logging

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
            service_name="onenote",
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
    one_note_data_source = OneNoteDataSource(ms_graph_client)
    
    # Test getting notebooks
    try:
        response = await one_note_data_source.me_get_onenote()
        print(f"✅ Get notebooks response: {response.data}")
    except Exception as e:
        print(f"❌ Error getting notebooks: {e}")
    

if __name__ == "__main__":
    asyncio.run(main())
