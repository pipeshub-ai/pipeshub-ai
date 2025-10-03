# ruff: noqa
import asyncio
import os

from app.sources.client.freshservice.freshservice import (
    FreshServiceClient,
    FreshServiceTokenConfig,
)
from app.sources.external.freshservice.freshservice import FreshServiceDataSource


async def main() -> None:
    domain = os.getenv("FRESHSERVICE_DOMAIN")
    api_key = os.getenv("FRESHSERVICE_API_KEY")
    if not domain or not api_key:
        raise RuntimeError("Please set FRESHSERVICE_DOMAIN and FRESHSERVICE_API_KEY env vars")

    client = FreshServiceClient.build_with_config(
        FreshServiceTokenConfig(domain=domain, api_key=api_key)
    )
    ds = FreshServiceDataSource(client)

    # List tickets
    resp = await ds.list_tickets(per_page=5)
    print("List tickets status:", resp.status)
    print("List tickets body:", resp.json() if resp.is_json else resp.text())

    # Create a sample ticket (adjust fields for your account)
    create_resp = await ds.create_ticket(
        email="example@example.com",
        subject="Test ticket from API",
        description="Created via API",
        priority=1,
        status=2,
    )
    print("Create ticket status:", create_resp.status)
    print("Create ticket body:", create_resp.json() if create_resp.is_json else create_resp.text())


if __name__ == "__main__":
    asyncio.run(main())
