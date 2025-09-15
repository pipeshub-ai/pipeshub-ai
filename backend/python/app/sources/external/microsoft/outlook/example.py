# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.outlook.outlook import OutlookCalendarContactsDataSource, OutlookCalendarContactsResponse

async def main():
    tenant_id = os.getenv("OUTLOOK_CLIENT_TENANT_ID")
    client_id = os.getenv("OUTLOOK_CLIENT_ID")
    client_secret = os.getenv("OUTLOOK_CLIENT_SECRET")
    if not tenant_id or not client_id or not client_secret:
        raise Exception("OUTLOOK_CLIENT_TENANT_ID, OUTLOOK_CLIENT_ID, and OUTLOOK_CLIENT_SECRET must be set")

    # testing for enterprise account
    client: MSGraphClient = MSGraphClient.build_with_config(
        MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id), 
        mode=GraphMode.APP)
    print(client)
    print("****************************")
    outlook_data_source: OutlookCalendarContactsDataSource = OutlookCalendarContactsDataSource(client)
    print("outlook_data_source:", outlook_data_source)
    print("Getting messages...")
    print("****************************")
    user_id_or_upn = "x"
    response: OutlookCalendarContactsResponse = await outlook_data_source.users_list_messages(user_id=user_id_or_upn)
    print(response.data)
    print(response.error)
    print(response.success)

    #getting messages with select and expand
    response: OutlookCalendarContactsResponse = await outlook_data_source.users_list_messages(user_id=user_id_or_upn, select=["id", "subject", "from", "receivedDateTime"])
    print(response.data)
    print(response.error)
    print(response.success)


if __name__ == "__main__":
    asyncio.run(main())

#users_list_messages