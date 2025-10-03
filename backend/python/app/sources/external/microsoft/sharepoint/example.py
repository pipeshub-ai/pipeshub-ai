# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.sharepoint.sharepoint import SharePointDataSource, SharePointResponse
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore


async def main():
    # tenant_id = os.getenv("AZURE_TENANT_ID")
    # client_id = os.getenv("AZURE_CLIENT_ID")
    # client_secret = os.getenv("AZURE_CLIENT_SECRET")
    # if not tenant_id or not client_id or not client_secret:
    #     raise Exception("AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET must be set")

    # # testing for enterprise account
    # client: MSGraphClient = MSGraphClient.build_with_config(
    #     MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id), 
    #     mode=GraphMode.APP)
    # print(client)
    # print("****************************")
    # sharepoint_data_source: SharePointDataSource = SharePointDataSource(client)
    # print("sharepoint_data_source:", sharepoint_data_source)
    # print("Getting sites...")
    # print("****************************")
    # user_id_or_upn = "x"
    # response: SharePointResponse = await sharepoint_data_source.sites_get_all_sites()
    # print(response.data)
    # print(response.error)
    # print(response.success)

    # response: SharePointResponse = await sharepoint_data_source.sites_get_all_sites(select=["id"]) # not working on select filter
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
            service_name="sharepointonline",
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
    sharepoint_data_source = SharePointDataSource(ms_graph_client)
    
    # Test getting sites
    try:
        response = await sharepoint_data_source.sites_get_all_sites()
        print(f"✅ Get sites response: {response.data}")
    except Exception as e:
        print(f"❌ Error getting sites: {e}")



if __name__ == "__main__":
    asyncio.run(main())
