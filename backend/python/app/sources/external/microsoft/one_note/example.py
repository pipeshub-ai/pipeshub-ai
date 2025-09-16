# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.one_note.one_note import OneNoteDataSource, OneNoteResponse

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
    one_note_data_source: OneNoteDataSource = OneNoteDataSource(client)
    print("one_note_data_source:", one_note_data_source)
    print("Getting drive...")
    print("****************************")
    user_id_or_upn = os.getenv("USER_ID_OR_UPN")
    if not user_id_or_upn:
        raise Exception("USER_ID_OR_UPN must be set")
    response: OneNoteResponse = await one_note_data_source.users_get_onenote(user_id=user_id_or_upn)
    print(response.data)
    print(response.error)
    print(response.success)

    

if __name__ == "__main__":
    asyncio.run(main())
