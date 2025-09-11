# ruff: noqa
import asyncio
import os

from app.sources.client.dropbox.dropbox_ import DropboxAppKeySecretConfig, DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox_ import DropboxDataSource

ACCESS_TOKEN = os.getenv("DROPBOX_TOKEN_TEAM")

async def main() -> None:
    config = DropboxTokenConfig(token=ACCESS_TOKEN)
    client = await DropboxClient.build_with_config(config, is_team=True)
    data_source = DropboxDataSource(client)

    #list all team members
    print("\nListing team members:")
    team_members = await data_source.team_members_list()
    print(team_members.to_dict())

    #List all  groups and memebrer
    print("\nListing groups:")
    groups = await data_source.team_groups_list()
    print(groups.to_dict())
    

if __name__ == "__main__":
    asyncio.run(main())
