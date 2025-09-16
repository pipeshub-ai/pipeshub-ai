# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.users_groups.users_groups import UsersGroupsDataSource, UsersGroupsResponse

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
    users_groups_data_source: UsersGroupsDataSource = UsersGroupsDataSource(client)
    print("users_groups_data_source:", users_groups_data_source)
    print("Getting users...")
    print("****************************")
    user_id_or_upn = os.getenv("USER_ID_OR_UPN")
    response: UsersGroupsResponse = await users_groups_data_source.users_user_list_user(user_id=user_id_or_upn)
    print(response.data)
    print(response.error)
    print(response.success)



if __name__ == "__main__":
    asyncio.run(main())
