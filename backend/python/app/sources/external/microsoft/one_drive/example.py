# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.one_drive.one_drive import OneDriveDataSource, OneDriveResponse

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
    one_drive_data_source: OneDriveDataSource = OneDriveDataSource(client)
    print("one_drive_data_source:", one_drive_data_source)
    print("Getting drive...")
    print("****************************")
    user_id_or_upn = "your_user_id_or_upn"
    response: OneDriveResponse = await one_drive_data_source.users_list_drives(user_id=user_id_or_upn)
    print(response.data)
    print(response.error)
    print(response.success)

    #getting drive with select and expand
    response: OneDriveResponse = await one_drive_data_source.users_list_drives(user_id=user_id_or_upn, select=["id", "name", "createdBy"])
    print(response.data)
    print(response.error)
    print(response.success)


    response: OneDriveResponse = await one_drive_data_source.users_get_drives(user_id=user_id_or_upn, drive_id="xyz")
    print(response.data)
    print(response.error)
    print(response.success)


if __name__ == "__main__":
    asyncio.run(main())
