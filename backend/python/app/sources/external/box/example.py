import asyncio

from app.sources.client.box.box import BoxClient, BoxAccessTokenConfig
from app.sources.external.box.box import BoxDataSource


async def main():
    access_token = "tPxO0saHjEm7io4wvKhpnm7e2XfapKkm"  # Replace with real access token
    base_url = "https://api.box.com/2.0"

    if not access_token:
        raise Exception("BOX_ACCESS_TOKEN is not set in environment variables.")

    config = BoxAccessTokenConfig(base_url=base_url, access_token=access_token)
    client = BoxClient.build_with_config(config)
    data_source = BoxDataSource(client)

    try:
        # Current User Info
        print("Getting current user info from Box:")
        try:
            user_info = await data_source.get_user_info()
            print(BoxDataSource.extract_body(user_info))
        except Exception as e:
            print("Error getting user info:", e)

        # List Items in Root Folder
        print("\nListing items in root folder:")
        try:
            items = await data_source.list_folder_items(folder_id="0")
            print(BoxDataSource.extract_body(items))
        except Exception as e:
            print("Error listing folder items:", e)

        # Create Folder
        print("\nCreating a new folder:")
        try:
            folder_resp = await data_source.create_folder(name="TestFolder2", parent_id="0")
            folder_data = BoxDataSource.extract_body(folder_resp)
            print(folder_data)
            folder_id = folder_data.get("id") if isinstance(folder_data, dict) else "0"
        except Exception as e:
            print("Error creating folder:", e)
            folder_id = "0"

        # Upload File
        print("\nUploading a file:")
        file_content = b"Hello, Box!"
        try:
            print(folder_id)
            upload_resp = await data_source.upload_file(folder_id=folder_id, file_name="hello.txt", file_content=file_content)
            upload_data = BoxDataSource.extract_body(upload_resp)
            print(upload_data)
            file_id = None
            if isinstance(upload_data, dict) and "entries" in upload_data:
                file_id = upload_data["entries"][0]["id"]
        except Exception as e:
            print("Error uploading file:", e)
            file_id = None

        # Download File
        if file_id:
            print("\nDownloading file:")
            try:
                download_resp = await data_source.download_file(file_id=file_id)
                print("Download successful, length:", len(download_resp.bytes()))
            except Exception as e:
                print("Error downloading file:", e)

        # Update File Metadata
        if file_id:
            print("\nUpdating file metadata:")
            try:
                update_resp = await data_source.update_file_metadata(file_id=file_id, updates={"description": "Test file"})
                print(BoxDataSource.extract_body(update_resp))
            except Exception as e:
                print("Error updating file metadata:", e)

        # Get Shared Link
        if file_id:
            print("\nGetting shared link:")
            try:
                link_resp = await data_source.get_shared_link(file_id=file_id)
                print(BoxDataSource.extract_body(link_resp))
            except Exception as e:
                print("Error getting shared link:", e)

        # Collaborate on Folder
        if folder_id:
            print("\nCreating collaboration on folder:")
            try:
                collab_resp = await data_source.create_collaboration(folder_id=folder_id, accessible_by={"login": "user@example.com"}, role="viewer")
                print(BoxDataSource.extract_body(collab_resp))
            except Exception as e:
                print("Error creating collaboration:", e)

        # Get File Metadata
        if file_id:
            print("\nGetting file metadata:")
            try:
                metadata_resp = await data_source.get_file_metadata(file_id=file_id)
                print(BoxDataSource.extract_body(metadata_resp))
            except Exception as e:
                print("Error getting file metadata:", e)

        # Delete File
        if file_id:
            print("\nDeleting file:")
            try:
                delete_resp = await data_source.delete_file(file_id=file_id)
                print(BoxDataSource.extract_body(delete_resp))
            except Exception as e:
                print("Error deleting file:", e)

        # Delete Folder
        if folder_id and folder_id != "0":
            print("\nDeleting folder:")
            try:
                delete_folder_resp = await data_source.delete_folder(folder_id=folder_id)
                print(BoxDataSource.extract_body(delete_folder_resp))
            except Exception as e:
                print("Error deleting folder:", e)
    finally:
        # Ensure aiohttp session is closed
        await client.get_client().close()


if __name__ == "__main__":
    asyncio.run(main())
