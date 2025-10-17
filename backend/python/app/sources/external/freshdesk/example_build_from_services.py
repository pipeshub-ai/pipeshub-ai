# ruff: noqa
import asyncio

from app.sources.client.freshdesk.freshdesk import FreshDeskClient
from app.sources.external.freshdesk.freshdesk import FreshdeskDataSource
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

    # Build FreshDesk client using configuration service (await the async method)
    try:
        freshdesk_client = await FreshDeskClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"FreshDesk client created successfully: {freshdesk_client}")
    except Exception as e:
        logger.error(f"Failed to create FreshDesk client: {e}")
        print(f"❌ Error creating FreshDesk client: {e}")
        return
    
    # Create data source and use it
    freshdesk_data_source = FreshdeskDataSource(freshdesk_client)
    
    # Test list tickets
    try:
        response = await freshdesk_data_source.list_tickets()
        print(f"✅ FreshDesk list tickets response: {response}")
    except Exception as e:
        print(f"❌ Error getting FreshDesk list tickets: {e}")

    finally:
        # Properly close the client session
        await freshdesk_client.get_client().close()


if __name__ == "__main__":
    asyncio.run(main())