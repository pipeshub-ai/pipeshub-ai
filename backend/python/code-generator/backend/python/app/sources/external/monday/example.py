import asyncio
import os

from app.sources.client.monday.monday import MondayClient, MondayConfig
from app.sources.external.monday.monday import MondayDataSource


async def main() -> None:
    config = MondayConfig(
        base_url=os.environ["MONDAY_BASE_URL"],
        token=os.environ["MONDAY_TOKEN"],
    )

    client = MondayClient.build_with_config(config)
    datasource = MondayDataSource(client)

    response = await datasource.get_boards()
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
