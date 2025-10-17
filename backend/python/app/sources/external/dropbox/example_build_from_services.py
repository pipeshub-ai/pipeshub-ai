# ruff: noqa
import asyncio

from app.sources.client.dropbox.dropbox_ import DropboxClient
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
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

    # Build Dropbox client using configuration service (await the async method)
    try:
        dropbox_client = await DropboxClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Dropbox client created successfully: {dropbox_client}")
    except Exception as e:
        logger.error(f"Failed to create Dropbox client: {e}")
        print(f"❌ Error creating Dropbox client: {e}")
        return
    
    # Create data source and use it
    dropbox_data_source = DropboxDataSource(dropbox_client)
    
    # Test list groups
    try:
        response = await dropbox_data_source.team_groups_list()
        print(f"✅ Dropbox list groups response: {response}")
    except Exception as e:
        print(f"❌ Error getting Dropbox list groups: {e}")


if __name__ == "__main__":
    asyncio.run(main())