# ruff: noqa
"""
Simple Notion API search example.
No pagination, no complexity - just search and print results.
"""
import asyncio
import os

from app.sources.external.notion.notion import NotionClient, NotionDataSource
from app.sources.client.notion.notion import NotionResponse, NotionTokenConfig

async def main():
    # Replace with your Notion integration token
    TOKEN = os.getenv("NOTION_TOKEN")
    print("TOKEN", TOKEN)
    # Create client
    config = NotionTokenConfig(token=TOKEN)
    print("config", config)
    client = NotionClient.build_with_config(config)
    print("client", client)
    notion = NotionDataSource(client)
    print("notion", notion)

    # Search for pages/databases containing "project"
    search_body = {
        "query": "project",
        "filter": {
            "value": "page",
            "property": "object"
        }
    }
    
    print("Searching for 'project'...")
    response: NotionResponse = await notion.search(request_body=search_body)
    print("response-----------", response.success)
    print("response-----------", response.data.json())
    print("response-----------", response.error)
    print("response-----------", response.message)

    response: NotionResponse = await notion.retrieve_page(page_id="26d6a62fbd3480f19afdfd747295f665")
    print("response-----------", response.success)
    print("response-----------", response.data.json())
    print("response-----------", response.error)
    print("response-----------", response.message)


if __name__ == "__main__":
    asyncio.run(main())