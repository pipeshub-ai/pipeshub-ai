# ruff: noqa
import asyncio
import os

from app.sources.client.box.box import BoxClient, BoxTokenConfig
from app.sources.external.box.box import BoxDataSource

ACCESS_TOKEN = os.getenv("BOX_TOKEN")

async def main() -> None:
    config = BoxTokenConfig(token=ACCESS_TOKEN)
    client = await BoxClient.build_with_config(config)
    data_source = BoxDataSource(client)

    # List current user
    print("Listing current user:")
    users = await data_source.get_current_user()
    print(users)

if __name__ == "__main__":
    asyncio.run(main())
