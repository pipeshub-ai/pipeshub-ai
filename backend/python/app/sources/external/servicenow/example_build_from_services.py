# ruff: noqa
import asyncio
import os

from app.sources.client.servicenow.servicenow import ServiceNowClient
from app.sources.external.servicenow.servicenow import ServiceNowDataSource
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

    # Build ServiceNow client using configuration service (await the async method)
    try:
        servicenow_client = await ServiceNowClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"ServiceNow client created successfully: {servicenow_client}")
    except Exception as e:
        logger.error(f"Failed to create ServiceNow client: {e}")
        print(f"❌ Error creating ServiceNow client: {e}")
        return
    
    # Create data source and use it
    servicenow_data_source = ServiceNowDataSource(servicenow_client)
    
    # Test list tickets
    try:
        response = await servicenow_data_source.get_now_table_tableName(tableName="incident")
        print(f"✅ ServiceNow list incident response: {response}")
    except Exception as e:
        print(f"❌ Error getting ServiceNow list incident: {e}")

    finally:
        # Properly close the client session
        await servicenow_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())