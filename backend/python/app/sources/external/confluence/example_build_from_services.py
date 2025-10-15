# ruff: noqa
import asyncio
import os

from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.confluence.confluence import ConfluenceClient, ConfluenceTokenConfig
from app.sources.external.common.atlassian import AtlassianCloudResource
from app.sources.external.confluence.confluence import ConfluenceDataSource
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main():
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build Confluence client using configuration service (await the async method)
    try:
        confluence_client = await ConfluenceClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Confluence client created successfully: {confluence_client}")
    except Exception as e:
        logger.error(f"Failed to create Confluence client: {e}")
        print(f"❌ Error creating Confluence client: {e}")
        return
    
    # Create data source and use it
    confluence_data_source = ConfluenceDataSource(confluence_client)
    
    # Get all spaces
    response: HTTPResponse = await confluence_data_source.get_pages()
    print(f"Response status: {response.status}")
    print(f"Response headers: {response.headers}")

    
    if response.status == 200:
        spaces = response.json()
        print(f"Found {len(spaces)} spaces:")
        for space in spaces[:5]:  # Show first 5 spaces
            print(f"  - {space.get('name', 'Unknown')} ({space.get('key', 'No key')})")
    else:
        print(f"Error response: {response.text}")

    await confluence_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())
