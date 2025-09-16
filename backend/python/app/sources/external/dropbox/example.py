# ruff: noqa
import asyncio
import os

from app.sources.client.dropbox.dropbox_ import DropboxAppKeySecretConfig, DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox_ import DropboxDataSource

ACCESS_TOKEN = os.getenv("DROPBOX_TOKEN")

async def main() -> None:
    config = DropboxTokenConfig(token=ACCESS_TOKEN)
    client = await DropboxClient.build_with_config(config, is_team=False)
    data_source = DropboxDataSource(client)

    # List files in root
    print("Listing root folder:")
    files = await data_source.files_list_folder(path="")
    print(files)

    # Upload a test file
    # print("\nUploading test.txt...")
    # upload_resp = await data_source.files_upload("/test.txt", b"Hello from API integration!")
    # print(upload_resp)

    # # Download the file
    # print("\nDownloading test.txt...")
    # download_resp = await data_source.files_download("/IMG_1677.jpg")
    # print(f"Downloaded bytes: {len(download_resp['data'])}")

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
    # search_resp = await data_source.files_search("test")
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
