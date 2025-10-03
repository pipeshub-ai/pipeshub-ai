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

async def main():
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    if not tenant_id or not client_id or not client_secret:
        raise Exception("AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET must be set")

    # testing for enterprise account
    # client: MSGraphClient = MSGraphClient.build_with_config(
    #     MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id), 
    #     mode=GraphMode.APP)
    # print(client)
    # print("****************************")
    # one_drive_data_source: OneDriveDataSource = OneDriveDataSource(client)
    # print("one_drive_data_source:", one_drive_data_source)
    # print("Getting drive...")
    # print("****************************")
    # user_id_or_upn = "your_user_id_or_upn"
    # response: OneDriveResponse = await one_drive_data_source.users_list_drives(user_id=user_id_or_upn)
    # print(response.data)
    # print(response.error)
    # print(response.success)

    # #getting drive with select and expand
    # response: OneDriveResponse = await one_drive_data_source.users_list_drives(user_id=user_id_or_upn, select=["id", "name", "createdBy"])
    # print(response.data)
    # print(response.error)
    # print(response.success)


    # response: OneDriveResponse = await one_drive_data_source.users_get_drives(user_id=user_id_or_upn, drive_id="xyz")
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
