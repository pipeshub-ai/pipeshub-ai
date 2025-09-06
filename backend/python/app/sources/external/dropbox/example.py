import asyncio
"""
Example usage for DropboxDataSource

This script demonstrates how to use the DropboxDataSource with DropboxClient.
Update the access token and parameters as needed for your Dropbox account.
"""

from app.sources.client.dropbox.dropbox import DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox import DropboxDataSource

# Replace with your actual Dropbox API access token
ACCESS_TOKEN = "DROPBOX_TOKEN"

async def main():
    # Initialize Dropbox client
    config = DropboxTokenConfig(access_token=ACCESS_TOKEN)
    client = DropboxClient.build_with_config(config)
    # Initialize data source
    data_source = DropboxDataSource(client)

    # Example: List files in the root folder
    print("Listing files in root folder:")
    files = await data_source.list_folder(path="")
    # import pdb; pdb.set_trace()
    print(files)
    for entry in files.get("entries", []):
        print(f"- {entry.get('name')}")

    # Example: Get metadata for a file or folder
    if files.get("entries"):
        first_entry = files["entries"][0]
        metadata = await data_source.get_metadata(path=first_entry["path_display"])
        print("\nMetadata for first entry:")
        print(metadata)

if __name__ == "__main__":
    asyncio.run(main())
