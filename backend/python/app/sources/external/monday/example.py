import asyncio

from app.sources.client.monday.monday import MondayClient
from app.sources.external.monday.monday import MondayDataSource


async def main() -> None:
    client = MondayClient()
    datasource = MondayDataSource(client)

    response = await datasource.get_boards()
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
