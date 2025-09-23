# ruff: noqa
"""
Simple Azure Blob API search example.
No pagination, no complexity - just search and print results.
"""
import asyncio
import os

from app.sources.client.azure.azure_blob import AzureBlobAccountKeyConfig, AzureBlobClient, AzureBlobResponse
from app.sources.external.azure.azure_blob import AzureBlobDataSource

async def main():
    # Azure Blob credentials
    ACCOUNT_NAME = os.getenv("AZURE_BLOB_ACCOUNT_NAME")
    ACCOUNT_KEY = os.getenv("AZURE_BLOB_ACCOUNT_KEY")
    BUCKET = os.getenv("AZURE_BLOB_CONTAINER_NAME")
    # Create client
    config = AzureBlobAccountKeyConfig(accountName=ACCOUNT_NAME, accountKey=ACCOUNT_KEY, containerName=BUCKET)

    print("config", config)
    client = AzureBlobClient.build_with_account_key_config(config)
    print("client", client)
    azure_blob_data_source = AzureBlobDataSource(client)
    print("azure_blob_data_source", azure_blob_data_source)

    # print("Running list containers...")
    # response: AzureBlobResponse = await azure_blob_data_source.list_containers()
    # print("response", response)

    print("Running account information...")
    response: AzureBlobResponse = await azure_blob_data_source.get_account_information()
    print("response", response)


if __name__ == "__main__":
    asyncio.run(main())