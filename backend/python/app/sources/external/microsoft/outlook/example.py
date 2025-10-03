# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient
from app.sources.external.microsoft.outlook.outlook import OutlookCalendarContactsDataSource
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main():
    # tenant_id = os.getenv("OUTLOOK_CLIENT_TENANT_ID")
    # client_id = os.getenv("OUTLOOK_CLIENT_ID")
    # client_secret = os.getenv("OUTLOOK_CLIENT_SECRET")
    # if not tenant_id or not client_id or not client_secret:
    #     raise Exception("OUTLOOK_CLIENT_TENANT_ID, OUTLOOK_CLIENT_ID, and OUTLOOK_CLIENT_SECRET must be set")

    # # testing for enterprise account
    # client: MSGraphClient = MSGraphClient.build_with_config(
    #     MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id), 
    #     mode=GraphMode.APP)
    # print(client)
    # print("****************************")
    # outlook_data_source: OutlookCalendarContactsDataSource = OutlookCalendarContactsDataSource(client)
    # print("outlook_data_source:", outlook_data_source)
    # print("Getting messages...")
    # print("****************************")
    # user_id_or_upn = "x"
    # response: OutlookCalendarContactsResponse = await outlook_data_source.users_list_messages(user_id=user_id_or_upn)
    # print(response.data)
    # print(response.error)
    # print(response.success)

    # #getting messages with select and expand
    # response: OutlookCalendarContactsResponse = await outlook_data_source.users_list_messages(user_id=user_id_or_upn, select=["id", "subject", "from", "receivedDateTime"])
    # print(response.data)
    # print(response.error)
    # print(response.success)

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
