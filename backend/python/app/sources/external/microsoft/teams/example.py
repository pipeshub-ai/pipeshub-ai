# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.teams.teams import TeamsDataSource, TeamsResponse
from msgraph.generated.models.chat import Chat #type: ignore
from msgraph.generated.models.aad_user_conversation_member import AadUserConversationMember #type: ignore


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
    teams_data_source: TeamsDataSource = TeamsDataSource(client)
    print("teams_data_source:", teams_data_source)
    print("Getting teams...")
    print("****************************")
    user_id_or_upn = os.getenv("USER_ID_OR_UPN")


    response: TeamsResponse = await teams_data_source.users_get_teamwork(user_id=user_id_or_upn)
    print(response.data)
    print(response.error)
    print(response.success)

    chat = Chat(
            topic="Test Chat",
            members=[
                AadUserConversationMember(
                    roles=["owner"],
                    additional_data={
                        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users/{user_id_or_upn}"
                    }
                )
            ]
        )
    response: TeamsResponse = await teams_data_source.users_create_chats(user_id=user_id_or_upn, body=chat) # fix the body 
    print(response.data)
    print(response.error)
    print(response.success)


if __name__ == "__main__":
    asyncio.run(main())
