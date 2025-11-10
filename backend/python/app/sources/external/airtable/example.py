# ruff: noqa
import asyncio
import os

from app.sources.client.airtable.airtable import AirtableTokenConfig, AirtableClient
from app.sources.external.airtable.airtable import AirtableDataSource

ACCESS_TOKEN = os.getenv("AIRTABLE_TOKEN")


async def main() -> None:
    if not ACCESS_TOKEN:
        raise Exception("AIRTABLE_TOKEN is not set")
    config = AirtableTokenConfig(token=ACCESS_TOKEN)
    client = AirtableClient.build_with_config(config)
    data_source = AirtableDataSource(client)

    # List files in root
    print("getting current user:")
    workspace = await data_source.get_current_user()
    print(workspace.success)
    print(workspace.data)  # type: ignore


if __name__ == "__main__":
    asyncio.run(main())
