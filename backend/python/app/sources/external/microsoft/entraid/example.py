# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.entraid.entraid import EntraIDDataSource, EntraIDResponse


async def main():
    tenant_id = os.getenv("ENTRAID_CLIENT_TENANT_ID")
    client_id = os.getenv("ENTRAID_CLIENT_ID")
    client_secret = os.getenv("ENTRAID_CLIENT_SECRET")
    if not tenant_id or not client_id or not client_secret:
        raise Exception("ENTRAID_CLIENT_TENANT_ID, ENTRAID_CLIENT_ID, and ENTRAID_CLIENT_SECRET must be set")

    # Build a client with app-only (client credentials) auth
    client: MSGraphClient = MSGraphClient.build_with_config(
        MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id),
        mode=GraphMode.APP)
    print(client)
    print("****************************")

    entra_id_ds: EntraIDDataSource = EntraIDDataSource(client)
    print("entra_id_ds:", entra_id_ds)
    print("****************************")

    # List domains
    print("Listing domains...")
    response: EntraIDResponse = await entra_id_ds.domains_list()
    print("Success:", response.success)
    print("Data:", response.data)
    print("Error:", response.error)
    print("****************************")

    # List service principals
    print("Listing service principals...")
    response = await entra_id_ds.list_service_principals()
    print("Success:", response.success)
    print("Data:", response.data)
    print("Error:", response.error)
    print("****************************")

    # List applications
    print("Listing applications...")
    response = await entra_id_ds.list_applications()
    print("Success:", response.success)
    print("Data:", response.data)
    print("Error:", response.error)
    print("****************************")

    # List directory roles
    print("Listing directory roles...")
    response = await entra_id_ds.list_directory_roles()
    print("Success:", response.success)
    print("Data:", response.data)
    print("Error:", response.error)
    print("****************************")

    # List conditional access policies
    print("Listing conditional access policies...")
    response = await entra_id_ds.list_conditional_access_policies()
    print("Success:", response.success)
    print("Data:", response.data)
    print("Error:", response.error)
    print("****************************")

    # List subscriptions
    print("Listing subscriptions...")
    response = await entra_id_ds.subscriptions_list()
    print("Success:", response.success)
    print("Data:", response.data)
    print("Error:", response.error)
    print("****************************")


if __name__ == "__main__":
    asyncio.run(main())
