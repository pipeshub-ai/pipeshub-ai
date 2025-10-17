# ruff: noqa
import asyncio

from app.sources.client.bookstack.bookstack import BookStackClient
from app.sources.external.bookstack.bookstack import BookStackDataSource
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

    # Build BookStack client using configuration service (await the async method)
    try:
        bookstack_client = await BookStackClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"BookStack client created successfully: {bookstack_client}")
    except Exception as e:
        logger.error(f"Failed to create BookStack client: {e}")
        print(f"❌ Error creating BookStack client: {e}")
        return
    
    # Create data source and use it
    bookstack_data_source = BookStackDataSource(bookstack_client)
    
    # Test list pages
    try:
        response = await bookstack_data_source.list_pages()
        print(f"✅ BookStack list pages response: {response}")
    except Exception as e:
        print(f"❌ Error getting BookStack list pages: {e}")
    finally:
        try:
            await bookstack_client.get_client().close_async_client()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())