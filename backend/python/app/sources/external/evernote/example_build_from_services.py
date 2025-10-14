# ruff: noqa
import asyncio

from app.sources.client.evernote.evernote import EvernoteClient
from app.sources.external.evernote.evernote import EvernoteDataSource
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

    # Build Evernote client using configuration service (await the async method)
    try:
        evernote_client = await EvernoteClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Evernote client created successfully: {evernote_client}")
    except Exception as e:
        logger.error(f"Failed to create Evernote client: {e}")
        print(f"❌ Error creating Evernote client: {e}")
        return
    
    # Create data source and use it
    evernote_data_source = EvernoteDataSource(evernote_client)
    
    # Test get default notebook
    try:
        response = await evernote_data_source.get_default_notebook()
        print(f"✅ Evernote get default notebook response: {response}")
    except Exception as e:
        print(f"❌ Error getting Evernote get default notebook: {e}")


if __name__ == "__main__":
    asyncio.run(main())