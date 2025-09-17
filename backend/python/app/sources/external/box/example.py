from app.sources.external.box.box import BoxDataSourceWithToken


def main() -> None:
    # Replace this with your real access token
    access_token = "YOUR_ACCESS_TOKEN"

    data_source = BoxDataSourceWithToken(access_token=access_token)

    try:
        # Current User Info
        print("Getting current user info from Box:")
        user_info = data_source.get_user_info()
        print(f"User: {user_info['name']} ({user_info['login']})")

        # List Items in Root Folder
        print("\nListing items in root folder:")
        items = data_source.list_folder_items(folder_id="0")
        for item in items:
            print(f"- {item['type']}: {item['name']} (id={item['id']})")

        # Create Folder
        print("\nCreating a new folder:")
        folder = data_source.create_folder(name="TestFolder2", parent_id="0")
        print(f"Created folder: {folder['name']} (id={folder['id']})")
        folder_id = folder["id"]

        # Upload File
        print("\nUploading a file:")
        file_path = "hello.txt"
        with open(file_path, "w") as f:
            f.write("Hello, Box!")

        uploaded_file = data_source.upload_file(folder_id=folder_id, file_path=file_path)
        print(f"Uploaded file: {uploaded_file['name']} (id={uploaded_file['id']})")
        file_id = uploaded_file["id"]

        # Download File
        print("\nDownloading file:")
        data_source.download_file(file_id=file_id, destination_path="downloaded_hello.txt")
        print("File downloaded successfully as downloaded_hello.txt")

        # List File Tasks (empty by default)
        print("\nListing file tasks:")
        tasks = data_source.list_file_tasks(file_id=file_id)
        print(tasks)

        # Delete File
        print("\nDeleting file:")
        data_source.delete_file(file_id=file_id)
        print("File deleted")

        # Delete Folder
        print("\nDeleting folder:")
        data_source.delete_folder(folder_id=folder_id)
        print("Folder deleted")

    except Exception as e:
        print("Error:", e)


if __name__ == "__main__":
    main()
