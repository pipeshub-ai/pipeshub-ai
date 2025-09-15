# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.sharepoint.sharepoint import SharePointDataSource, SharePointResponse

async def main():
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    if not tenant_id or not client_id or not client_secret:
        raise Exception("AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET must be set")

    # testing for enterprise account
    client: MSGraphClient = MSGraphClient.build_with_config(
        MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id), 
        mode=GraphMode.APP)
    print(client)
    print("****************************")
    sharepoint_data_source: SharePointDataSource = SharePointDataSource(client)
    print("sharepoint_data_source:", sharepoint_data_source)
    print("Getting sites...")
    print("****************************")
    user_id_or_upn = "x"
    response: SharePointResponse = await sharepoint_data_source.sites_get_all_sites()
    print(response.data)
    print(response.error)
    print(response.success)

    response: SharePointResponse = await sharepoint_data_source.sites_get_all_sites(select=["id"]) # not working on select filter
    print(response.data)
    print(response.error)
    print(response.success)



if __name__ == "__main__":
    asyncio.run(main())
