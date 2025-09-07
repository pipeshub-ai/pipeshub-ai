
# ruff: noqa
import asyncio
import os

from app.sources.client.box.box import BoxClient, BoxAccessTokenConfig
from app.sources.external.box.box import BoxDataSource

async def main():
    access_token = os.getenv("BOX_ACCESS_TOKEN")
    base_url = os.getenv("BOX_BASE_URL", "https://api.box.com/2.0")
    if not access_token:
        raise Exception("BOX_ACCESS_TOKEN is not set in environment variables.")

    config = BoxAccessTokenConfig(base_url=base_url, access_token=access_token)
    client = BoxClient.build_with_config(config)
    data_source = BoxDataSource(client)

    print("Getting current user info from Box:")
    try:
        user_info = await data_source.get_user_info()
        print(user_info)
    except Exception as e:
        print("Error getting user info:", e)

    print("\nListing items in root folder:")
    try:
        items = await data_source.list_folder_items(folder_id="0")
        print(items)
    except Exception as e:
        print("Error listing folder items:", e)


    # Example: Upload a file
    file_content = b"Hello, Box!"  # Change content as needed
    try:
        upload_resp = await data_source.upload_file(folder_id="0", file_name="hello.txt", file_content=file_content)
        print("Upload Response:", upload_resp)
    except Exception as e:
        print("Error uploading file:", e)

    # Example: Download a file
    file_id = "YOUR_FILE_ID"  # Replace with a real file ID
    try:
        download_resp = await data_source.download_file(file_id=file_id)
        print("Download Response:", download_resp)
    except Exception as e:
        print("Error downloading file:", e)

    # Example: Delete a file
    try:
        delete_resp = await data_source.delete_file(file_id=file_id)
        print("Delete Response:", delete_resp)
    except Exception as e:
        print("Error deleting file:", e)

if __name__ == "__main__":
    asyncio.run(main())
