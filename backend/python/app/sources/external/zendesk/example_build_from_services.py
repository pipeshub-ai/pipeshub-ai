# ruff: noqa
import asyncio

from app.sources.client.zendesk.zendesk import ZendeskClient
from app.sources.external.zendesk.zendesk import ZendeskDataSource
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

    # Build Zendesk client using configuration service (await the async method)
    try:
        zendesk_client = await ZendeskClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Zendesk client created successfully: {zendesk_client}")
    except Exception as e:
        logger.error(f"Failed to create Zendesk client: {e}")
        print(f"❌ Error creating Zendesk client: {e}")
        return
    
    # Create data source and use it
    zendesk_data_source = ZendeskDataSource(zendesk_client)
    
    # Test list tickets
    try:
        response = await zendesk_data_source.list_tickets()
        print(f"✅ Zendesk list tickets response: {response}")
    except Exception as e:
        print(f"❌ Error getting Zendesk list: {e}")

    finally:
        # Properly close the client session
        await zendesk_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())