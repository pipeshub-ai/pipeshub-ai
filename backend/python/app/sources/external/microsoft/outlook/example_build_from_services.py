# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient
from app.sources.external.microsoft.outlook.outlook import OutlookCalendarContactsDataSource
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
            service_name="outlook",
            logger=logger,
            config_service=config_service,
            mode=GraphMode.APP
        )
        print(f"Microsoft Graph client created successfully: {ms_graph_client}")
    except Exception as e:
        logger.error(f"Failed to create Microsoft Graph client: {e}")
        print(f"❌ Error creating Microsoft Graph client: {e}")
        return
    
    # Create data source and use it
    outlook_data_source = OutlookCalendarContactsDataSource(ms_graph_client)
    
    # Test getting messages (this will require a valid user ID)
    try:
        # Note: This is just an example - you'll need a real user ID
        test_user_id = "test-user-id"
        response = await outlook_data_source.users_list_messages(user_id=test_user_id)
        print(f"✅ Get messages response: {response}")
    except Exception as e:
        print(f"❌ Error getting messages (expected with test ID): {e}")



if __name__ == "__main__":
    asyncio.run(main())

#users_list_messages
