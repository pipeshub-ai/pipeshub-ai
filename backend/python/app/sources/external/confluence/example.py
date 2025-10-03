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

if __name__ == "__main__":
    asyncio.run(main())


# # ruff: noqa
# import asyncio
# import os

# from app.sources.client.confluence.confluence import (
#         ConfluenceClient,
#         ConfluenceTokenConfig,
# )
# from app.sources.client.http.http_client import HTTPClient
# from app.sources.client.http.http_request import HTTPRequest
# from app.sources.client.http.http_response import HTTPResponse
# from app.sources.external.common.atlassian import AtlassianCloudResource
# from app.sources.external.confluence.confluence import ConfluenceDataSource


# async def _get_accessible_resources() -> list[AtlassianCloudResource]:
#         """Get list of Atlassian sites (Confluence/Jira instances) accessible to the user
#         Args:
#             None
#         Returns:
#             List of accessible Atlassian Cloud resources
#         """
#         RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
#         http_client = HTTPClient(os.getenv("CONFLUENCE_TOKEN") or "", "Bearer")
#         request = HTTPRequest(url=RESOURCE_URL, method="GET", headers={"Content-Type": "application/json"})
#         try:
#             response: HTTPResponse = await http_client.execute(request)
#             print("response", response.json())
#         except Exception as e:
#             print("error", e)
#             raise e
#         return [
#             AtlassianCloudResource(
#                 id=resource["id"], #type: ignore[valid field]
#                 name=resource.get("name", ""), #type: ignore[valid field]
#                 url=resource["url"], #type: ignore[valid field]
#                 scopes=resource.get("scopes", []), #type: ignore[valid field]
#                 avatar_url=resource.get("avatarUrl"), #type: ignore[valid field]
#             )
#             for resource in response.json()
#         ]
# def main():
#     token = os.getenv("CONFLUENCE_TOKEN")
#     if not token:
#         raise Exception("CONFLUENCE_TOKEN is not set")
#     accessible_resources = asyncio.run(_get_accessible_resources())
#     if not accessible_resources:
#         print("No accessible resources found.")
#         raise Exception("No accessible resources found.")
#     print(accessible_resources)
#     cloud_id = accessible_resources[0].id


#     confluence_client : ConfluenceClient = ConfluenceClient.build_with_config(
#         ConfluenceTokenConfig(
#             base_url="https://api.atlassian.com/ex/confluence/" + cloud_id + "/wiki/api/v2/",
#             token=token,
#         ),
#     )
#     print(confluence_client)
#     confluence_data_source = ConfluenceDataSource(confluence_client)
#     response: HTTPResponse = asyncio.run(confluence_data_source.get_spaces())
#     print(response.json())
#     print(response.status)
#     print(response.headers)
#     print(response.text)



#     #print(confluence_client.get_client().get_spaces())

# if __name__ == "__main__":
#     main()
