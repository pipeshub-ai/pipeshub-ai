# ruff: noqa
import asyncio
import os

from app.sources.client.dropbox.dropbox_ import DropboxAppKeySecretConfig, DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
from app.sources.external.dropbox.pretty_print import to_pretty_json

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

ACCESS_TOKEN = os.getenv("DROPBOX_TEAM_TOKEN")
print(ACCESS_TOKEN)
async def main() -> None:
    config = DropboxTokenConfig(token=ACCESS_TOKEN)
    client = await DropboxClient.build_with_config(config, is_team=True)
    data_source = DropboxDataSource(client)

    # List current user
    # print("\nListing current user:")
    # current_user = await data_source.users_get_current_account()
    # print(current_user.data.name.display_name)
    
    # #List shared folders
    print("\nListing shared folders:")
    shared_folders = await data_source.sharing_list_folders()
    print(to_pretty_json(shared_folders))

    # # List files in root
    print("\nListing root folder:")
    files = await data_source.files_list_folder(path="")
    print(files)
    print(to_pretty_json(files))

    # List files in team folder
    print("\nListing team folder:")
    team_files = await data_source.files_list_folder(path="", team_folder_id="13131350499", recursive=True)
    print(to_pretty_json(team_files))

    # List groups
    print("\nListing groups:")
    groups = await data_source.team_groups_list()
    print(to_pretty_json(groups))

    # List group members
    print("\nListing group members:")
    group_members = await data_source.team_groups_members_list(group="g:a7389e73eef2b44f0000000000000003")
    print((group_members))

    # Upload a test file
    # print("\nUploading test.txt...")
    # upload_resp = await data_source.files_upload(b"Hello from API integration!", "/test.txt")
    # print(upload_resp)

    # # Download the file
    # print("\nDownloading test.txt...")
    # download_resp = await data_source.files_download("/test.txt")
    # print(f"Downloaded bytes: {download_resp.data[0].size}")
    # print(f"Download response: {download_resp}")

    # # Get metadata
    # print("\nGetting metadata for test.txt...")
    # metadata = await data_source.files_get_metadata(path="/test.txt")
    # print(metadata)

    # # Move the file
    # print("\nMoving test.txt to /renamed_test.txt...")
    # move_resp = await data_source.files_move("/test.txt", "/renamed_test.txt")
    # print(move_resp)

    # # Copy the file
    # print("\nCopying renamed_test.txt to /copy_test.txt...")
    # copy_resp = await data_source.files_copy("/renamed_test.txt", "/copy_test.txt")
    # print(copy_resp)

    # # Search for file
    # print("\nSearching for 'test'...")
    # search_resp = await data_source.files_search_v2("test")
    # print(search_resp)

    # # Delete files
    # print("\nDeleting renamed_test.txt...")
    # del1 = await data_source.files_delete("/renamed_test.txt")
    # print(del1)

    # print("\nDeleting copy_test.txt...")
    # del2 = await data_source.files_delete("/copy_test.txt")
    # print(del2)

    # # Create a folder
    # print("\nCreating folder /MyNewFolder3...")
    # folder_resp = await data_source.files_create_folder("/MyNewFolder3")
    # print(folder_resp)





if __name__ == "__main__":
    asyncio.run(main())
