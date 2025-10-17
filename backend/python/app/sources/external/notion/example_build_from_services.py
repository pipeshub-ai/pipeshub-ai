# ruff: noqa
"""
Simple Notion API search example.
No pagination, no complexity - just search and print results.
"""
import asyncio
import os

from app.sources.external.notion.notion import NotionClient, NotionDataSource
from app.sources.client.notion.notion import NotionResponse, NotionTokenConfig
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

    # Build Notion client using configuration service (await the async method)
    try:
        notion_client = await NotionClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Notion client created successfully: {notion_client}")
    except Exception as e:
        logger.error(f"Failed to create Notion client: {e}")
        print(f"❌ Error creating Notion client: {e}")
        return
    
    # Create data source and use it
    notion_data_source = NotionDataSource(notion_client)
    
    search_body = {
        "query": "project",
        "filter": {
            "value": "page",
            "property": "object"
        }
    }
    # Test getting pages (this will require a valid page ID)
    try:

        print("Searching for 'project'...")
        response: NotionResponse = await notion_data_source.search(request_body=search_body)
        print("response-----------", response.success)
        print("response-----------", response.data.json())
        print("response-----------", response.error)
        print("response-----------", response.message)

        response: NotionResponse = await notion_data_source.retrieve_page(page_id="26d6a62fbd3480f19afdfd747295f665")
        print("response-----------", response.success)
        print("response-----------", response.data.json())
        print("response-----------", response.error)
        print("response-----------", response.message)
        # Note: This is just an example - you'll need a real page ID
        test_page_id = "26d6a62fbd3480f19afdfd747295f665"
        response = await notion_data_source.retrieve_page(page_id=test_page_id)
        print(f"✅ Get page response: {response}")
    except Exception as e:
        print(f"❌ Error getting page (expected with test ID): {e}")

    finally:
        # Properly close the client session
        await notion_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())